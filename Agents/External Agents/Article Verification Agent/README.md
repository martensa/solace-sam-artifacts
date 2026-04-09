# Article Verification Agent for Solace Agent Mesh

A procurement-focused agent that verifies and enriches incomplete B2B product
article descriptions via web search. Built for the
[Solace Agent Mesh](https://solace.com/products/portal/agent-mesh/) using the
MCP (Model Context Protocol) over stdio.

## Use Case

In B2B procurement, article descriptions from suppliers are often abbreviated,
incomplete, or contain only an order code (e.g. `50140.2K4 Wandleuchte`).
Downstream processes -- EAN lookup, price comparison, image search -- need
accurate manufacturer names and full product designations.

This agent automates that verification by:

1. Pre-filtering non-product inputs (Nettoartikel, Bruttoartikel, pricing
   labels) to avoid unnecessary searches
2. Searching the web for the article code or description
3. Extracting the manufacturer and full product name from distributor listings
4. Returning a structured JSON result with confidence scoring

## Architecture

```text
+--------------------------------------------------------------+
|  Solace Agent Mesh                                           |
|                                                              |
|  +---------------+    stdio       +--------------------+     |
|  |  SAM Runtime  |<-------------->|  Article Verif.    |     |
|  |  (LLM Agent)  |  MCP JSON-    |  MCP Server v2.1   |     |
|  |               |  RPC 2.0      |  (Python)          |     |
|  +-------+-------+               +------+-------------+     |
|          |                              |                    |
|          | Solace PubSub+               | HTTP (cluster)     |
|          v                              v                    |
|  +---------------+        +--------------------------+       |
|  | Event Broker  |        | SearXNG (primary)        |       |
|  +---------------+        | Google+Bing+DDG agg.     |       |
|                           +--------------------------+       |
|                                    |  fallback               |
|                           +--------------------------+       |
|                           | DuckDuckGo HTML Search   |       |
|                           | (no API key required)    |       |
|                           +--------------------------+       |
|  Pre-filter (local):                                         |
|  +--------------------------------------------------+       |
|  | Regex-based detection of pricing labels,          |       |
|  | article code extraction, query optimization       |       |
|  +--------------------------------------------------+       |
+--------------------------------------------------------------+
```

The agent runs as a Kubernetes pod containing:

- **SAM Runtime** -- the Solace Agent Mesh framework that handles LLM
  orchestration, broker connectivity, and agent discovery
- **Article Verification MCP Server** -- a Python process communicating over
  stdio that provides pre-filtering and web search as MCP tools
- **SearXNG** -- a shared meta-search engine service in `sam-ent-k8s` namespace
  (see `Deployment/SearXNG/`)

## File Structure

```text
.
|-- src/
|   +-- article_verification_mcp_server.py  # MCP server (Python)
|-- deploy/
|   |-- sam-article-verification-agent-config.yaml      # Kubernetes ConfigMap
|   |-- sam-article-verification-agent-deployment.yaml   # Kubernetes Deployment
|   +-- sam-article-verification-agent-secret.yaml       # Kubernetes Secret
|-- Dockerfile                              # Docker image definition
|-- requirements.txt                        # Python dependencies
+-- README.md                              # This file
```

| Directory | Contents |
| --- | --- |
| `src/` | Python source code for the MCP server |
| `deploy/` | Kubernetes manifests (ConfigMap, Deployment, Secret) |
| Root | Dockerfile, requirements.txt, README |

## MCP Tools

| Tool | Type | Description |
| --- | --- | --- |
| `check_article` | Local | Pre-check an article description. Detects non-product inputs and returns `action=skip`. For real products, returns `action=search` with an optimized query and extracted article code. |
| `search_article` | Web | Search via SearXNG (primary) with DuckDuckGo fallback for a B2B product article. Returns titles, URLs, and snippets from electrical distributors. Titles typically show the manufacturer name first. |

### Workflow

1. LLM calls `check_article` with the raw article description
2. If `action=skip`: LLM returns an unspecific JSON result immediately
3. If `action=search`: LLM calls `search_article` with the suggested query
4. LLM analyzes search results to extract manufacturer, product name, article
   number, and category
5. LLM returns a structured JSON object

### Pre-Filter Patterns

The `check_article` tool detects these non-product input patterns:

| Pattern | Example | Reason |
| --- | --- | --- |
| Nettoartikel | `Norka Nettoartikel` | Net-price contract label |
| Nettoangebotspreise | `Trilux-Nettoangebotspreise` | Net-price offer label |
| BRUTTOARTIKEL | `BRUTTOARTIKEL` | Gross-price label |
| NLAG | `NLAG Sonderkondition` | NLAG pricing label |
| Brutto (no article number) | `Hoffmeister Brutto` | Generic gross-price category |

## Output Format

### Specific Product (Verified)

```json
{
  "manufacturer": "BEGA",
  "product_name": "BEGA 50140.2K4 Wandleuchte fuer den Innenbereich",
  "article_number": "50140.2K4",
  "category": "Wandleuchte",
  "confidence": "high",
  "is_specific": true,
  "original_input": "50140.2K4 Wandleuchte"
}
```

### Unspecific Input (Skipped)

```json
{
  "manufacturer": null,
  "product_name": null,
  "article_number": null,
  "category": null,
  "confidence": "none",
  "is_specific": false,
  "unspecific_reason": "Net-price contract label (Nettoartikel)",
  "original_input": "Norka Nettoartikel"
}
```

### Confidence Levels

| Level | Meaning |
| --- | --- |
| `high` | Multiple search results confirm the product and manufacturer |
| `medium` | Likely match but not 100% certain |
| `low` | Weak match, results are ambiguous |
| `none` | Not a specific product (pricing label, contract label, etc.) |

## Prerequisites

- Solace Agent Mesh Enterprise base image
  (`solace-agent-mesh-enterprise:1.97.2`) in your container registry
- A Solace PubSub+ Event Broker (local or cloud)
- An LLM service endpoint (e.g. LiteLLM proxy for Claude, GPT, or Gemini)
- A Kubernetes cluster with the `sam-ent-k8s-agents` namespace

No API key is required -- web search uses SearXNG (self-hosted meta-search
aggregating Google, Bing, and DuckDuckGo) with a DuckDuckGo HTML fallback.
SearXNG runs as a shared Kubernetes service in the `sam-ent-k8s` namespace.

## Configuration

All configuration is done via environment variables, set in the Kubernetes
Secret (`deploy/sam-article-verification-agent-secret.yaml`).

### Required Variables

| Variable | Description |
| --- | --- |
| `LLM_SERVICE_ENDPOINT` | LLM service URL (e.g. LiteLLM proxy) |
| `LLM_SERVICE_API_KEY` | LLM service API key |
| `LLM_SERVICE_GENERAL_MODEL_NAME` | LLM model identifier (e.g. `openai/claude-sonnet-4-6`) |
| `SOLACE_BROKER_URL` | Solace broker WebSocket URL |
| `NAMESPACE` | SAM namespace (e.g. `sam-ent-k8s`) |

### Optional Variables

| Variable | Default | Description |
| --- | --- | --- |
| `MCP_LOG_LEVEL` | `INFO` | MCP server log level (DEBUG, INFO, WARNING, ERROR) |
| `MCP_LOG_FILE` | `article_verification_mcp.log` | MCP server log file path |
| `SEARXNG_URL` | `http://searxng.sam-ent-k8s.svc.cluster.local:8080` | SearXNG service URL |
| `SEARCH_MAX_RESULTS` | `10` | Maximum search results per query |

## Deployment

### Step 1: Configure Credentials

Edit `deploy/sam-article-verification-agent-secret.yaml` and set your
environment-specific values:

```yaml
LLM_SERVICE_API_KEY: "your-llm-api-key"
SOLACE_BROKER_URL: "ws://your-broker:8008"
```

### Step 2: Build and Push the Docker Image

```bash
docker build -t localhost:5000/sam-article-verification-agent:1.0.0 .
docker push localhost:5000/sam-article-verification-agent:1.0.0
```

### Step 3: Deploy to Kubernetes

```bash
kubectl apply -f deploy/sam-article-verification-agent-secret.yaml
kubectl apply -f deploy/sam-article-verification-agent-config.yaml
kubectl apply -f deploy/sam-article-verification-agent-deployment.yaml
```

### Step 4: Verify

```bash
# Check pod status
kubectl -n sam-ent-k8s-agents get pods | grep article

# Check logs
kubectl -n sam-ent-k8s-agents logs deployment/sam-article-verification-agent

# Check MCP server log inside the container
kubectl -n sam-ent-k8s-agents exec deployment/sam-article-verification-agent -- \
  cat /opt/article-verification-mcp/article_verification_mcp.log
```

## Agent Behavior

The LLM agent is configured with B2B procurement-specific instructions:

- **Speed-first** -- max 3 tool calls per request. Partial results are
  returned rather than making the user wait for additional searches
- **Broad search** -- searches for the article code and description without
  restrictive keywords like "Datenblatt". This returns more distributor
  hits that clearly show manufacturer and product details
- **Manufacturer extraction** -- analyzes search result titles from shops
  (elektronetshop.de, voltus.com, rexel.de, zajadacz.de) where the
  manufacturer name typically appears first
- **Pre-filtering** -- detects pricing labels (Nettoartikel, Bruttoartikel,
  NLAG) and skips them without wasting a web search
- **JSON-only output** -- always returns strict JSON, no markdown wrapping
- **Known brand mappings** -- hardcoded knowledge for common patterns
  (e.g. TRUSYS --> LEDVANCE)
- **Standalone operation** -- never calls peer agents, works independently

## Test Results

Tested with 8 representative B2B articles (April 2026, SearXNG backend):

| Input | Expected | Result | Confidence |
| --- | --- | --- | --- |
| `50140.2K4 Wandleuchte` | Bega | BEGA | high |
| `207500 FacilityServer` | Gira | Gira | high |
| `84589K3 Aufsatzleuchte Grafit 3000 K` | Bega | BEGA | high |
| `ASM-C6A G Anschlussmodul CAT 6A` | OBO Bettermann | OBO Bettermann | high |
| `TRUSYS UNIV P 73W 840 N CL PS` | LEDVANCE | LEDVANCE | high |
| `787-872 AKKU-MODUL 24V 7Ah` | WAGO | WAGO | high |
| `MEG6921-0001 KNX Stellantrieb` | Merten | Merten | high |
| `Norka Nettoartikel` | Skip | Skip | none |

**Score: 8/8 (100%)**

## Agent Discovery

The agent publishes its capabilities via the Solace Agent Mesh agent card:

- **Agent name:** `ArticleVerificationAgent`
- **Display name:** Article Verification Agent
- **Skills:** Article Verification
- **Inter-agent communication:** disabled (standalone agent)
- **Agent discovery:** disabled
- **Discovery interval:** every 10 seconds

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Empty search results | SearXNG or upstream engines unavailable | Check SearXNG pod status, verify service connectivity |
| Wrong manufacturer | Ambiguous article code | Add more descriptive text to the input |
| `action=skip` for valid product | Input matches a pre-filter pattern | Remove pricing keywords from the input |
| Pod crash loop (OOMKilled) | Insufficient memory | Ensure limits are at least 1536Mi |
| MCP server not starting | Missing `uv` or dependencies | Check Dockerfile builds correctly |

## LLM Compatibility

The agent works with any LLM supported by the Solace Agent Mesh:

| Model | Status | Notes |
| --- | --- | --- |
| Claude Sonnet 4.6 | Recommended | Tested, handles all tools well |
| Claude Haiku 4.5 | Supported | Faster, lower cost, suitable for this task |
| GPT-4o / GPT-5 | Supported | Works well with structured JSON output |
| Gemini 2.5 Pro | Supported | Large context window |

## MCP Protocol Reference

The MCP server implements the
[Model Context Protocol](https://modelcontextprotocol.io/) (JSON-RPC 2.0 over
stdio) with these methods:

| Method | Description |
| --- | --- |
| `initialize` | Returns server info and capabilities |
| `tools/list` | Returns tool definitions |
| `tools/call` | Executes a tool and returns results |

All tool responses are compact JSON (no indentation) to minimize LLM token
usage.

## License

Internal use.
