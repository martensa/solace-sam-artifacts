# Price Comparison Agent

## What this is

Production-grade price comparison agent for Solace Agent Mesh, built
**B2B-first** (industrial procurement, electrical components, tools,
office supplies). Uses SearXNG meta-search for discovery, Playwright
with stealth mode for detail-page extraction, and optional SerpAPI
for Google Shopping. Returns structured offers with trust-anchored
outlier flags and match-confidence scoring.

## Key architecture decisions

- **MCP over stdio**: JSON-RPC 2.0 with newline-delimited JSON framing
  (same pattern as Web Scraper Agent).
- **SearXNG for discovery**: queries SearXNG JSON API (cluster-internal)
  aggregating Google, Bing, DuckDuckGo. Appends "Preis kaufen" to the
  raw query. Inline prices in snippets are captured opportunistically.
- **Playwright for accuracy**: fetches top-ranked URLs via stealth
  browser with layered extraction (site-specific -> JSON-LD ->
  Microdata -> noise-filtered generic CSS -> regex). Generic and regex
  fallbacks return at most 1 primary price per URL to avoid noise from
  teaser / accessory / shipping fragments.
- **URL ranking with context**: scoring function combines domain score
  (72 curated portals), manufacturer-domain penalty (-40 for 52 known
  brand sites), product-path bonus (+8), category-path penalty (-15),
  query-token-in-URL bonus (+10), and a hard blacklist for archive /
  sold / forum URLs.
- **Domain diversity**: enforced twice -- `max_detail_urls_per_domain`
  limits fetch slots, `max_offers_per_domain` caps the final output.
  Prevents idealo.de from consuming all slots for consumer queries,
  and surfaces more distributors for B2B queries.
- **Trust-anchored outlier detection**: anchor is the median of prices
  from TRUSTED_PRICE_DOMAINS (~70 domains, derived from the scoring
  tiers). Outliers flagged via ratio window (0.2x-5x) AND a secondary
  MAD-based check. Trusted-domain prices ARE still tested (a trusted
  site can list an accessory); they get a softer reason string.
- **Match-confidence**: each Playwright fetch captures the page title
  and compares tokens with the query (high / medium / low).
- **SerpAPI optional**: if PRICE_SERPAPI_KEY is set, also queries
  Google Shopping. Otherwise works without it.
- **B2B quality-first parameters**: 75s total budget, 10 detail URLs,
  18s per page, 4 concurrent fetches, 30 min cache TTL.
- **Programmatic tool budget**: `max_llm_calls_per_task=10` (4 tool
  calls + responses + intro/outro).
- **B2B input filter**: skips price search for Nettoartikel /
  Bruttoartikel / NLAG pricing labels and responds immediately.

## Tools

| Tool | Purpose |
|------|---------|
| `search_product_prices` | Default price search by EAN or product name |
| `batch_search_prices` | Multiple products at once (up to 10) |
| `export_comparison_report` | CSV export with outlier markers + filtered stats |

## Pipeline (from `tools/search_prices.py`)

```text
handle_search_prices(query)
  1. Cache lookup (TTL 30min)
  2. Phase 1: SearXNG + optional SerpAPI (parallel)
  3. Phase 2: dedup + score + blacklist filter + diverse top-N URLs
  4. Phase 3: Playwright (concurrent) -> (offers, page_title)
  5. Phase 4: dedup offers, sort, per-domain cap, flag outliers,
     compute raw + filtered insights
```

## Outlier detection

Defined in `_flag_outliers()`:

- Needs >=4 offers with prices, otherwise all marked non-outlier.
- Anchor: median of trusted-domain prices if any, else overall median.
- Ratio window: flagged if < 20% or > 500% of anchor.
- MAD window: flagged if |price - anchor| > 10 * MAD (with a 10%
  floor on MAD to avoid overflagging in tight markets).
- Trusted-domain offers that drift outside the window get a soft
  reason "stray sub-listing"; non-trusted get "likely accessory /
  quantity unit / wrong product" or "likely bundle / contract".

## Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PRICE_SEARXNG_URL` | (cluster URL) | SearXNG JSON API endpoint |
| `PRICE_SERPAPI_KEY` | (empty) | Optional Google Shopping |
| `PRICE_TOTAL_TIMEOUT_SECONDS` | 75 | Per-search budget |
| `PRICE_MAX_DETAIL_URLS` | 10 | Top-N URLs fetched via Playwright |
| `PRICE_MAX_DETAIL_URLS_PER_DOMAIN` | 1 | Fetch-time diversity cap |
| `PRICE_MAX_OFFERS_PER_DOMAIN` | 4 | Output-time diversity cap |
| `PRICE_DETAIL_TIMEOUT_SECONDS` | 18 | Per-page Playwright timeout |
| `PRICE_CONCURRENT_FETCHES` | 4 | Parallel Playwright workers |
| `PRICE_CACHE_TTL_SECONDS` | 1800 | Result cache TTL |
| `WEB_SCRAPER_HEADLESS` | true | Playwright headless mode |
| `WEB_SCRAPER_PER_DOMAIN_DELAY_SECONDS` | 1.0 | Rate limiting per domain |
| `WEB_SCRAPER_MAX_CONTEXTS` | 5 | Browser context pool size |

## Source files

```text
src/price_comparison_mcp/
  server.py             -- JSON-RPC 2.0 dispatcher
  browser_manager.py    -- Playwright stealth (adapted from Web Scraper)
  config.py             -- BrowserConfig + PriceSearchConfig
  errors.py             -- Structured error taxonomy
  response.py           -- Response mode builder
  cache.py              -- TTL cache for results
  price_extractor.py    -- Layered price extraction (noise-filtered)
  searxng_client.py     -- Async HTTP client for SearXNG
  serpapi_client.py     -- Async HTTP client for SerpAPI (optional)
  tools/
    search_prices.py    -- Main pipeline, scoring, outlier detection
    batch_search.py     -- Batch wrapper
    export_report.py    -- CSV export with outlier columns
```

## Tuning points in `tools/search_prices.py`

| Constant | Purpose |
|----------|---------|
| `_PRICE_SITE_SCORES` | 72 domains, score 25-100 (edit to add/remove portals) |
| `_MANUFACTURER_DOMAINS` | 52 brand sites, subtract `_MANUFACTURER_PENALTY` |
| `_MANUFACTURER_PENALTY` | 40 (deprioritizes manufacturer pages) |
| `_PRODUCT_PATH_MARKERS` / `_PRODUCT_PATH_BONUS` | 8 bonus for `/produkt/` etc. |
| `_CATEGORY_PATH_MARKERS` / `_CATEGORY_PATH_PENALTY` | -15 for `/kategorie/` etc. |
| `_URL_BLACKLIST_MARKERS` | archive/sold/forum paths excluded entirely |
| `TRUSTED_PRICE_DOMAINS` | auto-derived (score >= 55, non-manufacturer) |
| `_OUTLIER_LOW_RATIO` / `_OUTLIER_HIGH_RATIO` | 0.2 / 5.0 |
| `_OUTLIER_MAD_K` | 10.0 |

And in `price_extractor.py`:

| Constant | Purpose |
|----------|---------|
| `_PRICE_NOISE_MARKERS` | substrings that mark price elements as noise |

## Running locally

```bash
uv venv && uv pip install -e .
playwright install chromium
python -m price_comparison_mcp.server
```

## Code conventions

- ASCII-only in all files.
- English only in code, comments, tool descriptions, agent instructions.
- Deploy YAMLs structurally consistent across agents.
- Browser env vars use `WEB_SCRAPER_` prefix, price-pipeline env vars
  use `PRICE_` prefix, MCP framing uses `MCP_` prefix.
- All new portals should be added to `_PRICE_SITE_SCORES` only --
  `TRUSTED_PRICE_DOMAINS` is derived automatically from it.
