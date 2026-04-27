# Price Comparison Agent

Production-grade, **category-agnostic** price-comparison agent for
Solace Agent Mesh (SAM). Built for German B2B procurement as the
primary use case; consumer queries (Mode, Bücher, Lebensmittel,
Elektronik, Spielzeug, Sport, Möbel) are handled with equal depth.

The agent classifies every query into one of 13 product categories,
runs a category-aware multi-source discovery (SearXNG + optional paid
backends), fetches detail pages with a Playwright stealth browser,
and returns structured offers with composite confidence, outlier
flags, and per-category insights.

## Highlights

- **13 category profiles** with per-category domain overlays,
  manufacturer-deprioritisation, query-expansion templates, price
  bands, and title-gate anti-lexicons
- **Cascaded classifier** (heuristic → optional LLM fallback) routes
  every query to the right profile
- **Multi-source discovery**: SearXNG (primary, always on) plus
  optional SerpAPI / Brave / Serper / Apify; each is inert without
  its API key
- **Playwright stealth fetch** with site-specific extractors for
  Idealo, Geizhals, Amazon, Otto, MediaMarkt, Saturn, Conrad,
  Reichelt, Voelkner, Zalando, AboutYou, Nike, Adidas, ASOS, H&M,
  Snipes, Foot Locker, plus generic JSON-LD / microdata / regex
  fallbacks
- **Variant detectors** block wrong-SKU traps: fashion size + color,
  wine vintage, ISBN, automotive OEM code, and manufacturer
  part-number (the v1.0 quality push closed the most common B2B
  failure mode)
- **EAN cross-match** lifts confidence to `exact` when a GTIN is
  visible on the detail page
- **LLM result-validator** veto pass flags accessory / bundle /
  wrong-variant offers
- **Quantity-aware bulk pricing**: a request for 100 pieces drops
  to the matching `ab 50` Staffelpreis automatically when the offer
  exposes tier_pricing
- **Trust-anchored outlier detection** (ratio + MAD + per-category
  price band) keeps anomalies visible but never recommended
- **Honest fallbacks**: when zero offers are found, the tool emits a
  `next_actions` list with category-appropriate vendor URLs (Sonepar
  / Rexel for industrial, Amazon / Otto-Office / Viking for office,
  Zalando / AboutYou for fashion, Sigma-Aldrich / Carl-Roth for
  chemistry, Thalia / Hugendubel for books)
- **Persistent learning**: a SQLite domain-stats table promotes
  consistently productive shops into the active scoring overlay;
  table is snapshot to S3 on every successful search and restored
  on pod startup

## Architecture

```text
+------------------------------------------------------------------+
|  sam-solace-lab-agents                                           |
|                                                                  |
|  +----------------------------+                                  |
|  | sam-price-comparison-agent |                                  |
|  | (SAM agent + MCP stdio)    |                                  |
|  |                            |                                  |
|  | tools (3 MCP):             |                                  |
|  |   search_product_prices    |                                  |
|  |   batch_search_prices      |                                  |
|  |   export_comparison_report |                                  |
|  | + builtin: artifact_mgmt   |                                  |
|  +-------+--------------------+                                  |
|          |                                                       |
|          | Playwright (stealth)                                  |
|          v                                                       |
|     +------------------------------------------------+           |
|     | Idealo, Geizhals, Sonepar, Rexel, Conrad, RS,  |           |
|     | Reichelt, Amazon, Otto, MediaMarkt, Zalando,   |           |
|     | Nike, Sigma-Aldrich, Thalia, ... (107 domains) |           |
|     +------------------------------------------------+           |
+------------------------------------------------------------------+
                       |                          |
                       v                          v
            +----------------------+   +-----------------------+
            | sam-solace-lab       |   | sam-solace-lab-shared |
            | Orchestrator + Gate  |   | SearXNG meta-search   |
            +----------------------+   +-----------------------+
```

## Deployment Model

Custom Docker image with Playwright Chromium. Deployed via three
manifests (Secret, ConfigMap, Deployment) under `deploy/`.

| Property      | Value                                                          |
|---------------|----------------------------------------------------------------|
| Namespace     | `sam-solace-lab-agents`                                        |
| Deployment    | `sam-price-comparison-agent`                                   |
| Image         | `registry.solace.lab/sam-price-comparison-agent:1.0.0`         |
| `agent_name`  | `PriceComparisonAgent`                                         |
| Display name  | `Price Comparison Agent`                                       |
| Model         | `openai/claude-sonnet-4-6` (via LiteLLM proxy)                 |

## Tools

| Tool | Use case |
|------|----------|
| `search_product_prices` | Single product (EAN/GTIN, SKU, or free-text). Returns up to 20 offers ranked by composite confidence then total price. |
| `batch_search_prices`   | 2-25 products in ONE call. Optimised for tender Positionslisten. Auto-chunked internally; quantity-aware bulk pricing. |
| `export_comparison_report` | CSV export of a previous result. Outlier markers, match confidence, VAT status, raw + filtered insight columns. |

Hard ceiling: **15 LLM calls per task** (covers 1 batch call +
auto-chunk artifact loads + final report). Per-batch wall-clock cap:
**110 s for the full pipeline** (auto-chunked per item).

## Pipeline

```text
search_product_prices(query)
  0a. EAN/ISBN checksum fast-fail (reject typo barcodes)
  0b. Cascaded classifier -> CategoryProfile
  0c. Locale detection (de/en/fr/es/it)
  0d. Cache lookup (keyed by category + locale)
  1. Discovery: SearXNG (general + shopping in parallel) + optional
     paid backends + aggregator-SERP injection, category-aware
     query variants
  2. URL scoring (domain overlay + learned domains + variant
     penalties + part-number gate + off-locale cap + blacklist)
     -> diverse top-N selection
  3. Optional LLM reranker (default ON)
  4. Playwright detail fetch (per-domain concurrency cap; 8s backoff
     for Idealo / Geizhals; homepage warmup for bot-protected sites)
  5. Layered extraction: site-specific CSS > JSON-LD > microdata
     > generic CSS > regex; aggregator-SERP tile parser as a
     last resort for Idealo / Geizhals search-result pages
  6. EAN cross-match, LLM validator veto, variant-detector penalties
  7. Outlier flag (ratio + MAD + per-category price band)
  8. Composite confidence = match_conf * price_source_weight
  9. Quantity-aware bulk-tier swap (batch only)
 10. Record successful high-confidence domains -> domain_stats
     (SQLite + periodic S3 snapshot)
```

## Key behaviour

| Aspect | Setting |
|---|---|
| Tool-call budget | 15 LLM calls per task |
| Total per-search wall-clock | 110 s |
| Per-detail-page timeout | 18 s |
| Discovery backend (always on) | SearXNG meta-search (Google + Bing + DuckDuckGo + Mojeek + Startpage + Qwant) |
| Optional paid backends | SerpAPI / Brave / Serper / Apify (all inert without key) |
| Detail fetch | Playwright Chromium, 4 parallel, per-domain concurrency cap for aggregators |
| Cache TTL | 1800 s (30 min) -- B2B prices change slowly |
| Batch size | 1-25 items, auto-chunked into groups of 5 |
| Quick search | `fetch_details=false` -- SearXNG snippets only, ~3-5 s per item |
| Deep search (default) | `fetch_details=true` -- full pipeline |
| Net-price detection | `is_b2b_netto: true` or text markers ("Nettoartikel" / "BRUTTOARTIKEL" / "NLAG"); skipped automatically |

## Outlier detection (trust-anchor)

Offers are flagged with `is_outlier: true` when any of three windows
fires:

1. **Ratio window** around the trust-anchor median: below 20 % or
   above 500 % of anchor.
2. **MAD-based** (Median Absolute Deviation): more than 10 × MAD
   from anchor, even within the ratio window. Catches tight
   markets where 20-500 % is too wide.
3. **Per-category price band**: each profile carries an explicit
   `[min, max]` EUR range (e.g. `book_media: [0.50, 500.0]`,
   `industrial_mro: [0.10, 200000.0]`). Anything outside is flagged
   regardless of statistical position.

The anchor is the median of prices from `TRUSTED_PRICE_DOMAINS`
(curated B2B distributors + major aggregators). With no trusted
price present, falls back to the overall median.

## Portal coverage

107 domains across 13 categories. The base scoring tier is curated;
each category profile can promote (never demote) domains via its
`domain_scores` overlay.

| Tier | Examples |
|---|---|
| Aggregators (top) | idealo, geizhals, billiger, guenstiger, preis, preisvergleich |
| DE electrical wholesale | sonepar, rexel, fega, eibmarkt, voltus, elektro4000, elektro-wandelt |
| Industrial electronics | conrad, reichelt, voelkner, rs-online, farnell, digikey, mouser, distrelec, buerklin, rutronik24, tme, elv |
| B2B / MRO | mercateo, industry-electronics, contorion, kaiser-kraft, schaefer-shop, svh24, hoffmann-group, wuerth, haberkorn, expondo, toolineo, berner |
| B2B IT | bechtle, jacob, cyberport, future-x, computeruniverse, alternate |
| Workwear / PPE | engelbert-strauss, mewa, arbeitsschutz-express |
| Consumer retail | amazon, otto, mediamarkt, saturn, notebooksbilliger, kaufland, real, galaxus |
| DIY / building | bauhaus, hornbach, obi, hagebau, toom |
| Sanitary | reuter, megabad, skybad, emero, calmwaters, badshop, sanitino, badshop-web, ksr-shop, msr24, heizungsdiscount24 |
| Sanitary brands (depri.) | grohe, hansgrohe, geberit, villeroy-boch, duravit, keramag-design, laufen, axor, kludi, dornbracht |
| Fashion | zalando, aboutyou, breuninger, peek-cloppenburg, asos, bonprix, hm, mytheresa, jd-sports, footlocker, snipes, engelhorn |
| Books | amazon, thalia, hugendubel, buecher.de |
| Wine / food | gute-weine, feineweinwelt, lacave-conrad, millesima, weinclub, rewe |
| Chemistry | sigmaaldrich, carlroth, vwr, fishersci, th-geyer, merck-chemicals, alfa, tci-europe |
| Marketplaces (lower weight) | ebay, kleinanzeigen |
| Manufacturer domains (deprioritised) | 32 brands: niedax, obo-bettermann, hager, siemens, abb, schneider-electric, phoenixcontact, weidmueller, wago, bosch, makita, festool, hilti, sony, samsung, ... |

## Configuration

All settings come from environment variables. Sensible defaults are
hard-coded; overrides flow through the agent's Secret + ConfigMap.

### Pipeline feature flags

| Flag | Default | Purpose |
|---|---|---|
| `PRICE_ENABLE_CATEGORIES` | `true` | Classify + apply category profile overlays |
| `PRICE_ENABLE_EAN_FASTFAIL` | `true` | Reject invalid GTIN/ISBN at ingress |
| `PRICE_ENABLE_LOCALE_TEMPLATES` | `true` | Per-(category, locale) query expansions |
| `PRICE_ENABLE_ANTILEX_GATE` | `true` | Title-gate anti-lexicon forces mc=low |
| `PRICE_ENABLE_PART_NUMBER_GATE` | `true` | SKU/part-number title gate |
| `PRICE_ENABLE_CLASSIFIER_LLM` | `true` | Stage-3 LLM classifier fallback |
| `PRICE_ENABLE_LLM_RERANKER` | `true` | LLM cross-encoder reranks top candidates |
| `PRICE_ENABLE_DOMAIN_DISCOVERY` | `true` | Dynamic domain-stats learning |
| `PRICE_DOMAIN_STATS_S3_SNAPSHOT` | `true` | Persist learned-domain table to S3 |
| `PRICE_ENABLE_AGENT_DELEGATION` | `false` | Phase F: delegate EAN lookup to EANSearchAgent (opt-in) |

### Pipeline parameters

| Variable | Default | Purpose |
|---|---|---|
| `PRICE_TOTAL_TIMEOUT_SECONDS` | `110` | Per-search wall-clock cap |
| `PRICE_DETAIL_TIMEOUT_SECONDS` | `18` | Per-URL detail-fetch timeout |
| `PRICE_MAX_DETAIL_URLS` | `10` | Top-N URLs sent to Playwright |
| `PRICE_MAX_DETAIL_URLS_PER_DOMAIN` | `1` | Diversity at fetch time |
| `PRICE_MAX_OFFERS_PER_DOMAIN` | `4` | Diversity in final output |
| `PRICE_CONCURRENT_FETCHES` | `4` | Parallel Playwright contexts |
| `PRICE_CACHE_TTL_SECONDS` | `1800` | Result cache TTL |

### LLM validator

| Variable | Default | Purpose |
|---|---|---|
| `PRICE_LLM_VALIDATOR_ENABLED` | `true` | Final veto pass on extracted offers |
| `PRICE_LLM_VALIDATOR_TIMEOUT_SECONDS` | `8.0` | Per-validator-call timeout |
| `PRICE_LLM_VALIDATOR_MAX_OFFERS` | `8` | Max offers reviewed per call |

### Optional paid backends

All four are inert when their key is empty.

| Variable | Backend |
|---|---|
| `PRICE_SERPAPI_KEY` | Google Shopping via SerpAPI (paid, best quality) |
| `PRICE_BRAVE_API_KEY` | Brave Search API (free 2000/mo) |
| `PRICE_SERPER_API_KEY` | Serper.dev (free 2500 one-time) |
| `PRICE_APIFY_TOKEN` | Apify Google Shopping Scraper (paid) |

## File structure

```text
Agents/External Agents/Price Comparison Agent/
|-- deploy/
|   |-- sam-price-comparison-agent-config.yaml
|   |-- sam-price-comparison-agent-secret.yaml.template
|   +-- sam-price-comparison-agent-deployment.yaml
|-- src/
|   +-- price_comparison_mcp/
|       |-- server.py              # MCP JSON-RPC dispatcher
|       |-- browser_manager.py     # Playwright stealth + warmup
|       |-- config.py              # PriceSearchConfig + BrowserConfig
|       |-- errors.py              # Structured error taxonomy
|       |-- response.py            # Response mode builder
|       |-- cache.py               # TTL cache
|       |-- price_extractor.py     # Layered extraction + SERP tile parser
|       |-- searxng_client.py      # SearXNG client
|       |-- serpapi_client.py      # SerpAPI client (optional)
|       |-- brave_client.py        # Brave Search client (optional)
|       |-- serper_client.py       # Serper.dev client (optional)
|       |-- apify_client.py        # Apify client (optional)
|       |-- result_validator.py    # LLM validator pass
|       |-- categories/            # 13 profiles + classifier + variant detectors
|       |-- enrichment/            # OFF + Wikidata + (opt-in) EANSearchAgent
|       |-- discovery/             # Domain stats + LLM reranker
|       |-- locale/                # Detector + per-(cat, locale) templates
|       +-- tools/                 # search_prices / batch_search / export_report
|-- tests/                         # 570 tests, all green
|-- Dockerfile
|-- pyproject.toml
|-- Makefile                       # build / push / release / rollout
|-- CLAUDE.md                      # Developer-facing detail
+-- README.md                      # This file
```

## Build, push, deploy

The Makefile encapsulates the standard cycle. Version is read from
`pyproject.toml`; both `:VERSION` and `:latest` tags are pushed.

```bash
make release VERSION=1.0.0   # build + push + rollout-restart
make rollout                 # restart pod against current :latest
make test                    # run pytest suite (570 tests)
```

Manual equivalent:

```bash
cd "Agents/External Agents/Price Comparison Agent"

# Build (Docker via Rancher Desktop on macOS)
DOCKER_CONFIG=/Users/alexandermartens/.docker \
  /Users/alexandermartens/.rd/bin/docker build \
    -t registry.solace.lab/sam-price-comparison-agent:1.0.0 \
    -t registry.solace.lab/sam-price-comparison-agent:latest .

# Push (needs docker-credential-osxkeychain on PATH)
PATH="/Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin:$PATH" \
  DOCKER_CONFIG=/Users/alexandermartens/.docker \
  /Users/alexandermartens/.rd/bin/docker push \
    registry.solace.lab/sam-price-comparison-agent:1.0.0
/Users/alexandermartens/.rd/bin/docker push \
    registry.solace.lab/sam-price-comparison-agent:latest

# Apply manifests + rollout
kubectl apply -f deploy/sam-price-comparison-agent-secret.yaml
kubectl apply -f deploy/sam-price-comparison-agent-config.yaml
kubectl apply -f deploy/sam-price-comparison-agent-deployment.yaml
kubectl rollout restart deployment/sam-price-comparison-agent \
    -n sam-solace-lab-agents
kubectl rollout status deployment/sam-price-comparison-agent \
    -n sam-solace-lab-agents --timeout=120s
```

## Verification

Port-forward the gateway and send a test request:

```bash
kubectl port-forward svc/agent-mesh 8081:80 -n sam-solace-lab
```

```bash
curl -s -X POST http://localhost:8081/api/v1/message:send \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-pc-001",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "Finde Preise fuer Bosch Professional GBH 2-26 F Bohrhammer"
        }],
        "messageId": "msg-pc-001",
        "metadata": {"agent_name": "PriceComparisonAgent"}
      }
    }
  }'
```

```bash
# Poll the task
curl -s http://localhost:8081/api/v1/tasks/<task_id>
```

**Success criteria:**

- Returns 4-10 offers from multiple merchants
- `match_confidence` is `exact` or `high` for the cheapest offer
- `category` is set (e.g. `tools_hardware`)
- Outliers (if any) are flagged with `outlier_reason`
- Total wall-clock under 110 s

## Activating optional search backends

The pipeline ships with four optional structured-shopping backends.
Each one is inert until its API key is set; they all run in parallel
when active. Activating Brave alone is the recommended way to
sidestep the Idealo/Geizhals bot-detection issue documented below.

| Backend | Free tier | Paid | Activation |
|---|---|---|---|
| **Brave Search API** (recommended) | 2000 calls / month | $5 / 50k | `PRICE_BRAVE_API_KEY` |
| **Serper.dev** | 2500 calls one-time | $50 / 50k | `PRICE_SERPER_API_KEY` |
| **Apify** (dedicated Idealo/Geizhals actors) | $5 credit / month | ~$5 / 1000 results | `PRICE_APIFY_TOKEN` |
| **SerpAPI** (Google Shopping) | --- | $50 / 5k | `PRICE_SERPAPI_KEY` |

### Why this matters

Idealo and Geizhals return HTTP 503/403 to direct browser fetches
even with stealth + per-domain concurrency cap (Phase L+) and
homepage warmup. **Brave's backend already aggregates Idealo and
Geizhals data and returns it through a clean API.** Activating
Brave moves the aggregator scraping out of our pipeline and into a
service that handles bot-detection professionally.

### Activation steps

1. Get a Brave API key: <https://brave.com/search/api/> (free tier
   2000 calls/month, no credit card required).

2. Add it to your local `.env`:

   ```bash
   PRICE_BRAVE_API_KEY="BSA..."
   ```

3. Re-render the agent secret and apply:

   <!-- markdownlint-disable MD013 -->

   ```bash
   make secrets
   kubectl apply -f "Agents/External Agents/Price Comparison Agent/deploy/sam-price-comparison-agent-secret.yaml"
   kubectl rollout restart deployment/sam-price-comparison-agent -n sam-solace-lab-agents
   ```

   <!-- markdownlint-enable MD013 -->

4. Verify the key is visible in the MCP subprocess:

   ```bash
   POD=$(kubectl get pods -n sam-solace-lab-agents -o name | grep price | sed 's|.*/||')
   kubectl logs -n sam-solace-lab-agents "$POD" --tail=200 | grep PRICE_BRAVE_API_KEY
   ```

   The log should show `'PRICE_BRAVE_API_KEY': 'BSA...'` (truncated)
   in the MCPToolset env block.

5. Run a smoke test with an Idealo-typical query and confirm offers
   come back via `source: brave` in the result JSON.

The other three keys can be added the same way; all four work in
parallel without further config.

## Known limitations (v1.0.0)

- **Login-only B2B SKUs** (Sonepar/Rexel internal catalogues): no
  public price obtainable. The tool surfaces `next_actions` with
  vendor URLs and a clear "Kundenlogin erforderlich" hint, but
  cannot fetch the price itself. Phase Q (B2B-portal scraper)
  is on the v1.1 roadmap.
- **Aggregator bot-detection**: Idealo and Geizhals occasionally
  return HTTP 503/403 on direct browser fetches despite homepage
  warmup and per-domain concurrency cap. The pipeline gracefully
  routes around to alternative sources (eBay, BSH-Direct,
  billiger.de, lacave-conrad, …) when this happens.
  **Mitigation:** activate the Brave Search API key (see above) --
  Brave indexes Idealo/Geizhals server-side and returns the data
  through a clean API, sidestepping the scrape entirely.
- **Model-line discrimination without part numbers**: queries like
  "MEPA ellipse Betätigungsplatte" where the discriminator is a
  lowercase model-line word are partially handled by the LLM
  reranker but not as cleanly as part-number-bearing queries.

## Changelog

| Date | Version | Highlights |
|---|---|---|
| 2026-04-27 | **1.0.0 GA** | Final v1.0.0 production release. 570 tests, 84 % validated coverage on the 25-position B2B procurement workload (Testlauf 6 with Brave API key). |
| 2026-04-27 | | Tier-1+2 hardening: otto.de relative-URL fix, aggregator search-URL detection extended (Amazon ?k=, Otto /suche/), prisma.film + 7 spam-host caps, LLM-validator prompt with wrong-category guard, wine price band raised to 5000 EUR, sanitary brand-map (wilo/stiebel-eltron/ideal-standard/mepa/vaillant/viessmann/+9), home-appliance brand-map (bosch mum/kenwood/kitchenaid/severin/krups/+10), word-boundary brand-match (closes Kabelschelle false positive). |
| 2026-04-27 | | Brave Search API integration (PRICE_BRAVE_API_KEY); first-party result_filter=products bug fixed (Brave does not have a products filter -- web search now serves the full 5-variant cascade per query). |
| 2026-04-27 | | Phase K+ + L+: per-category `next_actions` (consumer vs B2B routing), per-domain concurrency cap (idealo/geizhals=1, billiger=2), per-domain retry backoff (idealo/geizhals=8 s) |
| 2026-04-26 | | Phase J + N: fashion-site extractors (Zalando, AboutYou, Snipes, Nike, Adidas, ASOS, H&M, Foot Locker), Idealo/Geizhals SERP tile parser |
| 2026-04-26 | | Phase O + P: off-locale TLD/host cap (drops Asian/RU Q&A pages), domain-stats S3 snapshot persistence |
| 2026-04-26 | | Phase L + M: bot-detection homepage warmup, LLM reranker enabled by default |
| 2026-04-26 | | Phase R: quantity-aware bulk pricing (Staffel match) |
| 2026-04-25 | | Phase I: manufacturer part-number title gate (eliminated Klasse-C false-positive matches) |
| 2026-04-24 | | Phase F: opt-in EANSearchAgent peer delegation via SAM Gateway REST |
| 2026-04-24 | | Phase E: structured `next_actions` for empty-offer responses |
| 2026-04-23 | | Phase B + B+ + C: OpenFoodFacts + Wikidata enrichment, parallel EAN reverse lookup, Mojeek/Startpage/Qwant in shared SearXNG |
| 2026-04-22 | | Phase A+: B2B marketplace overlay based on Testlauf 1+2 evidence |
| 2026-04-21 | | v1.0 alphaN: 13 category profiles, cascaded classifier, variant detectors, locale templates, domain-stats learning, LLM validator |
| 2026-04-10 | 0.x | Complete rewrite as MCP server with SearXNG + Playwright |
| 2026-04-01 | 0.0 | Initial deployment with httpx scrapers |
