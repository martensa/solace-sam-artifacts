# Web Scraper MCP Agent for Solace Agent Mesh

A headless-browser web scraping agent that runs as an MCP server (stdio) in the
Solace Agent Mesh (SAM). Uses a real Chromium browser with stealth technology to
access bot-protected websites, download any file type, extract product images,
and take screenshots.

## Overview

This agent fills the gap where HTTP-based agents fail. When a request hits bot
protection (403 errors, CAPTCHAs, JavaScript challenges, empty responses), the
orchestrator routes to this agent. It can also download files and images from
protected sources that other agents cannot reach.

The agent is general-purpose and works in any workflow where web content must be
retrieved from protected or JavaScript-rendered websites. Other agents in the
mesh can delegate to it whenever they encounter access restrictions. Its output
(HTML, files, images) can be passed to any downstream agent for further
processing.

### When to use this agent

The orchestrator should route to this agent when:

- Another agent receives HTTP 403, CAPTCHA, or empty responses
- A website requires JavaScript rendering to display content
- Files (PDFs, manuals, catalogs, datasheets) must be downloaded from
  protected sources
- Product images need to be extracted from shop pages with bot protection
- A screenshot of a protected page is needed for visual analysis

### When NOT to use this agent

- Public web content accessible via simple HTTP requests (use a web research
  agent instead -- it is faster and uses fewer resources)
- Content that does not require a browser (API endpoints, RSS feeds, etc.)

## Architecture

```text
src/web_scraper_mcp/
  server.py              MCP JSON-RPC server (newline-delimited JSON over stdio)
  config.py              Configuration via environment variables (Pydantic)
  validation.py          URL validation, SSRF protection, input sanitization
  errors.py              Structured error taxonomy with codes and categories
  browser_manager.py     Browser lifecycle, stealth, CORS-free download, caching
  image_extractor.py     5-strategy product image detection
  response.py            Response builder with full/summary modes
  tools/
    fetch_webpage.py     HTML rendering with JS execution and challenge handling
    download_image.py    Product image download with intelligent extraction
    download_file.py     Universal file download (all media types)
    search_image.py      Image search via Google and Bing Images (parallel)
    screenshot.py        Webpage screenshots (full page or element)
```

## Tools

### fetch_protected_webpage

Fetch a bot-protected webpage using a headless browser. Returns fully rendered
HTML after JavaScript execution. Bypasses Cloudflare, Akamai, and similar bot
protection.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| url | string | yes | URL to fetch (http or https) |
| wait_for_selector | string | no | CSS selector to wait for before extraction |
| timeout_seconds | integer | no | Max wait time in seconds (5-120, default: 30) |
| response_mode | string | no | full or summary (default: full) |

### download_product_image

Download the main product image from a page URL. Extracts the image using
og:image, Schema.org, CSS selectors, and size heuristics. Returns
base64-encoded image data.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| url | string | yes | Product page URL or direct image URL |
| image_selector | string | no | CSS selector for the image element |
| output_format | string | no | Output format: png, jpg, or webp (default: png) |
| max_width | integer | no | Max width in pixels (50-4096, default: 1200) |
| response_mode | string | no | full or summary (default: full) |

### search_and_download_image

Search for a product image by name or identifier and download the best match
from Google or Bing Images. Use when no direct URL is available. Downloads
candidates in parallel batches for speed.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| search_query | string | yes | Product name, article number, or search term |
| preferred_sources | array | no | Preferred source domains for image selection |
| output_format | string | no | Output format: png, jpg, or webp (default: png) |
| response_mode | string | no | full or summary (default: full) |

### download_file

Download any file from a URL. Supports PDFs, Excel, Word, videos, images,
archives, and all other file types. Bypasses bot protection, CORS, and CDN
restrictions. Maximum file size is 50 MB.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| url | string | yes | Direct URL to the file (http or https) |
| navigate_first | boolean | no | Navigate first for session cookies (default: true) |
| response_mode | string | no | full or summary (default: full) |

### screenshot_webpage

Take a screenshot of a webpage or a specific element. Supports full-page and
element-specific captures via CSS selector. Use as fallback when no individual
resource can be extracted.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| url | string | yes | URL to screenshot (http or https) |
| element_selector | string | no | CSS selector for element-specific capture |
| viewport_width | integer | no | Viewport width (320-3840, default: 1920) |
| viewport_height | integer | no | Viewport height (240-2160, default: 1080) |
| full_page | boolean | no | Capture full scrollable page (default: false) |
| response_mode | string | no | full or summary (default: full) |

## Tool Selection

```text
Image needed + URL known:
  download_product_image --> screenshot_webpage (fallback)

Image needed + only name or identifier:
  search_and_download_image --> screenshot_webpage (fallback)

File or document needed:
  download_file

HTML content from protected site:
  fetch_protected_webpage

Last resort for any content:
  screenshot_webpage
```

## Error Handling

All tool errors return structured fields for programmatic routing:

| Field | Type | Description |
|-------|------|-------------|
| error_code | string | Machine-readable code (e.g. NETWORK_TIMEOUT, BOT_BLOCKED) |
| error_category | string | Category: network, bot_protection, not_found, validation, extraction, server, internal |
| retryable | boolean | Whether the orchestrator should retry |
| isError | boolean | Always true for errors |

Error categories:

| Category | Retryable codes | Non-retryable codes |
|----------|-----------------|---------------------|
| network | NETWORK_TIMEOUT, CONNECTION_RESET, CONNECTION_REFUSED | DNS_RESOLUTION_FAILED |
| bot_protection | | BOT_BLOCKED, CAPTCHA_REQUIRED, LOGIN_REQUIRED |
| not_found | | HTTP_404, HTTP_410, ELEMENT_NOT_FOUND, IMAGE_NOT_FOUND, NO_SEARCH_RESULTS |
| validation | | INVALID_URL, SSRF_BLOCKED, INVALID_PARAMETER, FILE_TOO_LARGE |
| server | HTTP_5XX | |
| extraction | DOWNLOAD_FAILED | INVALID_IMAGE_DATA |
| internal | BROWSER_CRASHED | INTERNAL_ERROR |

## Response Modes

All tools support a `response_mode` parameter that controls how much data is
returned. This is critical for multi-agent workflows where intermediate results
do not need full payloads.

### full (default)

Returns structured metadata as a markdown table followed by the inline payload
(base64 image, HTML text, file data). Use at the end of a workflow chain when
the actual content is needed.

### summary

Returns only the metadata table and a next-step hint. No payload data is
included. Use in mid-chain steps where only metadata (URL, MIME type, size,
filename) needs to be passed to the next agent.

### Artifact handling (SAM-managed)

Solace Agent Mesh handles artifact storage internally via its `artifact_service`
(S3-compatible, e.g. SeaweedFS). The deploy config sets
`artifact_handling_mode: reference`, which means SAM automatically stores large
payloads as artifacts in S3 and passes artifact URIs between agents. This
requires no custom code in the MCP server.

Related settings in the secret/config:

- `ENABLE_EMBED_RESOLUTION`: When true, SAM injects artifact content into the
  LLM context automatically. Recommended for Claude and Gemini (large context
  window). Set to false for GPT models to reduce prompt size.
- `ENABLE_ARTIFACT_CONTENT_INSTRUCTION`: When true, SAM adds instructions about
  artifact content to the LLM prompt.

The `builtin-group: artifact_management` in the config gives the agent access
to SAM's built-in artifact tools (list, read, create, update, delete).

### Next-step hints

Every response includes a one-line hint describing what can be done with the
output. These hints help the orchestrator decide the next agent in a workflow
chain without hardcoding agent names:

- HTML: "Can be parsed for links, data extraction, or further navigation."
- Images: "Available for embedding, display, or further analysis."
- Text files: "Available for analysis or further processing."
- Binary files: "Document conversion agents can extract text from PDF, DOCX,
  XLSX, and similar formats."
- Screenshots: "Available for visual analysis, embedding, or display."

## Health Check

The server supports a `health/check` MCP method that verifies the browser
subsystem is operational. It returns:

```json
{
  "healthy": true,
  "browser_connected": true,
  "active_contexts": 2,
  "error": null
}
```

A startup health check runs automatically when the server starts. If the
browser fails to launch, the server logs an error but continues accepting
requests (the browser may become available later).

## Multi-Agent Integration

### How the orchestrator discovers this agent

The agent publishes an agent card every 10 seconds via the Solace broker
(`agent_card_publishing: { interval_seconds: 10 }`). The agent card contains the
agent name, description, available skills, and supported input/output modes.
Agent discovery is enabled (`agent_discovery: { enabled: true }`), and
inter-agent communication allows requests from all agents
(`allow_list: ["*"]`).

The agent description is written as a decision rule:

> Use this agent when HTTP-based agents fail with 403, CAPTCHA, JavaScript
> challenges, or empty responses.

This gives the orchestrator a clear trigger condition rather than a feature list.

### Choosing the right response mode in workflows

In a multi-step workflow, the orchestrator should choose the response mode based
on what the next agent in the chain needs:

| Scenario | Recommended mode |
|----------|-----------------|
| Final step: user needs to see the content | full |
| Mid-chain: next agent only needs the URL or filename | summary |
| Single-step: only this agent is involved | full |

SAM's `artifact_handling_mode: reference` automatically handles large payload
storage and retrieval between agents via S3 artifacts.

## Security Bypass Architecture

### CORS (Cross-Origin Resource Sharing)

Downloads use `context.request` (Playwright API-level HTTP client) which
operates outside the Same-Origin-Policy. No CORS preflight, no origin checks.

### CSP (Content-Security-Policy)

Stealth JavaScript is injected via CDP `Page.addScriptToEvaluateOnNewDocument`
which executes before CSP evaluation (Chromium only).

### Mixed Content, COEP, COOP, CORP

API-level downloads bypass all browser-level cross-origin policies and
mixed-content blocking.

### Download Architecture

```text
Tool calls download_resource()
  1. context.request.get()        API-level HTTP, CORS-free, follows redirects
     on failure:
  2. In-page fetch() via evaluate  Uses page cookie/session context
     on failure:
  3. New page + page.goto()       Full browser navigation (last resort)
```

## Stealth and Anti-Detection

- Automation flags removed: `navigator.webdriver`, Playwright globals
- Chrome environment emulated: plugins, MimeTypes, `window.chrome`
- Fingerprint consistency: WebGL vendor/renderer, Canvas noise, Connection API
- Behavioral simulation: Bezier mouse movements, natural scrolling, delays
- CSP bypass: CDP-based script injection before CSP evaluation
- Domain context caching: cookies and sessions persist per domain
- Rate limiting with jitter: per-domain delay with random component

## Security Features

- SSRF protection: private IPs, loopback, carrier-grade NAT (100.64.0.0/10),
  IPv6-mapped IPv4, and cloud metadata endpoints are blocked. DNS resolution
  is validated before connection.
- Port restriction: only ports 80, 443, 8080, and 8443 are allowed.
- URL validation: only http and https schemes are allowed.
- Input validation: all parameters are checked against defined ranges before
  tool execution.
- Non-root container: the Docker image runs as an unprivileged user.

## Local Development

```bash
pip install -e ".[dev]"
playwright install chromium
pytest tests/ -v
python -m web_scraper_mcp.server
```

Test with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python -m web_scraper_mcp.server
```

## Configuration

### MCP server settings (shared SAM convention)

| Variable | Default | Description |
|----------|---------|-------------|
| MCP_MAX_RESPONSE_CHARS | 25000 | Hard character cap on tool responses to the LLM |
| MCP_LOG_LEVEL | INFO | MCP server log level |
| MCP_LOG_FILE | (empty) | Log file path (empty = stderr only) |

### Browser settings (WEB_SCRAPER_ prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| WEB_SCRAPER_HEADLESS | true | Run browser in headless mode |
| WEB_SCRAPER_BROWSER_TYPE | chromium | Browser engine (chromium, firefox, webkit) |
| WEB_SCRAPER_LOCALE | de-DE | Browser locale (Accept-Language, JS APIs) |
| WEB_SCRAPER_TIMEZONE | Europe/Berlin | Browser timezone |
| WEB_SCRAPER_STEALTH_ENABLED | true | Enable stealth mode |
| WEB_SCRAPER_MIN_DELAY_MS | 500 | Min delay between actions in milliseconds |
| WEB_SCRAPER_MAX_DELAY_MS | 3000 | Max delay between actions in milliseconds |
| WEB_SCRAPER_PER_DOMAIN_DELAY_SECONDS | 3 | Min wait between requests per domain |
| WEB_SCRAPER_MAX_CONTEXTS | 3 | Max concurrent browser contexts |
| WEB_SCRAPER_CONTEXT_IDLE_TIMEOUT | 300 | Idle timeout for contexts in seconds |
| WEB_SCRAPER_DEFAULT_TIMEOUT_MS | 30000 | Default operation timeout |
| WEB_SCRAPER_NAVIGATION_TIMEOUT_MS | 60000 | Page navigation timeout |

### SAM / LLM settings (in secret)

| Variable | Description |
|----------|-------------|
| NAMESPACE | SAM namespace (must match cluster) |
| SOLACE_BROKER_URL | Solace broker WebSocket URL |
| SOLACE_BROKER_VPN | Solace broker VPN name |
| SOLACE_BROKER_USERNAME | Solace broker username |
| SOLACE_BROKER_PASSWORD | Solace broker password |
| LLM_SERVICE_ENDPOINT | LLM API endpoint (e.g. LiteLLM proxy) |
| LLM_SERVICE_API_KEY | LLM API key |
| LLM_SERVICE_GENERAL_MODEL_NAME | LLM model name with provider prefix |
| ENABLE_EMBED_RESOLUTION | Inject artifact content into LLM context (true/false) |
| ENABLE_ARTIFACT_CONTENT_INSTRUCTION | Add artifact instructions to LLM prompt (true/false) |
| S3_BUCKET_NAME | S3 bucket for artifact storage |
| S3_ENDPOINT_URL | S3-compatible endpoint (e.g. SeaweedFS) |
| AWS_ACCESS_KEY_ID | S3 access key |
| AWS_SECRET_ACCESS_KEY | S3 secret key |
| AWS_REGION | S3 region (default: us-east-1) |

## Deployment

### Build and push the Docker image

```bash
docker build -t localhost:5000/sam-web-scraper-agent:1.0.0 .
docker push localhost:5000/sam-web-scraper-agent:1.0.0
```

### Deploy to Kubernetes

Before deploying, replace all placeholder values in the secret file with
actual credentials. For production, use an external secret store (External
Secrets Operator, Sealed Secrets, or HashiCorp Vault).

```bash
kubectl apply -f deploy/sam-web-scraper-agent-secret.yaml
kubectl apply -f deploy/sam-web-scraper-agent-config.yaml
kubectl apply -f deploy/sam-web-scraper-agent-deployment.yaml
```

### Resource requirements

| Resource | Request | Limit |
|----------|---------|-------|
| CPU | 500m | 2000m |
| Memory | 1 Gi | 4 Gi |

The agent requires more memory than typical agents because it runs a full
Chromium browser. The 4 Gi limit accommodates up to 3 concurrent browser
contexts with complex pages.

## LLM Compatibility

The agent is designed to work with any major LLM (Claude, GPT-4o, Gemini)
as the orchestrator:

- Tool descriptions are concise and use imperative English
- Input schemas include min/max constraints and enums for structured validation
- Responses use markdown tables for structured metadata
- Binary content is base64-encoded with explicit MIME types
- Structured error responses with machine-readable codes and retry guidance
- Next-step hints guide the orchestrator without naming specific agents
- Response modes reduce token overhead in multi-agent chains
- MCP_MAX_RESPONSE_CHARS truncates oversized responses (25000 for Claude/Gemini,
  10000 recommended for GPT)

## Testing

```bash
# Unit tests (fast, no browser needed)
pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires Chromium + network)
playwright install chromium
pytest tests/test_integration.py -v

# All tests
pytest tests/ -v

# Lint
ruff check src/ tests/
```

## Project Structure

```text
Web Scraper Agent/
  Dockerfile                                  Container build definition
  pyproject.toml                              Python package configuration
  README.md                                   This file
  CLAUDE.md                                   AI-assisted development notes
  requirements-lock.txt                       Pinned production dependencies
  .github/workflows/ci.yaml                  CI pipeline (lint, test, ASCII check)
  .gitignore                                  Git ignore rules
  src/
    web_scraper_mcp/
      __init__.py                             Package init with version
      server.py                               MCP server entry point
      config.py                               Environment-based configuration
      errors.py                               Structured error taxonomy
      validation.py                           Input validation and SSRF protection
      browser_manager.py                      Browser lifecycle and downloads
      image_extractor.py                      Product image extraction logic
      response.py                             Response builder (full/summary)
      tools/
        __init__.py
        fetch_webpage.py                      fetch_protected_webpage tool
        download_image.py                     download_product_image tool
        download_file.py                      download_file tool
        search_image.py                       search_and_download_image tool
        screenshot.py                         screenshot_webpage tool
  tests/
    test_validation.py                        URL validation and SSRF tests
    test_response.py                          Response builder tests
    test_config.py                            Configuration tests
    test_server.py                            MCP protocol and dispatch tests
    test_errors.py                            Error taxonomy tests
    test_download_image.py                    Image processing tests
    test_integration.py                       End-to-end tests with real browser
  deploy/
    sam-web-scraper-agent-config.yaml         Kubernetes ConfigMap (SAM apps format)
    sam-web-scraper-agent-deployment.yaml     Kubernetes Deployment
    sam-web-scraper-agent-secret.yaml         Kubernetes Secret (placeholders)
```
