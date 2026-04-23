# Price Comparison Agent

Production-grade price comparison agent for Solace Agent Mesh (SAM),
built for **B2B procurement** as the primary use case (consumer queries
are handled as well). Combines SearXNG meta-search, optional Google
Shopping (SerpAPI), and a Playwright headless browser with stealth mode
to extract accurate prices from 70+ trusted German / European retail
and distributor sites.

Core quality features:

- Trust-anchored outlier detection (trusted-domain median + MAD)
- Domain diversity enforcement at fetch time and in the final output
- URL-path heuristics (product pages > category pages)
- Manufacturer-domain deprioritization (avoids price-less spec pages)
- Noise filtering in generic CSS extraction (UVP / "ab X" / shipping)
- Product-match confidence scoring (query vs page title)
- German B2B distributors covered (Sonepar, Rexel, RS, Farnell,
  Mercateo, Conrad, Reichelt, Voelkner, Industry-Electronics, ...)

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
| Image | `registry.solace.lab/sam-price-comparison-agent:2.1.0` |
| agent_name | `PriceComparisonAgent` |
| display_name | `Price Comparison Agent` |
| Model | `openai/claude-sonnet-4-6` (via LiteLLM) |

## Search Pipeline

```text
search_product_prices("Niedax WRL 200.400 F")
  |
  +-- Phase 1 (discovery, 0-5s):
  |     SearXNG (+ SerpAPI if key present) -- parallel
  |
  +-- Phase 2 (ranking, 0-1s):
  |     - Domain base score (_PRICE_SITE_SCORES, ~72 domains)
  |     - Manufacturer penalty (-40 for niedax.de etc.)
  |     - URL-path bonus (+8 /produkt/ etc.) / penalty (-15 /kategorie/)
  |     - Query-token-in-URL bonus (+10)
  |     - Blacklist (-1) for archived / sold / forum URLs
  |     - Deduplicate and sort by score
  |     - Enforce max 1 URL per domain -> top 10 diverse URLs
  |
  +-- Phase 3 (fetch, 2-60s):
  |     Playwright (4 concurrent), 18s per URL
  |     Layered extraction:
  |       1. Site-specific CSS (Idealo, Geizhals, Amazon ...)
  |       2. JSON-LD / Schema.org Product markup
  |       3. OpenGraph / Microdata
  |       4. Generic CSS (1 primary price per page, noise-filtered)
  |       5. Regex on visible text (1 primary price per page)
  |     + capture page title for match-confidence
  |
  +-- Phase 4 (aggregate, 0-1s):
        - Deduplicate by merchant+price
        - Sort by total price (asc)
        - Enforce max 4 offers per domain (diversity)
        - Flag outliers (trust-anchor median + MAD)
        - Compute insights (raw + filtered)
```

Total budget: **75 seconds**. Quality over speed.

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
| Tool call budget | Hard limit: 10 LLM calls (4 tool calls + responses) |
| Search backend | SearXNG JSON API (Google, Bing, DuckDuckGo aggregated) |
| Detail pages | Playwright stealth browser (4 concurrent) |
| SerpAPI | Optional (only if PRICE_SERPAPI_KEY is set) |
| B2B detection | Skips Nettoartikel, responds immediately |
| Quick Search | fetch_details=false, 3-5 seconds |
| Deep Search | fetch_details=true, up to 75 seconds |
| Batch mode | Up to 10 items, fetch_details=false by default |
| Cache | 1800s (30 min) TTL -- B2B prices change slowly |

## Outlier Detection (Trust-Anchor)

Prices are flagged with `is_outlier=true` when they deviate strongly
from the market. The backend computes two thresholds:

1. **Ratio window** around anchor: < 20% or > 500% of anchor is flagged.
2. **MAD-based** (Median Absolute Deviation): anything more than 10x
   MAD from anchor is flagged even within the ratio window. Handles
   tight markets where 20%-500% is too wide.

The anchor is the median of prices from TRUSTED_PRICE_DOMAINS (~70
curated distributors + aggregators). If no trusted price is present,
falls back to the overall median. Trusted offers that drift outside
the thresholds are flagged with a softer "stray sub-listing" reason.

Why it matters for B2B: industrial articles often return many
misleading per-unit / per-meter prices from less-known shops that
would poison a plain median. Anchoring on trusted-domain prices
(industry-electronics, sonepar, rexel, mercateo, ...) keeps the
reference price realistic.

## Portal Coverage (72 domains, B2B-first)

| Tier | Coverage |
|------|----------|
| Consumer aggregators | idealo, geizhals, billiger, guenstiger, preis, preisvergleich |
| DE electrical wholesale (CRITICAL) | sonepar, rexel, fega, eibmarkt |
| Industrial electronics | conrad, reichelt, voelkner, rs-online, farnell, digikey, mouser, distrelec, buerklin, rutronik24, tme, elv |
| B2B / MRO | mercateo, industry-electronics, voltus, elektro4000, contorion, kaiser-kraft, schaefer-shop, svh24, hoffmann-group, wuerth, haberkorn, expondo, toolineo, berner |
| B2B IT | bechtle, jacob, cyberport, future-x, computeruniverse |
| Workwear / PPE | engelbert-strauss, mewa, arbeitsschutz-express |
| Consumer retailers | amazon, otto, mediamarkt, saturn, notebooksbilliger, alternate |
| DIY / building | bauhaus, hornbach, obi, hagebau, toom |
| B2B marketplaces | wer-liefert-was (wlw), europages |
| Marketplaces (lower weight) | ebay, kleinanzeigen |
| Deprioritized | 52 manufacturer domains (niedax, bosch, hager, siemens, abb, ...) |

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
docker build -t registry.solace.lab/sam-price-comparison-agent:2.1.0 .
docker push registry.solace.lab/sam-price-comparison-agent:2.1.0

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
| 2026-04-21 | v2.1.0: B2B production hardening |
| 2026-04-21 |   + Expanded portal coverage 33 → 72 domains (Sonepar, Rexel, RS, Bechtle, ...) |
| 2026-04-21 |   + URL-path heuristics (product vs category) |
| 2026-04-21 |   + Manufacturer-domain deprioritization (52 brands) |
| 2026-04-21 |   + URL blacklist (archive/sold/forum patterns) |
| 2026-04-21 |   + Noise filtering in generic CSS extractor (UVP / "ab X" / strike-through) |
| 2026-04-21 |   + Product-match confidence scoring (page title vs query) |
| 2026-04-21 |   + MAD-based secondary outlier threshold |
| 2026-04-21 |   + Trust-anchor outlier detection with full domain coverage |
| 2026-04-21 |   + Per-domain caps (fetch: 1, output: 4) for diversity |
| 2026-04-21 |   + CSV export with outlier markers + filtered statistics |
| 2026-04-21 |   + Quality-first parameters: 75s total / 10 URLs / 18s detail / 30min cache |
| 2026-04-21 |   + B2B-focused agent instruction (4 tool calls ceiling) |
| 2026-04-10 | v2.0.0: Complete rewrite as MCP server |
| 2026-04-10 |   + Replaced httpx scrapers with SearXNG + Playwright |
| 2026-04-10 |   + Added SerpAPI as optional Google Shopping source |
| 2026-04-10 |   + Reduced tools from 6 to 3 (removed redundant wrappers) |
| 2026-04-10 |   + Added stealth browser (derived from Web Scraper Agent) |
| 2026-04-10 |   + Added site-specific + generic price extraction |
| 2026-04-10 |   + Added programmatic tool budget (max_llm_calls_per_task) |
| 2026-04-01 | v1.0.0: Initial deployment with httpx scrapers |
