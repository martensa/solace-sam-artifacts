# EAN Search Agent for Solace Agent Mesh

A procurement-focused agent that searches EAN/UPC/GTIN barcode databases to
find all possible barcodes for a product name or description. Built for the
[Solace Agent Mesh](https://solace.com/products/portal/agent-mesh/) using the
MCP (Model Context Protocol) over stdio.

## Supported Database Backends

The backend is selectable via the `EAN_DATABASE_BACKEND` environment variable:

| Backend | Database | Coverage | Cost | API Key |
| --- | --- | --- | --- | --- |
| `upcitemdb` (default) | [UPCitemdb](https://www.upcitemdb.com/) | 687M+ products | Free: 100 req/day | Not required |
| `ean_search` | [ean-search.org](https://www.ean-search.org/) | 1B+ barcodes | Free to Gold tier | Required |

## Use Case

In procurement, teams often need to identify the correct EAN barcode(s) for a
product before placing orders. A single product name like "A4 printer paper"
can map to dozens of EANs across manufacturers, regions, and package sizes.
This agent automates that lookup by searching broadly, deduplicating results,
and presenting all options with category and country metadata so procurement
teams can make informed decisions.

## Architecture

```text
+--------------------------------------------------------------+
|  Solace Agent Mesh                                           |
|                                                              |
|  +---------------+    stdio       +--------------------+     |
|  |  SAM Runtime  |<-------------->|  EAN Search        |     |
|  |  (LLM Agent)  |  MCP JSON-    |  MCP Server v1.0   |     |
|  |               |  RPC 2.0      |  (Python)          |     |
|  +-------+-------+               +------+-------------+     |
|          |                              |                    |
|          | Solace PubSub+               | HTTPS              |
|          v                              v                    |
|  +---------------+        +--------------------------+       |
|  | Event Broker  |        | Backend (configurable):  |       |
|  +---------------+        |  - api.ean-search.org    |       |
|                           |  - api.upcitemdb.com     |       |
|  Local lookups:           +--------------------------+       |
|  +--------------------------------------------------+       |
|  | EAN checksum validation + GS1 country prefix      |       |
|  +--------------------------------------------------+       |
+--------------------------------------------------------------+
```

The agent runs as a Kubernetes pod containing:

- **SAM Runtime** -- the Solace Agent Mesh framework that handles LLM
  orchestration, broker connectivity, and agent discovery
- **EAN Search MCP Server** -- a Python process communicating over stdio
  that wraps the selected barcode API as MCP tools, plus local EAN
  validation and country lookup

## File Structure

```text
.
|-- src/
|   +-- ean_search_mcp_server.py           # Unified MCP server (Python)
|-- deploy/
|   |-- sam-ean-search-agent-config.yaml   # Kubernetes ConfigMap
|   |-- sam-ean-search-agent-deployment.yaml # Kubernetes Deployment
|   +-- sam-ean-search-agent-secret.yaml   # Kubernetes Secret (credentials)
|-- Dockerfile                             # Docker image definition
|-- requirements.txt                       # Python dependencies (requests)
|-- .gitignore                             # Git ignore rules
|-- .dockerignore                          # Docker build context exclusions
+-- README.md                              # This file
```

| Directory | Contents |
| --- | --- |
| `src/` | Python source code for the MCP server |
| `deploy/` | Kubernetes manifests (ConfigMap, Deployment, Secret) |
| Root | Dockerfile, requirements.txt, README |

## MCP Tools

### Tools Available with Both Backends

| Tool | Type | Description |
| --- | --- | --- |
| `ean_product_search` | API call | Search by product name. Auto-paginates all results. Primary tool. |
| `ean_category_search` | API call | Search by product name filtered to a category. |
| `ean_barcode_lookup` | API call | Look up a specific EAN/UPC/GTIN to identify the product. |
| `ean_verify_checksum` | Local | Validate the check digit of an EAN/UPC/GTIN barcode. No API call. |
| `ean_issuing_country` | Local | GS1 registration country for a barcode (not manufacturing origin). No API call. |

### Additional Tools with ean_search Backend Only

| Tool | Type | Description |
| --- | --- | --- |
| `ean_similar_product_search` | API call | Fuzzy name matching for approximate or misspelled names. |
| `ean_barcode_prefix_search` | API call | Search by barcode prefix to find all manufacturer products. |

### Backend Feature Comparison

| Feature | `upcitemdb` | `ean_search` |
| --- | --- | --- |
| Brand filter on search | Yes | No |
| Language filter | No | Yes (10 languages) |
| Fuzzy/similar search | No | Yes |
| Manufacturer prefix search | No | Yes |
| Price range data | Yes | No |
| Local checksum validation | Yes | Yes |
| Local country lookup | Yes | Yes |

Tools marked **Local** do not consume API quota and can be called unlimited
times.

## Prerequisites

- Solace Agent Mesh Enterprise base image
  (`solace-agent-mesh-enterprise:latest`) in your container registry
- A Solace PubSub+ Event Broker (local or cloud)
- An LLM service endpoint (e.g. LiteLLM proxy for OpenAI, Gemini, or Claude)
- A Kubernetes cluster with the `sam-ent-k8s-agents` namespace
- **For `ean_search` backend only:** an API token from
  [ean-search.org](https://www.ean-search.org/ean-database-api.html)

No API key or sign-up is needed for the `upcitemdb` free tier (default).

### API Plans

**ean-search.org** (`ean_search`):

| Plan | Queries/Month | Price |
| --- | --- | --- |
| Trial | 100 | Free |
| Pro | 5,000 | 19 EUR/month |
| Bronze | 50,000 | Contact |
| Silver | 150,000 | Contact |
| Gold | 300,000 | Contact |

**UPCitemdb** (`upcitemdb`):

| Plan | Requests/Day | Price | Sign-up |
| --- | --- | --- | --- |
| Explorer (free) | 100 | Free | Not required |
| DEV | 20,000 lookup / 2,000 search | $99/month | Required |
| PRO | 150,000 lookup / 20,000 search | $699/month | Required |

## Configuration

All configuration is done via environment variables, set in the Kubernetes
Secret (`deploy/sam-ean-search-agent-secret.yaml`).

### Required Variables

| Variable | Description |
| --- | --- |
| `EAN_DATABASE_BACKEND` | `upcitemdb` (default) or `ean_search` |
| `LLM_SERVICE_ENDPOINT` | LLM service URL (e.g. LiteLLM proxy) |
| `LLM_SERVICE_API_KEY` | LLM service API key |
| `LLM_SERVICE_GENERAL_MODEL_NAME` | LLM model identifier (e.g. `openai/claude-sonnet-4-6`) |
| `SOLACE_BROKER_URL` | Solace broker WebSocket URL |

### Required for ean_search Backend Only

| Variable | Description |
| --- | --- |
| `EAN_SEARCH_API_TOKEN` | Your ean-search.org API token |

### Optional Variables (ean-search.org)

| Variable | Default | Description |
| --- | --- | --- |
| `EAN_SEARCH_API_BASE` | `https://api.ean-search.org/api` | API base URL |
| `EAN_SEARCH_LANGUAGE` | `1` | Language filter (1=English, 99=any) |

### Optional Variables (UPCitemdb)

| Variable | Default | Description |
| --- | --- | --- |
| `UPCITEMDB_API_BASE` | `https://api.upcitemdb.com/prod/trial` | API base URL. Use `/prod/v1` for paid plans. |
| `UPCITEMDB_API_KEY` | *(empty)* | API key for paid plans. Leave empty for free tier. |

### Optional Variables (Shared)

| Variable | Default | Description |
| --- | --- | --- |
| `EAN_SEARCH_MAX_PAGES` | `10` (ean_search) / `5` (upcitemdb) | Max pages to auto-paginate per search |
| `EAN_SEARCH_TIMEOUT` | `30` | HTTP request timeout in seconds |
| `EAN_SEARCH_MIN_INTERVAL` | `0.5` (ean_search) / `1.0` (upcitemdb) | Min seconds between API requests |
| `EAN_SEARCH_MAX_RETRIES` | `1` | Retries on transient 5xx / connection errors (0 = no retries) |
| `EAN_SEARCH_RETRY_BACKOFF` | `2.0` | Base backoff in seconds between retries |
| `MCP_MAX_RESPONSE_CHARS` | `25000` | Hard character cap on responses (use `10000` for GPT) |
| `MCP_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `MCP_LOG_FILE` | `ean_search_mcp.log` | Log file path |

### Language Codes (ean_search Backend Only)

| Code | Language |
| --- | --- |
| 1 | English |
| 2 | French |
| 3 | German |
| 4 | Spanish |
| 5 | Portuguese |
| 6 | Italian |
| 7 | Dutch |
| 8 | Polish |
| 9 | Swedish |
| 10 | Turkish |
| 99 | Any language |

## Deployment

### Step 1: Choose Your Backend

**Option A -- UPCitemdb (free, no sign-up):**

Edit `deploy/sam-ean-search-agent-secret.yaml`:

```yaml
EAN_DATABASE_BACKEND: "upcitemdb"
```

No API key needed. Works out of the box.

**Option B -- ean-search.org (paid, more features):**

Edit `deploy/sam-ean-search-agent-secret.yaml`:

```yaml
EAN_DATABASE_BACKEND: "ean_search"
EAN_SEARCH_API_TOKEN: "your-ean-search-token"
```

### Step 2: Configure Credentials

Edit `deploy/sam-ean-search-agent-secret.yaml` and set your
environment-specific values:

```yaml
LLM_SERVICE_API_KEY: "your-llm-api-key"
SOLACE_BROKER_URL: "ws://your-broker:8008"
```

Adjust the LLM model, broker settings, and S3 configuration to match your
environment.

### Step 3: Build and Push the Docker Image

```bash
docker build -t localhost:5000/sam-ean-search-agent:1.0.0 .
docker push localhost:5000/sam-ean-search-agent:1.0.0
```

### Step 4: Deploy to Kubernetes

```bash
kubectl apply -f deploy/sam-ean-search-agent-secret.yaml
kubectl apply -f deploy/sam-ean-search-agent-config.yaml
kubectl apply -f deploy/sam-ean-search-agent-deployment.yaml
```

### Step 5: Verify

```bash
# Check pod status
kubectl -n sam-ent-k8s-agents get pods -l app=sam-custom-agents

# Check logs
kubectl -n sam-ent-k8s-agents logs deployment/sam-ean-search-agent

# Check MCP server log inside the container
kubectl -n sam-ent-k8s-agents exec deployment/sam-ean-search-agent -- \
  cat /opt/ean-search-mcp-server/ean_search_mcp_server.log
```

The agent registers itself via agent discovery and will appear in the Solace
Agent Mesh UI once running.

## Switching Backends

To switch from UPCitemdb to ean-search.org (or vice versa):

1. Update the secret:

   ```yaml
   EAN_DATABASE_BACKEND: "ean_search"
   EAN_SEARCH_API_TOKEN: "your-token-here"
   ```

2. Apply and restart:

   ```bash
   kubectl apply -f deploy/sam-ean-search-agent-secret.yaml
   kubectl -n sam-ent-k8s-agents rollout restart deployment/sam-ean-search-agent
   ```

The MCP server dynamically adjusts the available tools based on the selected
backend. No code or config changes needed beyond the secret.

## Example Interactions

**Find EANs for a product:**

> "Find all EAN numbers for Coca Cola 330ml cans"

The agent will:

1. Run `ean_product_search` with the product name
2. If using `ean_search`, follow up with `ean_similar_product_search`
3. Deduplicate and present all results in a table

**Verify a barcode:**

> "Is 5449000000996 a valid EAN? What product is it?"

The agent will:

1. Run `ean_verify_checksum` to validate the check digit (local)
2. Run `ean_barcode_lookup` to identify the product
3. Run `ean_issuing_country` to find the GS1 registration country (local)

**Search with brand filter (upcitemdb):**

> "What are the EAN codes for Apple AirPods?"

The agent will run `ean_product_search` with name="AirPods" and brand="Apple".

**Search by manufacturer prefix (ean_search):**

> "Show me all products with EAN prefix 4006381"

The agent will run `ean_barcode_prefix_search` with the prefix.

**Narrow by category:**

> "Find all office paper products in the Office Supplies category"

The agent will run `ean_category_search` with the category and keyword.

## Agent Behavior

The LLM agent is configured with procurement-specific instructions:

- **Search broadly first** -- starts with product name search, then retries
  with shorter or broader terms if results are sparse
- **Multi-strategy search** -- tries brand filters, category filters, and
  every available search tool before giving up
- **Presents all options** -- results displayed in markdown tables with EAN,
  product name, category, and additional metadata; grouped by brand or
  category when more than 15 results
- **Enriches with local data** -- adds GS1 registration country (clarifying
  it is where the barcode was registered, not where the product was made)
  and validates checksums without consuming API quota
- **Handles truncation** -- when results are capped by the character limit,
  informs the user and suggests narrowing the search
- **Surfaces errors** -- shows actionable hints from tool errors (e.g.
  quota exceeded, invalid token) instead of generic failure messages
- **Real data only** -- never fabricates EAN numbers; never passes a product
  name to the barcode lookup tool (which requires a numeric code)

## Agent Discovery

The agent publishes its capabilities via the Solace Agent Mesh agent card:

- **Agent name:** `EANSearchAgent`
- **Display name:** EAN Search Agent
- **Skills:** Product EAN Search, Category Search, Barcode Lookup, EAN
  Validation, Artifact Management
- **Inter-agent communication:** enabled (allows other agents to request EAN
  lookups)
- **Discovery interval:** every 10 seconds

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| "API token may be invalid" | Wrong/missing `EAN_SEARCH_API_TOKEN` | Verify token at ean-search.org |
| "API quota may be exceeded" | Monthly limit reached (ean_search) | Upgrade plan or reduce `EAN_SEARCH_MAX_PAGES` |
| "Rate limit exceeded" | Free tier exhausted (upcitemdb) | Wait until next day or upgrade |
| Empty search results | Query too specific | Try shorter terms, remove brand filter |
| Truncated responses | Exceeds `MCP_MAX_RESPONSE_CHARS` | Increase cap or narrow search |
| Pod crash loop | Missing env vars or broker down | Check `kubectl logs` and secret values |
| MCP server not starting | Invalid `EAN_DATABASE_BACKEND` | Must be `ean_search` or `upcitemdb` |

## LLM Compatibility

The agent works with any LLM supported by the Solace Agent Mesh:

| Model | Status | Notes |
| --- | --- | --- |
| Claude 3.5+ / Claude 4 | Recommended | Handles all tools well, large context window |
| Gemini 2.5 Pro | Recommended | Large context, handles big result sets |
| GPT-4o / GPT-5 | Supported | Set `MCP_MAX_RESPONSE_CHARS` to `10000` |
| Llama 3.1 70B+ | Supported | May need lower `MCP_MAX_RESPONSE_CHARS` |
| Mistral Large | Supported | Works for basic searches |

## Upgrading UPCitemdb to a Paid Plan

1. Sign up at [devs.upcitemdb.com](https://devs.upcitemdb.com/)
2. Update the Kubernetes secret:

   ```yaml
   UPCITEMDB_API_KEY: "your-paid-api-key"
   UPCITEMDB_API_BASE: "https://api.upcitemdb.com/prod/v1"
   EAN_SEARCH_MAX_PAGES: "20"
   EAN_SEARCH_MIN_INTERVAL: "0.5"
   ```

3. Apply and restart:

   ```bash
   kubectl apply -f deploy/sam-ean-search-agent-secret.yaml
   kubectl -n sam-ent-k8s-agents rollout restart deployment/sam-ean-search-agent
   ```

## MCP Protocol Reference

The MCP server implements the
[Model Context Protocol](https://modelcontextprotocol.io/) (JSON-RPC 2.0 over
stdio) with these methods:

| Method | Description |
| --- | --- |
| `initialize` | Returns server info and capabilities |
| `tools/list` | Returns tool definitions (varies by backend) |
| `tools/call` | Executes a tool and returns results |

All tool responses are compact JSON (no indentation) to minimize LLM token
usage. For UPCitemdb, response fields are trimmed to essential data (images,
offers, and URLs are stripped) to save tokens.

## License

Internal use. The ean-search.org API is a commercial service -- see their
[terms](https://www.ean-search.org/ean-database-api.html). The UPCitemdb free
tier requires no sign-up -- paid plans are governed by
[UPCitemdb terms](https://devs.upcitemdb.com/).
