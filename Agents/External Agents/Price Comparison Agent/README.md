# Price Comparison Agent

Real-time price comparison agent for Solace Agent Mesh (SAM). Searches
market prices via SearXNG meta-search engine, optional Google Shopping
(SerpAPI), and Playwright headless browser with stealth mode for
accurate price extraction from German e-commerce sites.

## Architecture

```text
+-----------------------------------------------------+
|  sam-solace-lab-agents namespace                        |
|                                                      |
|  +-------------------------+                         |
|  | Price Comparison Agent  |                         |
|  | (MCP over stdio)        |                         |
|  |                         |                         |
|  | MCP: price-comparison   |                         |
|  |   search_product_prices |                         |
|  |   batch_search_prices   |                         |
|  |   export_comparison_rpt |                         |
|  | builtin: artifact_mgmt  |                         |
|  +-------+---------+-------+                         |
|          |         |                                  |
|          |    Playwright                              |
|          |    (stealth browser)                       |
|          |         |                                  |
|          | Solace  +---> Idealo, Geizhals, Amazon,    |
|          | PubSub+       Otto, MediaMarkt, ...        |
|          |                                           |
+-----------------------------------------------------+
           |                          |
           v                          v
+---------------------+   +----------------------+
| sam-solace-lab         |   | sam-solace-lab          |
| Orchestrator        |   | SearXNG Pod          |
| (agent card         |   | searxng:8080         |
|  discovery)         |   | (ClusterIP)          |
+---------------------+   +----------------------+
```

## Deployment Model

Manually deployed via kubectl manifests (ConfigMap, Secret,
Deployment). Uses a custom Docker image with Playwright Chromium.

| Property | Value |
|----------|-------|
| Namespace | `sam-solace-lab-agents` |
| Deployment | `sam-price-comparison-agent` |
| Image | `localhost:5000/sam-price-comparison-agent:2.0.0` |
| agent_name | `PriceComparisonAgent` |
| display_name | `Price Comparison Agent` |
| Model | `openai/claude-sonnet-4-6` (via LiteLLM) |

## Search Pipeline

```text
search_product_prices("Niedax WRL 200.400 F")
  |
  +-- [0-5s] Phase 1: SearXNG + SerpAPI (parallel)
  +-- [0-2s] Phase 2: URL ranking + deduplication
  +-- [2-45s] Phase 3: Playwright detail fetch (3 concurrent)
  +-- [0-2s] Phase 4: Aggregate + format
```

Total budget: 55 seconds. Detail pages have 15s timeout each.

## Tools

| Tool | Purpose |
|------|---------|
| `search_product_prices` | Default price search by EAN or product name |
| `batch_search_prices` | Multiple products (up to 10) at once |
| `export_comparison_report` | CSV export of results |

## Price Extraction

Layered extraction from Playwright-fetched pages:

1. Site-specific CSS selectors (Idealo, Geizhals, Amazon, Otto, ...)
2. JSON-LD / Schema.org Product markup
3. OpenGraph / Microdata meta tags
4. Generic CSS heuristics (.price, [data-price])
5. Regex on visible text (with EUR currency marker required)

## Key Behaviour

| Feature | Detail |
|---------|--------|
| Tool call budget | Hard limit: 8 LLM calls (programmatic) |
| Search backend | SearXNG JSON API for price discovery |
| Detail pages | Playwright stealth browser for accurate extraction |
| SerpAPI | Optional (only if PRICE_SERPAPI_KEY is set) |
| B2B detection | Skips Nettoartikel, responds immediately |
| Quick Search | fetch_details=false, 3-5 seconds |
| Deep Search | fetch_details=true, up to 55 seconds |
| Batch mode | Up to 10 items, fetch_details=false by default |
| Cache | 300s TTL for repeated queries |

## Environment Variables

| Variable | Value | Notes |
|----------|-------|-------|
| `LLM_SERVICE_GENERAL_MODEL_NAME` | `openai/claude-sonnet-4-6` | Sonnet |
| `LLM_SERVICE_ENDPOINT` | `https://lite-llm.mymaas.net` | LiteLLM |
| `LLM_SERVICE_API_KEY` | (secret) | Shared key |
| `PRICE_SEARXNG_URL` | `http://searxng....:8080` | SearXNG |
| `PRICE_SERPAPI_KEY` | (optional) | Google Shopping |
| `SOLACE_BROKER_URL` | `ws://host.docker.internal:8008` | Broker |
| `NAMESPACE` | `sam-solace-lab` | Mesh namespace |
| `S3_ENDPOINT_URL` | `http://agent-mesh-seaweedfs-0...:8333` | S3 |

## File Structure

```text
Agents/External Agents/Price Comparison Agent/
|-- deploy/
|   |-- sam-price-comparison-agent-config.yaml
|   |-- sam-price-comparison-agent-secret.yaml
|   +-- sam-price-comparison-agent-deployment.yaml
|-- src/
|   +-- price_comparison_mcp/
|       |-- server.py              # MCP JSON-RPC dispatcher
|       |-- browser_manager.py     # Playwright stealth
|       |-- config.py              # Browser + search config
|       |-- errors.py              # Structured error taxonomy
|       |-- response.py            # Response mode builder
|       |-- cache.py               # TTL cache
|       |-- price_extractor.py     # Price extraction from HTML
|       |-- searxng_client.py      # SearXNG HTTP client
|       |-- serpapi_client.py      # SerpAPI HTTP client
|       +-- tools/
|           |-- search_prices.py   # Main search pipeline
|           |-- batch_search.py    # Batch wrapper
|           +-- export_report.py   # CSV formatter
|-- Dockerfile
|-- pyproject.toml
|-- CLAUDE.md
+-- README.md
```

## Deployment

```bash
# Build Docker image
docker build -t localhost:5000/sam-price-comparison-agent:2.0.0 .
docker push localhost:5000/sam-price-comparison-agent:2.0.0

# Apply manifests
kubectl apply -f deploy/sam-price-comparison-agent-secret.yaml
kubectl apply -f deploy/sam-price-comparison-agent-config.yaml
kubectl apply -f deploy/sam-price-comparison-agent-deployment.yaml

# Verify
kubectl get pods -n sam-solace-lab-agents | grep price-comparison

# Check logs
kubectl logs deployment/sam-price-comparison-agent \
  -n sam-solace-lab-agents --tail=50
```

## Verification

Port-forward the gateway and send a test request:

```bash
kubectl port-forward svc/agent-mesh 8081:80 -n sam-solace-lab
```

```bash
curl -s -X POST http://localhost:8081/api/v1/message:send \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-pc-001",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "Finde Preise fuer Niedax WRL 200.400 F"
        }],
        "messageId": "msg-pc-001",
        "metadata": {
          "agent_name": "PriceComparisonAgent"
        }
      }
    }
  }'
```

**Success criteria:**

- Returns actual price offers from multiple merchants
- SearXNG results include inline prices
- Playwright fetches detail pages without being blocked
- Completes in less than 60 seconds
- B2B net-price items skipped correctly

## Changelog

| Date | Change |
|------|--------|
| 2026-04-10 | v2.0.0: Complete rewrite as MCP server |
| 2026-04-10 | Replaced httpx scrapers with SearXNG + Playwright |
| 2026-04-10 | Added SerpAPI as optional Google Shopping source |
| 2026-04-10 | Reduced tools from 6 to 3 (removed redundant wrappers) |
| 2026-04-10 | Added stealth browser (copied from Web Scraper Agent) |
| 2026-04-10 | Added site-specific + generic price extraction |
| 2026-04-10 | Added programmatic tool budget (max_llm_calls_per_task) |
| 2026-04-01 | v1.0.0: Initial deployment with httpx scrapers |
