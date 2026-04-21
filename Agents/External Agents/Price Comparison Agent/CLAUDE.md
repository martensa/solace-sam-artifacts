# Price Comparison Agent

## What this is

MCP-based price comparison agent for Solace Agent Mesh. Uses SearXNG meta-search
for price discovery, Playwright with stealth mode for detail page extraction, and
optional SerpAPI for Google Shopping. Returns structured price data with merchant,
shipping, and source URLs.

## Key architecture decisions

- **MCP over stdio**: JSON-RPC 2.0 with newline-delimited JSON framing (same
  pattern as Web Scraper Agent).
- **SearXNG for discovery**: Queries SearXNG JSON API (cluster-internal) to find
  price-relevant URLs from Google, Bing, and DuckDuckGo. Appends "Preis kaufen"
  to queries for better price results.
- **Playwright for accuracy**: Fetches top-ranked URLs via stealth browser to
  extract accurate prices using site-specific CSS selectors, JSON-LD, microdata,
  and regex fallbacks.
- **SerpAPI optional**: If PRICE_SERPAPI_KEY is set, also queries Google Shopping
  for structured price data. Otherwise works without it.
- **Speed constraint**: Max 3 tool calls, 55-second pipeline budget, 15s per
  detail page. Batch defaults to fetch_details=false (SearXNG-only, 3-5s/item).
- **B2B detection**: Skips price search for Nettoartikel/B2B items.
- **Programmatic tool budget**: max_llm_calls_per_task=8.

## Tools

| Tool | Purpose |
|------|---------|
| `search_product_prices` | Default price search by EAN or product name |
| `batch_search_prices` | Multiple products at once (up to 10) |
| `export_comparison_report` | CSV export of results |

## Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PRICE_SEARXNG_URL` | (cluster URL) | SearXNG JSON API endpoint |
| `PRICE_SERPAPI_KEY` | (empty) | Optional Google Shopping via SerpAPI |
| `WEB_SCRAPER_HEADLESS` | true | Playwright headless mode |
| `WEB_SCRAPER_PER_DOMAIN_DELAY_SECONDS` | 1.0 | Rate limiting per domain |

## Source files

```text
src/price_comparison_mcp/
  server.py             -- JSON-RPC 2.0 dispatcher
  browser_manager.py    -- Playwright stealth (adapted from Web Scraper)
  config.py             -- BrowserConfig + PriceSearchConfig
  errors.py             -- Structured error taxonomy
  response.py           -- Response mode builder
  cache.py              -- TTL cache for results
  price_extractor.py    -- Layered price extraction from HTML
  searxng_client.py     -- Async HTTP client for SearXNG
  serpapi_client.py      -- Async HTTP client for SerpAPI (optional)
  tools/
    search_prices.py    -- Main search pipeline
    batch_search.py     -- Batch search wrapper
    export_report.py    -- CSV export
```

## Running locally

```bash
uv venv && uv pip install -e .
playwright install chromium
python -m price_comparison_mcp.server
```

## Code conventions

- ASCII-only in all files.
- English only in code, comments, tool descriptions, agent instructions.
- Deploy YAMLs structurally identical to Web Scraper Agent.
- All env vars for browser use WEB_SCRAPER_ prefix, price config uses PRICE_ prefix.
- MCP env vars: MCP_MAX_RESPONSE_CHARS, MCP_LOG_LEVEL, MCP_LOG_FILE.
