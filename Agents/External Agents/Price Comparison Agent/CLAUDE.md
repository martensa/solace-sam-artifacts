# Price Comparison Agent

## What this is

Python tool-based agent (not MCP) that scrapes real-time prices from Idealo,
Geizhals, and Google Shopping. Returns price comparisons sorted by total cost.

## Key architecture decisions

- **Python tools (not MCP)**: Uses `tool_type: python` with
  `component_module: price_comparison_agent.tools`. This is different from
  the other agents which use MCP over stdio.
- **Speed constraint**: Max 2 tool calls, 15-second budget.
- **B2B detection**: Skips price search for Nettoartikel/B2B items and
  responds with "Nettoartikel -- kein oeffentlicher Preis verfuegbar."
- **EAN-first search**: Searches by EAN if available (more precise), falls
  back to product name search.
- **Three scraper sources**: Idealo, Geizhals, Google Shopping. Each can be
  enabled/disabled independently.

## Tools

| Tool | Purpose |
|------|---------|
| `search_product_prices` | Default price search by EAN or product name |
| `batch_search_prices` | Multiple products at once |
| `compare_suppliers` | Explicit supplier ranking |
| `find_cheaper_alternatives` | Only when explicitly requested |
| `export_comparison_report` | CSV export |

## Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SERPAPI_KEY` | (empty) | Optional, for structured Google Shopping |
| `SCRAPER_PROXY` | (empty) | Optional rotating proxy |

## Scraper source files

- `src/price_comparison_agent/scrapers/idealo.py`
- `src/price_comparison_agent/scrapers/geizhals.py`
- `src/price_comparison_agent/scrapers/google_shopping.py`

## Installation note

Uses `pip install .` from pyproject.toml (not uv). Source directory is removed
after install to avoid duplicate tool registration by SAM.
