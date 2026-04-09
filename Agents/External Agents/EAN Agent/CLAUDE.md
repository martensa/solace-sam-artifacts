# EAN Search Agent

## What this is

MCP-based agent for EAN/UPC/GTIN barcode lookup. Given a product name or
manufacturer, it searches barcode databases and returns EAN-13 codes.

## Key architecture decisions

- **MCP over stdio**: JSON-RPC 2.0, Python server at
  `/opt/ean-search-mcp-server/ean_search_mcp_server.py`
- **Dual backend**: ean-search.org (1B+ barcodes, API token required) or
  UPCitemdb (687M+ products, free tier 100/day). Configured via
  `EAN_DATABASE_BACKEND` env var.
- **Speed constraint**: Max 2 tool calls, 10-second budget. Partial results
  are acceptable.
- **Rate limiting**: Built-in per-request interval (`EAN_SEARCH_MIN_INTERVAL`)
  and in-memory cache (`EAN_CACHE_TTL`).

## MCP Tools

| Tool | Purpose |
|------|---------|
| `ean_product_search` | Find EAN by product name with auto-pagination |
| `ean_barcode_lookup` | Fast lookup by numeric EAN/UPC/GTIN |
| `ean_category_search` | Search within product category |
| `ean_validation` | Validate check digits, GS1 country lookup (zero API cost) |

## Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `EAN_DATABASE_BACKEND` | `ean_search` | Backend: `ean_search` or `upcitemdb` |
| `EAN_SEARCH_API_TOKEN` | - | API token for ean-search.org |
| `EAN_SEARCH_LANGUAGE` | `99` (any) | Language filter |
| `EAN_CACHE_TTL` | `3600` | Cache TTL in seconds |
| `MCP_MAX_RESPONSE_CHARS` | `25000` | Max MCP response size |
