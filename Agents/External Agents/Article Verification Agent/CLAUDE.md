# Article Verification Agent

## What this is

MCP-based agent that verifies and enriches incomplete B2B product article
descriptions. Given a raw article code like "50140.2K4 Wandleuchte", it
identifies the manufacturer (BEGA), full product name, and category via web
search.

## Key architecture decisions

- **MCP over stdio**: JSON-RPC 2.0, Python server at
  `/opt/article-verification-mcp/article_verification_mcp_server.py`
- **Two-step workflow**: `check_article` (pre-filter) then `search_article`
  (web search). Pre-filter catches non-product inputs (Nettoartikel,
  Bruttoartikel, NLAG) without wasting a web search.
- **SearXNG primary, DDG fallback**: Search via shared SearXNG service
  (`http://searxng.sam-ent-k8s.svc.cluster.local:8080`) with DuckDuckGo HTML
  scraping as fallback if SearXNG is unavailable.
- **Broad search strategy**: Search for article code as-is without appending
  keywords like "Datenblatt". Broad queries return more distributor/shop
  listings where manufacturer names appear in titles.
- **No custom artifact/session management**: SAM handles artifacts via
  `builtin-group: artifact_management`.
- **Max 3 tool calls**: Speed constraint to keep response times reasonable.
  check_article + search_article + optional load_artifact = 3.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `check_article` | Pre-filter: detects pricing labels, extracts article code, builds search query |
| `search_article` | Web search via SearXNG/DDG, returns titles/URLs/snippets |

## Pre-filter patterns

Inputs matching these are skipped without web search:
- Nettoartikel, Nettoangebotspreise (net-price labels)
- BRUTTOARTIKEL (gross-price label)
- NLAG (pricing label)
- "Brutto" without a numeric article code

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SEARXNG_URL` | `http://searxng.sam-ent-k8s.svc.cluster.local:8080` | SearXNG endpoint |
| `SEARCH_MAX_RESULTS` | `10` | Max results per search |
| `MCP_LOG_LEVEL` | `INFO` | Log level |
| `MCP_LOG_FILE` | `article_verification_mcp.log` | Log file path |

## Output format

Returns JSON with: manufacturer, product_name, article_number, category,
confidence (high/medium/low/none), is_specific, original_input.

## Test results (April 2026, SearXNG)

8/8 (100%) on representative B2B articles including BEGA, Gira, OBO Bettermann,
LEDVANCE, WAGO, Merten, and Nettoartikel skip detection.

## Code conventions

- ASCII-only in all files.
- English for code, comments, tool descriptions.
- MCP server uses only Python stdlib (urllib, json, re) -- no requests needed.
- All regex patterns use unicode escapes for umlauts (\u00C4 etc.).
