# Web Scraper MCP Agent

## What this is

A headless-browser web scraping MCP server for Solace Agent Mesh (SAM).
Playwright + stealth plugins bypass bot protection (Cloudflare, Akamai, CORS, CSP).

## Key architecture decisions

- **MCP over stdio**: JSON-RPC 2.0 with newline-delimited JSON framing.
- **No custom artifact/session management**: SAM handles artifacts (S3/SeaweedFS)
  and sessions internally. The agent only uses `builtin-group: artifact_management`.
- **response_mode**: `full` (inline payload) or `summary` (metadata only).
  SAM's `artifact_handling_mode: reference` handles the artifact layer.
- **3-tier download**: context.request -> in-page fetch() -> page.goto.
- **Domain context caching**: Browser contexts are cached per domain to preserve
  cookies/sessions. This is the agent's own integrated caching layer.
- **Structured errors**: All tool errors include error_code, error_category, and
  retryable fields for programmatic orchestrator routing.

## Running locally

```bash
pip install -e ".[dev]"
playwright install chromium
pytest tests/ -v
python -m web_scraper_mcp.server
```

## Running tests

```bash
# Unit tests (fast)
pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires Chromium + network)
pytest tests/test_integration.py -v

# All tests
pytest tests/ -v
```

## Code conventions

- ASCII-only in all files (no unicode dashes, arrows, or box-drawing characters).
- English only in code, comments, tool descriptions, and agent instructions.
- Keep deploy YAMLs structurally identical to the EAN Agent reference at
  github.com/martensa/solace-sam-artifacts (apps format, not flows).
- All env vars for the MCP subprocess use `WEB_SCRAPER_` prefix (browser config)
  or no prefix (`MCP_MAX_RESPONSE_CHARS`, `MCP_LOG_LEVEL`, `MCP_LOG_FILE`).
- Tool handler signature: `async def handle_*(arguments, browser_mgr) -> dict`.
- Locale/timezone defaults: de-DE / Europe/Berlin (configurable via env).
