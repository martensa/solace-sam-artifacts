# Price Comparison Agent (v1.0.0)

## What this is

Production-grade **category-agnostic** price comparison agent for
Solace Agent Mesh. Handles industrial/MRO, tools, sanitary, office,
electronics, fashion, food+wine, books, automotive, chemistry,
cosmetics, sports, home+garden, and toys with equal depth. Multi-
backend discovery, Playwright stealth detail fetch, structured
offer output with composite confidence + outlier detection.

## Key architecture decisions (v1.0)

### Categories & profiles
- **13 category profiles** in YAML (`categories/_data/`):
  default, industrial_mro, electronics, tools_hardware,
  fashion_apparel, book_media, food_beverage, automotive,
  chemicals_lab, cosmetic_pharma, office_supplies, sports_outdoor,
  home_garden, toys_hobby, **sanitary**. Each profile carries
  `domain_scores`, `manufacturer_domains`, `query_expansions`,
  `rule_c_fillers`, `variant_detectors`, `price_band`,
  `title_gate_antilex`, `preferred_shopping_engines`.
- **Inheritance**: tools_hardware < industrial_mro < default.
  Resolution happens at load time -- runtime code always sees a
  fully-resolved profile.
- **`max()`-based overlay**: categories can only LIFT domain scores,
  never demote. Preserves the hand-tuned base tier.

### Classifier cascade
- **Stage 1 (heuristic, ~0ms)**: barcode-shape hints (ISBN/ISSN
  prefix), structural patterns (CAS, OEM codes), ~120-entry
  brand map, ~25 keyword regexes.
- **Stage 2 (EAN enrichment, deferred to v1.1)**: will resolve
  GTINs to canonical product name via OpenFoodFacts / Wikidata.
- **Stage 3 (LLM, ~400ms on miss)**: only fires when Stage-1
  confidence < 0.5 AND query has >=3 alpha tokens. Uses LiteLLM
  with 3s timeout, JSON-schema enforced, LRU+SQLite cache.

### Multi-source discovery
- **SearXNG** (primary, cluster-internal): general + shopping in
  parallel, per-category preferred engines.
- **SerpAPI / Brave / Serper / Apify** (optional, inert without
  API key).
- **Active-coverage injection** of idealo/geizhals/conrad
  aggregator SERP URLs for thin candidate pools.

### URL scoring
1. Domain base score from `_PRICE_SITE_SCORES` (72 curated portals)
2. Category overlay (`max()`-based)
3. Learned-domain overlay from `domain_stats` (SQLite + decay,
   auto-promotes after 3+ successful high-confidence hits)
4. Manufacturer penalty (default -40, per-category overridable:
   chemicals_lab=0, book_media=60)
5. Product-path bonus / category-path penalty
6. Foreign-qualifier penalty (Rules A/B/C: letter-digit compound,
   word-adjacent compound, alpha variant suffix)
7. Category variant detectors (fashion_size, fashion_color,
   wine_vintage, book_edition, automotive_oem)

### Detail fetch + extraction
- **Playwright stealth** with site-specific > JSON-LD > microdata
  > noise-filtered generic CSS > regex extraction chain.
- Each offer tagged with `price_source` (json_ld/microdata/
  css_site/css_generic/regex) fed into `composite_confidence`.
- **LLM validator**: final gate that vetoes wrong-variant offers.

### Outlier detection
- Trust-anchored (median of TRUSTED_PRICE_DOMAINS prices).
- **Ratio window** (<20% / >500% of anchor) + **MAD window**
  (10x MAD) + **category price-band** (hard bound per profile).

### Locale
- Char-class + stopword voting detector (de/en/fr/es/it).
- Per-(category, locale) query-expansion templates.
- Per-aggregator TLD routing (amazon.de/com/fr/it/es).

### Dynamic domain discovery (v1.0 beta1+)
- SQLite `/app/data/domain_stats.db` (emptyDir in v1.0;
  PV / S3-snapshot in v1.1).
- Writer: after each search, domains with high-confidence
  non-outlier offers get a hit logged.
- Reader: at `_score_url` time, decayed-hits >= 3 promote
  domains into [50, 85] score range.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `search_product_prices` | Default single-item search by EAN / SKU / name |
| `batch_search_prices` | 2-25 items, auto-chunked |
| `export_comparison_report` | CSV export with outlier markers + composite confidence |

## Pipeline

```text
handle_search_prices(query)
  0a. EAN checksum fast-fail (reject invalid barcodes)
  0b. Cascaded classifier -> CategoryProfile
  0c. Locale detection
  0d. Cache lookup (keyed by category+locale)
  1. Discovery: SearXNG + optional paid backends + aggregator
     SERP injection, category-aware query variants
  2. Score + diverse top-N selection (domain overlay + learned
     domains + variant penalties + blacklist)
  3. Optional LLM reranker (opt-in)
  4. Playwright detail fetch (N concurrent, 18s per URL)
  5. LLM validator veto pass
  6. Flag outliers (ratio + MAD + per-category price-band)
  7. Composite confidence = match_conf * price_source_weight
  8. Record successful high-confidence domains -> domain_stats
  9. Return structured JSON with offers, insights, category,
     locale, timing
```

## Feature flags (ENV)

| Flag | Default | Purpose |
|------|---------|---------|
| `PRICE_ENABLE_CATEGORIES` | `true` | Classify + overlay (falls back to v2.3.5 when false) |
| `PRICE_ENABLE_EAN_FASTFAIL` | `true` | Reject invalid GTIN/ISBN at ingress |
| `PRICE_ENABLE_LOCALE_TEMPLATES` | `true` | Category/locale-aware query expansion |
| `PRICE_ENABLE_ANTILEX_GATE` | `true` | Title-gate anti-lexicon forces mc=low |
| `PRICE_ENABLE_PART_NUMBER_GATE` | `true` | Phase I: SKU title-gate forces mc=low when query part-number is missing in title |
| `PRICE_ENABLE_CLASSIFIER_LLM` | `true` | Stage-3 LLM fallback |
| `PRICE_CLASSIFIER_LLM_MAX_LATENCY_MS` | `3000` | Stage-3 timeout |
| `PRICE_CLASSIFIER_LLM_MIN_CONFIDENCE` | `0.5` | Heuristic threshold below which LLM fires |
| `PRICE_ENABLE_LLM_RERANKER` | `false` | Opt-in reranker |
| `PRICE_ENABLE_DOMAIN_DISCOVERY` | `true` | Dynamic domain learning |
| `PRICE_DOMAIN_STATS_DB_PATH` | `/app/data/domain_stats.db` | SQLite location |

All are settable via the agent secret; template documents them.

## Source files

```text
src/price_comparison_mcp/
  server.py               JSON-RPC 2.0 dispatcher
  browser_manager.py      Playwright stealth
  config.py               PriceSearchConfig + BrowserConfig + feature flags
  errors.py               Structured error taxonomy
  response.py             Response mode builder
  cache.py                TTL cache
  price_extractor.py      Layered price extraction with price_source tag
  searxng_client.py       Async SearXNG client
  serpapi_client.py       Async SerpAPI client (optional)
  brave_client.py         Async Brave Search client (optional)
  serper_client.py        Async Serper.dev client (optional)
  apify_client.py         Async Apify client (optional)
  result_validator.py     LLM validator pass
  categories/
    __init__.py
    models.py             CategoryProfile dataclass + merge_profiles()
    registry.py           YAML-backed singleton with inheritance + cycle check
    classifier.py         Stage-1 heuristic (barcode + brand + keyword)
    classifier_llm.py     Stage-3 LLM cascade + LRU cache
    variant_detectors.py  fashion_size / fashion_color / wine_vintage /
                          book_edition / automotive_oem /
                          manufacturer_part_number
    _data/                13 category YAMLs + _default
  enrichment/
    __init__.py
    ean.py                GTIN/ISBN checksum + GS1 prefix lookup
  discovery/
    __init__.py
    domain_stats.py       SQLite learning table + decay + promotion
    rerank.py             LLM cross-encoder reranker (opt-in)
  locale/
    __init__.py
    detector.py           char-class + stopword voting
    templates.py          per-(category, locale) query expansions
  tools/
    search_prices.py      Pipeline orchestrator
    batch_search.py       Batch wrapper (2-25 items, auto-chunked)
    export_report.py      CSV export with outlier + confidence columns

tests/
  test_regression_fixes.py      29 locks-in-behaviour tests (CI gate)
  test_ean_validator.py         70 GTIN/ISBN/prefix tests
  test_category_registry.py     20 load/inherit/merge/cycle
  test_category_classifier.py   50+ heuristic dispatcher tests
  test_classifier_llm.py        10 mocked LLM cascade tests
  test_locale.py                35 detector + templates
  test_profile_overlay.py       11 overlay contract + BC
  test_pipeline_wireup.py       17 alpha3 integration
  test_alpha4_features.py       18 price-bounds + composite + variants
  test_variant_detectors.py     27 detector unit tests
  test_domain_stats.py          14 SQLite + decay
  test_rerank.py                8 reranker
  test_smoke_categories.py      22 end-to-end over 20 representative queries

Total: 340+ tests, all green, gate the docker build.
```

## Running locally

```bash
cd "Agents/External Agents/Price Comparison Agent"
uv venv && uv pip install -e ".[dev]"
playwright install chromium
make test                   # run full pytest suite
python -m price_comparison_mcp.server    # run MCP server on stdio
```

## Build + deploy

```bash
make release VERSION=1.0.0  # build + push :1.0.0 + :latest + rollout
```

Registry cleanup (from repo root):
```bash
./scripts/registry-cleanup.sh sam-price-comparison-agent 1.0.0 latest
```

## Code conventions

- ASCII-only in source (unicode OK in test-fixture display names).
- English for code/comments/tool descriptions; German for user-
  facing output (instruction template).
- Every new feature ships behind an ENV flag (`PRICE_ENABLE_*`).
- Profile additions go in YAML (`_data/`), never in Python code --
  loader handles them automatically.
- New portal? Add to either `_PRICE_SITE_SCORES` (base global) or
  the relevant `categories/_data/*.yaml` (overlay).
- New variant axis? Add a detector to `variant_detectors.py` and
  reference its name from the category YAML.
