# Price Comparison Agent v1.0

A professional [Solace Agent Mesh][sam] agent for real-time product
price comparison across multiple German price-comparison platforms.
Built for **procurement teams and facility managers** who need fast,
reliable price intelligence for purchasing decisions, tender
evaluations, and contract negotiations.

```text
+-------------------+   A2A / Solace Event Mesh   +-------------------------+
|  SAM Orchestrator |  ========================>  | Price Comparison Agent  |
|  (or user chat)   |  <========================  |  - Idealo scraper       |
+-------------------+                             |  - Geizhals scraper     |
         ^                                        |  - Google Shopping      |
         |                                        +-------------------------+
         |     Inter-Agent Communication                      ^
         v                                                    |
+-------------------+     +----------------------+            |
| EAN Search Agent  |     | Contract DB Agent    |  ----------+
+-------------------+     +----------------------+
```

---

## Table of Contents

- [Overview](#overview)
- [Use Cases](#use-cases)
- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Tools Reference](#tools-reference)
- [Agent Card and Skills](#agent-card-and-skills)
- [Configuration Reference](#configuration-reference)
- [Quick Start (Local)](#quick-start-local)
- [Docker](#docker)
- [Kubernetes Deployment](#kubernetes-deployment)
- [Testing](#testing)
- [LLM Compatibility](#llm-compatibility)
- [Inter-Agent Communication](#inter-agent-communication)
- [Scraping Notes](#scraping-notes)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Overview

The Price Comparison Agent gives any AI workflow inside a Solace Agent
Mesh deployment the ability to look up current product prices across
the three largest German-language price-comparison services:

- **Idealo.de** -- Germany's largest price-comparison site
- **Geizhals.de** -- Widely used in Germany, Austria, and Switzerland
- **Google Shopping** -- Broadest coverage, powered by SerpAPI or HTML

All three sources are queried in parallel, results are deduplicated
and ranked by total cost (product price + shipping). The agent
proactively detects whether cheaper alternatives are available and
surfaces them without being asked.

The agent operates as part of the **Solace Agent Mesh** alongside
other specialized agents (EAN Search Agent, Contract DB Agent, and
others). Every tool response includes structured JSON data for
seamless inter-agent communication.

---

## Use Cases

### AI-Assisted Tender Comparison

Process large tender documents (Leistungsverzeichnisse) with dozens
or hundreds of line items. The batch search tool accepts the full
position list, retrieves prices for all items in parallel, and
produces a cost overview with grand total -- ready for award
documentation.

### Facility Management Price Lookup

Facility managers (Objektleiter) can quickly look up EAN barcodes
or product names to find current market prices, identify the
cheapest supplier, and discover alternative products with savings
potential -- all in a single conversational interaction.

### Contract Price Validation

Compare existing contract prices against current market prices to
identify renegotiation opportunities. The agent provides tiered
recommendations (urgent, recommended, or optional) based on the
price deviation percentage.

### Procurement Documentation

Export structured CSV reports for award memos (Vergabevermerke),
offer comparisons, and audit documentation. Reports include
metadata, statistical price insights, and all individual offers.

---

## Features

| Feature | Detail |
| --- | --- |
| EAN / barcode lookup | EAN-8, EAN-13, UPC-A, GTIN-14 with checksum validation |
| Free-text product search | Brand names, model numbers, descriptions |
| Multi-source parallel search | All three platforms queried concurrently via asyncio |
| Total-cost ranking | Sorts by price + shipping |
| Supplier comparison | All merchants for one product, deduplicated |
| Auto alternative suggestions | Triggered when savings exceed 10% |
| Savings calculation | Absolute EUR and relative percentage |
| Price insights | Min, max, median, average, price spread with merchant count |
| Batch price search | Multiple products at once for tenders (concurrency-limited) |
| Contract price comparison | Market price vs. contract price with renegotiation advice |
| CSV export | Structured report with metadata and insights sections |
| Quantity support | All tools accept quantity for total cost calculation |
| Structured JSON output | Every tool response includes JSON for inter-agent use |
| Result caching | TTL-based in-memory cache to avoid duplicate scraper requests |
| Direct purchase links | Clickable merchant link per offer |
| Multilingual responses | Responds in the user's language, defaults to German |
| Retry with backoff | Up to 3 retries with exponential back-off (429, 503, timeouts) |
| Proxy support | Optional HTTP/HTTPS proxy for scraper requests |
| SerpAPI integration | Optional structured Google Shopping data |
| Health probes | Kubernetes liveness and readiness probes |

---

## Architecture

```text
User / Orchestrator
         |
         | A2A task (text)
         v
+------------------------------------------------+
|          Price Comparison Agent (SAM)           |
|                                                |
|   instruction (LLM system prompt, 9 rules)     |
|   +--------------------------------------------+
|   | LLM (GPT-4o / Claude / Gemini / ...)       |
|   | - auto-detects EAN vs. product name        |
|   | - selects and calls tools                  |
|   | - formats markdown response                |
|   +--------------------------------------------+
|                                                |
|   tools/  (DynamicToolProvider)                |
|   +------------------------------+             |
|   | 1. search_product_prices     |             |
|   | 2. find_cheaper_alternatives |             |
|   | 3. compare_suppliers         |             |
|   | 4. batch_search_prices       |             |
|   | 5. compare_with_contract     |             |
|   | 6. export_comparison_report  |             |
|   +------------------------------+             |
|          |           |           |              |
|       Idealo      Geizhals  Google Shopping     |
|     (scraper)    (scraper)  (SerpAPI / HTML)    |
+------------------------------------------------+
         |                          |
    structured JSON            structured JSON
         v                          v
+-------------------+     +----------------------+
| EAN Search Agent  |     | Contract DB Agent    |
+-------------------+     +----------------------+
```

The agent is built on the **Solace Agent Mesh (SAM)** framework.
SAM routes messages between agents via a Solace Event Broker using
the A2A protocol. The agent publishes an **Agent Card** every 10
seconds that allows other agents and orchestrators to discover its
capabilities automatically.

Key components:

- **DynamicToolProvider** -- registers all 6 tools with the SAM
  framework and injects tool configuration from the ConfigMap
- **BaseScraper** -- abstract base class with shared HTTP retry
  logic, price parsing, TTL-based caching, and alternative search
- **Pydantic v2 models** -- strict data validation with
  `model_dump(mode="json")` for inter-agent serialization
- **Artifact management** -- built-in SAM tool group for storing
  exported CSV reports in S3-compatible storage

---

## Project Structure

```text
.
+-- Dockerfile                     Docker image (SAM enterprise base)
+-- .dockerignore                  Excluded from Docker build context
+-- pyproject.toml                 Python package metadata (v1.0.0)
+-- requirements.txt               Pinned runtime dependencies
+-- README.md                      This file
|
+-- deploy/                        Kubernetes manifests
|   +-- ...-config.yaml            ConfigMap (full agent YAML config)
|   +-- ...-deployment.yaml        Deployment with health probes
|   +-- ...-secret.yaml            Secret template (fill before use)
|
+-- src/
|   +-- price_comparison_agent/
|       +-- __init__.py            Package init, version (1.0.0)
|       +-- models.py             Pydantic v2 data models (11 models)
|       +-- utils.py              EAN validation, price formatting
|       +-- tools.py              DynamicToolProvider (6 tools)
|       +-- scrapers/
|           +-- __init__.py        Re-exports scraper classes
|           +-- base.py            Abstract base, HTTP retry, caching
|           +-- idealo.py          Idealo.de scraper
|           +-- geizhals.py        Geizhals.de scraper
|           +-- google_shopping.py SerpAPI + HTML fallback scraper
|
+-- tests/
    +-- __init__.py
    +-- test_utils.py              EAN validation, price formatting tests
    +-- test_tools.py              Batch parser, insights, merge tests
    +-- test_scrapers.py           Cache, price parsing, shipping tests
```

All Kubernetes file names follow the pattern
`sam-price-comparison-agent-<kind>.yaml`.

---

## Tools Reference

### 1. `search_product_prices`

Queries all enabled price-comparison sources in parallel and returns
a merged, deduplicated, price-sorted list of offers with statistical
price insights.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | yes | EAN or product name |
| `search_type` | string | no | `"ean"` / `"name"` (auto-detected) |
| `max_results` | integer | no | Per source (default 5, max 20) |
| `quantity` | integer | no | Quantity for total cost (default 1) |

**Returns:** Markdown table sorted by total price with merchant
links, price insights (min/max/median/spread), and a `structured`
JSON payload for other agents.

---

### 2. `find_cheaper_alternatives`

Searches for functionally equivalent products that cost less than a
given reference price. Savings must exceed 5% to qualify.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `product_name` | string | yes | Reference product name |
| `current_price` | float | yes | Reference unit price in EUR |
| `category` | string | no | Product category to narrow search |
| `max_alternatives` | integer | no | Maximum alternatives (default 5) |
| `quantity` | integer | no | Quantity for total savings (default 1) |

**Returns:** Ranked table with price, merchant, savings in EUR and
percentage. When quantity > 1, includes total savings per item.

---

### 3. `compare_suppliers`

Returns a ranked supplier table for a single product. Shows price,
shipping, total cost, delivery time, and a direct link per merchant.
Offers are deduplicated per merchant (best price wins).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | yes | EAN or product name |
| `search_type` | string | no | `"ean"` / `"name"` (auto-detected) |
| `quantity` | integer | no | Quantity for total cost (default 1) |

**Returns:** Table sorted by total price (cheapest first) with price
insights and purchase recommendation.

---

### 4. `batch_search_prices`

Searches multiple products simultaneously with concurrency limiting.
Ideal for tender specifications (Leistungsverzeichnisse) and RFQs.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | string | yes | Comma- or line-separated product list |

Input format supports quantity and position labels:

```text
10 x 4006381333931 | Pos. 1.1
Bosch GSR 18V-28
5 x Hilti TE 30-A36
3 x Fischer FIS V 360 | Pos. 3.2
```

Comma-separated input is also supported for simple lists:

```text
Bosch GSR 18V, Hilti TE 30, Makita DHP 486
```

**Returns:** Summary table with per-item unit price, total price,
merchant, and a grand total across all positions. Includes structured
JSON with per-item insights.

---

### 5. `compare_with_contract_price`

Compares a contract price against current market prices. Identifies
renegotiation needs using a three-tier recommendation system:

- **> 15% above market** -- Urgent renegotiation needed
- **5-15% above market** -- Renegotiation recommended
- **< 5% above market** -- Contract is market-competitive

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | yes | EAN or product name |
| `contract_price` | float | yes | Contract unit price in EUR |
| `contract_supplier` | string | no | Contract supplier name |
| `contract_reference` | string | no | Contract number or reference |
| `quantity` | integer | no | Quantity for total comparison (default 1) |

**Returns:** Contract vs. market comparison table with actionable
recommendation and price insights.

---

### 6. `export_comparison_report`

Exports a price comparison as a structured CSV report. The CSV is
stored as an artifact via the built-in `artifact_management` tool
group (S3-compatible storage).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | yes | EAN or product name |
| `search_type` | string | no | `"ean"` / `"name"` (auto-detected) |
| `quantity` | integer | no | Quantity (default 1) |

**CSV structure:**

1. **Metadata section** -- search query, type, quantity, offer count
2. **Price insights section** -- min, max, median, average, spread
3. **Offer list** -- product, brand, EAN, merchant, price, shipping,
   total, delivery time, source, and URL

The CSV uses semicolon (`;`) as delimiter for Excel compatibility
in German locale.

---

## Agent Card and Skills

The agent publishes an A2A Agent Card every 10 seconds for automatic
capability discovery. The card defines 6 skills that map 1:1 to the
registered tools:

| Skill ID | Skill Name | Maps to Tool |
| --- | --- | --- |
| `price_search` | Product Price Search | `search_product_prices` |
| `alternative_finder` | Cheaper Alternative Finder | `find_cheaper_alternatives` |
| `supplier_comparison` | Supplier Comparison | `compare_suppliers` |
| `batch_search` | Batch Price Search | `batch_search_prices` |
| `contract_price_comparison` | Contract Price vs. Market | `compare_with_contract_price` |
| `export_report` | CSV Export Report | `export_comparison_report` |

**Input modes:** `text`

**Output modes:** `text`, `file`

The agent instruction contains 9 rules that govern LLM behavior:

1. **Auto-detect input type** -- EAN vs. product name (never ask)
2. **Always use tools** -- never invent or estimate data
3. **Choose the right tool** -- decision matrix for all 6 tools
4. **Retry on empty results** -- up to 3 attempts with broadened queries
5. **Proactively suggest alternatives** -- when savings exceed 10%
6. **Highlight price insights** -- display statistics prominently
7. **Inter-agent communication** -- use structured JSON from other agents
8. **Response format** -- markdown tables, sorted by total price
9. **Language** -- respond in user's language, default German

---

## Configuration Reference

All runtime configuration is injected via environment variables.
In Kubernetes these come from the Secret
(`deploy/sam-price-comparison-agent-secret.yaml`). For local
development, set them in your shell or a `.env` file.

### Required Variables

| Variable | Description |
| --- | --- |
| `NAMESPACE` | SAM namespace for this agent group |
| `SOLACE_BROKER_URL` | Broker WebSocket URL |
| `SOLACE_BROKER_VPN` | Solace Message VPN name |
| `SOLACE_BROKER_USERNAME` | Broker username |
| `SOLACE_BROKER_PASSWORD` | Broker password |
| `LLM_SERVICE_ENDPOINT` | LiteLLM proxy or provider URL |
| `LLM_SERVICE_API_KEY` | LLM provider API key |
| `LLM_SERVICE_GENERAL_MODEL_NAME` | Model ID, e.g. `openai/gpt-4o` |
| `S3_BUCKET_NAME` | S3-compatible bucket for artifacts |
| `S3_ENDPOINT_URL` | S3 endpoint URL |
| `AWS_ACCESS_KEY_ID` | S3 access key |
| `AWS_SECRET_ACCESS_KEY` | S3 secret key |

### Optional Variables

| Variable | Default | Description |
| --- | --- | --- |
| `SERPAPI_KEY` | `""` | Google Shopping structured data via SerpAPI |
| `SCRAPER_PROXY` | `""` | HTTP proxy for scraper requests |
| `SOLACE_DEV_MODE` | `false` | Dev mode (no broker connection needed) |
| `USE_TEMPORARY_QUEUES` | `true` | Use ephemeral broker queues |
| `ENABLE_EMBED_RESOLUTION` | `true` | Inject artifact content into LLM context |
| `ENABLE_ARTIFACT_CONTENT_INSTRUCTION` | `true` | Include artifact instructions |
| `AWS_REGION` | `us-east-1` | S3 region |
| `ARTIFACT_SERVICE_TYPE` | `s3` | Artifact backend type |

### Tool Configuration (ConfigMap)

These settings are defined in the ConfigMap under `tool_config`:

| Setting | Default | Description |
| --- | --- | --- |
| `enable_idealo` | `true` | Enable Idealo scraper |
| `enable_geizhals` | `true` | Enable Geizhals scraper |
| `enable_google` | `true` | Enable Google Shopping scraper |
| `max_results_per_source` | `5` | Max products per source (1-20) |
| `request_timeout` | `15` | HTTP timeout in seconds |
| `cache_ttl` | `300` | Result cache TTL in seconds (0 to disable) |

---

## Quick Start (Local)

### Prerequisites

- Python 3.12 or later
- A running Solace Event Broker (PubSub+ Standard free tier works)
- An LLM API key (OpenAI, Anthropic, Google, or a LiteLLM proxy)

### Setup

```bash
# 1. Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt
pip install -e ".[dev]"

# 3. Set required environment variables
export NAMESPACE="sam-local"
export SOLACE_BROKER_URL="ws://localhost:8008"
export SOLACE_BROKER_VPN="default"
export SOLACE_BROKER_USERNAME="default"
export SOLACE_BROKER_PASSWORD="default"
export LLM_SERVICE_ENDPOINT="https://api.openai.com/v1"
export LLM_SERVICE_API_KEY="sk-..."
export LLM_SERVICE_GENERAL_MODEL_NAME="openai/gpt-4o"
export S3_BUCKET_NAME="sam-local"
export S3_ENDPOINT_URL="http://localhost:8333"
export AWS_ACCESS_KEY_ID="local"
export AWS_SECRET_ACCESS_KEY="local"

# Optional: SerpAPI for reliable Google Shopping results
export SERPAPI_KEY="your-serpapi-key"

# 4. Run the agent
sam run deploy/sam-price-comparison-agent-config.yaml
```

The agent registers itself with the broker and is immediately
available for A2A requests from any other agent or gateway in the
same namespace.

---

## Docker

### Build the Image

```bash
docker build \
  -t localhost:5000/sam-price-comparison-agent:1.0.0 .
```

The image extends
`localhost:5000/solace-agent-mesh-enterprise:latest`.
Replace the registry prefix to match your environment.

### Run Locally

```bash
docker run --rm \
  -e NAMESPACE="sam-local" \
  -e SOLACE_BROKER_URL="ws://host.docker.internal:8008" \
  -e SOLACE_BROKER_VPN="default" \
  -e SOLACE_BROKER_USERNAME="default" \
  -e SOLACE_BROKER_PASSWORD="default" \
  -e LLM_SERVICE_ENDPOINT="https://api.openai.com/v1" \
  -e LLM_SERVICE_API_KEY="sk-..." \
  -e LLM_SERVICE_GENERAL_MODEL_NAME="openai/gpt-4o" \
  -e S3_BUCKET_NAME="sam-local" \
  -e S3_ENDPOINT_URL="http://host.docker.internal:8333" \
  -e AWS_ACCESS_KEY_ID="local" \
  -e AWS_SECRET_ACCESS_KEY="local" \
  localhost:5000/sam-price-comparison-agent:1.0.0
```

### Push to Registry

```bash
docker push localhost:5000/sam-price-comparison-agent:1.0.0
```

---

## Kubernetes Deployment

Three manifest files are provided in the `deploy/` directory.
Apply them in the order shown below.

### Step 1 -- Fill in the Secret

Open `deploy/sam-price-comparison-agent-secret.yaml` and replace
every placeholder value with real credentials.

> **Security note:** Never commit a Secret file with real credentials
> to version control. Use Sealed Secrets, External Secrets Operator,
> or a secrets management solution (HashiCorp Vault, AWS Secrets
> Manager) in production environments.

### Step 2 -- Apply Manifests

```bash
kubectl apply -f deploy/sam-price-comparison-agent-secret.yaml
kubectl apply -f deploy/sam-price-comparison-agent-config.yaml
kubectl apply -f deploy/sam-price-comparison-agent-deployment.yaml
```

### Step 3 -- Verify

```bash
# Check pod status
kubectl -n sam-ent-k8s-agents get pods \
  -l app=sam-custom-agents

# Tail logs
kubectl -n sam-ent-k8s-agents logs -f \
  deployment/sam-price-comparison-agent

# Check readiness
kubectl -n sam-ent-k8s-agents describe \
  deployment sam-price-comparison-agent
```

Expected log output when the agent is healthy:

```text
INFO  price_comparison_agent - Agent registered
INFO  price_comparison_agent - Listening on namespace
```

### Deployment Features

The Kubernetes deployment includes:

- **Startup probe** -- process check every 5s (allows up to 60s for
  initial startup before liveness kicks in)
- **Liveness probe** -- process check every 30s (restarts
  unresponsive pods after 3 failures)
- **Readiness probe** -- process check every 10s (removes pod from
  service on 2 consecutive failures)
- **Resource limits** -- 100m-1000m CPU, 512Mi-1536Mi memory
- **ConfigMap volume mount** -- agent YAML config injected at
  `/app/configs/agents/`

### Step 4 -- Update the Image

To deploy a new version, update the image tag and re-apply:

```bash
kubectl -n sam-ent-k8s-agents set image \
  deployment/sam-price-comparison-agent \
  sam-price-comparison-agent=\
localhost:5000/sam-price-comparison-agent:2.1.0
```

---

## Testing

The project includes unit tests for all core pure functions. Tests
do not require a running broker, LLM, or internet connection.

### Run Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_utils.py -v
```

### Test Coverage

| Test File | Covers |
| --- | --- |
| `test_utils.py` | EAN validation (8/13/14-digit, checksums), price formatting, savings calculation, search type detection, text truncation |
| `test_tools.py` | Batch input parser (quantity, labels, multiline, comma, EAN), dedup key generation, price insights computation, product merging and deduplication |
| `test_scrapers.py` | Result cache (TTL, expiry, clear), price parsing (German format, EUR prefix, integers), shipping cost parsing (gratis, numeric, unknown) |

---

## LLM Compatibility

The agent instruction and tool docstrings are written in English
for maximum compatibility across all major LLM families. The agent
has been designed and tested to produce consistent results with:

| Model Family | Example Model Name | Embed Resolution |
| --- | --- | --- |
| GPT-4o / GPT-5 | `openai/gpt-4o` | `false` |
| Claude 3.5 / 4.x | `openai/claude-sonnet-4-6` | `true` |
| Gemini 2.0 / 2.5 | `openai/gemini-2.5-pro` | `true` |
| Llama 3.x | `openai/llama-3-70b` | `true` |
| Mistral Large | `openai/mistral-large` | `true` |

All model names use the LiteLLM `provider/model` prefix convention.
When connecting directly to an OpenAI-compatible endpoint without
LiteLLM, use the raw model name (e.g. `gpt-4o`).

### Design Choices for Multi-LLM Reliability

- **ASCII-only source files** -- all Python, YAML, and Markdown
  files contain only ASCII characters to avoid encoding issues
  across different LLM tokenizers
- **English tool docstrings** -- tool descriptions use English for
  reliable parameter extraction across all models
- **Explicit tool selection rules** -- prevent the LLM from choosing
  the wrong tool or answering from memory
- **Retry instruction** (rule 4) -- handles transient scraping
  failures gracefully without surfacing errors to the user
- **Proactive alternative suggestion** (rule 5) -- stated as an
  unconditional rule so even passive models will act on it
- **Pydantic v2 `model_dump(mode="json")`** -- ensures all
  structured data is JSON-serializable regardless of the LLM's
  serialization preferences

---

## Inter-Agent Communication

The Price Comparison Agent is designed to work with other agents in
the Solace Agent Mesh. Every tool response includes a `structured`
field containing JSON-serializable data.

### Data Flow Examples

**EAN Search Agent to Price Comparison Agent:**

```text
1. User asks: "Find prices for the Bosch cordless drill"
2. Orchestrator routes to EAN Search Agent
3. EAN Search Agent returns: { "ean": "4006381333931", "name": "..." }
4. Orchestrator routes EAN to Price Comparison Agent
5. Price Comparison Agent returns prices with structured JSON
```

**Contract DB Agent to Price Comparison Agent:**

```text
1. User asks: "Is our contract price for item X still competitive?"
2. Orchestrator routes to Contract DB Agent
3. Contract DB Agent returns: { "price": 45.99, "supplier": "..." }
4. Orchestrator routes to Price Comparison Agent with contract data
5. Price Comparison Agent returns market comparison with recommendation
```

### Configuration

Inter-agent communication is configured in the ConfigMap:

```yaml
inter_agent_communication:
  allow_list: ["*"]           # Accept requests from all agents
  request_timeout_seconds: 120  # Timeout for inter-agent requests
```

The timeout is set to 120 seconds to accommodate batch operations
that may involve multiple sequential tool calls.

---

## Scraping Notes

Idealo and Geizhals do not offer a public API. The agent uses
asynchronous HTTP scraping via `httpx` with `beautifulsoup4`. This
approach has the following considerations:

### Bot-Protection Blocking

Platforms may block repeated requests from a single IP. For
production deployments, configure a rotating residential proxy
service via the `SCRAPER_PROXY` environment variable:

```bash
export SCRAPER_PROXY="http://user:pass@proxy-host:port"
```

### Selector Drift

If a platform redesigns its HTML, the CSS selectors in the
respective scraper files may need updating:

- `src/price_comparison_agent/scrapers/idealo.py`
- `src/price_comparison_agent/scrapers/geizhals.py`

Selectors use multiple fallback patterns to reduce breakage
frequency. Monitor agent logs for parsing warnings.

### Google Shopping

A [SerpAPI][serpapi] key (`SERPAPI_KEY`) provides structured JSON
responses and is the recommended approach for reliable production
use. Without a key the agent falls back to direct HTML scraping
of Google Shopping, which is less stable.

### Result Caching

All scrapers cache results for 5 minutes (configurable via
`cache_ttl` in the tool configuration). This avoids redundant
HTTP requests during:

- Batch searches where multiple items hit the same scraper
- Sequential tool calls within the same conversation turn
- Retry attempts on partially failed searches

The cache is per-scraper-instance and uses `time.monotonic()` for
TTL tracking. Set `cache_ttl: 0` to disable caching.

### Retry Logic

The base scraper automatically retries failed HTTP requests with
exponential back-off:

- **Retried status codes:** 429 (rate limit), 503 (service unavailable)
- **Retried exceptions:** connection errors, timeouts
- **Back-off formula:** `2^attempt` seconds (1s, 2s, 4s)
- **Maximum retries:** 2 (configurable via `max_retries`)

### Concurrency Control

Batch searches use an `asyncio.Semaphore(5)` to limit concurrent
scraper operations, preventing IP blocks from sudden request bursts
when processing large tender documents.

---

## Troubleshooting

### Agent does not register with the broker

- Verify `SOLACE_BROKER_URL`, `SOLACE_BROKER_VPN`, and credentials
- Check that the Solace broker is reachable from the agent pod/container
- Look for connection errors in agent logs

### No prices found for a product

- Check if the product is available on Idealo, Geizhals, or Google
  Shopping by searching manually
- If using a proxy, verify it is working correctly
- Check for scraper errors in agent logs (CSS selector drift)
- Try broadening the search query (shorter product name)

### Slow batch searches

- Large batches (20+ items) may take 30-60 seconds
- The concurrency limiter (5 concurrent items) prevents IP blocks
  but adds sequential processing time
- Consider increasing `cache_ttl` if items overlap across searches
- Enable SerpAPI for faster Google Shopping results

### CSV export is empty

- Ensure S3-compatible storage is configured and accessible
- Check `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, and credentials
- Verify the `artifact_management` tool group is included in the
  ConfigMap (it is by default)

### LLM does not call the right tool

- Verify the instruction in the ConfigMap matches the current version
- Check that tool docstrings are in English (ASCII-only)
- For GPT models, set `ENABLE_EMBED_RESOLUTION=false` to reduce
  prompt size and improve tool selection accuracy

---

## License

MIT

[sam]: https://solacelabs.github.io/solace-agent-mesh/
[serpapi]: https://serpapi.com
