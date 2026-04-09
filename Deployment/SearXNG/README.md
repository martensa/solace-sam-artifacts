# SearXNG -- Shared Meta-Search Engine

A self-hosted [SearXNG](https://github.com/searxng/searxng) instance deployed as
a shared Kubernetes service. Aggregates results from Google, Bing, and DuckDuckGo
without requiring API keys. Used by SAM agents for web search (product lookup,
article verification, general research).

## Architecture

```text
+-----------------------------------------------------+
|  sam-ent-k8s namespace                               |
|                                                      |
|  +----------------+       +----------------------+   |
|  | SearXNG Pod    |       | Upstream Engines     |   |
|  |                | HTTPS | - Google (weight 1.2) |   |
|  | searxng:8080   |------>| - Google DE (1.1)    |   |
|  | (ClusterIP)    |       | - Bing (1.0)         |   |
|  |                |       | - DuckDuckGo (0.8)   |   |
|  +-------+--------+       +----------------------+   |
|          ^                                           |
|          | HTTP (cluster-internal)                    |
|          |                                           |
+-----------------------------------------------------+
           |
  +--------+--------------------------------------------+
  |  sam-ent-k8s-agents namespace                       |
  |                                                     |
  |  +----------------------------+                     |
  |  | Article Verification Agent |                     |
  |  | (MCP server)               |                     |
  |  +----------------------------+                     |
  |                                                     |
  |  +----------------------------+                     |
  |  | Any other agent needing    |                     |
  |  | web search                 |                     |
  |  +----------------------------+                     |
  +-----------------------------------------------------+
```

## Service Details

| Property | Value |
|----------|-------|
| Namespace | `sam-ent-k8s` |
| Service name | `searxng` |
| Service type | ClusterIP |
| Port | 8080 |
| Internal URL | `http://searxng.sam-ent-k8s.svc.cluster.local:8080` |
| Image | `searxng/searxng:latest` |
| Health check | `GET /healthz` |

## Search Engines

| Engine | Shortcut | Weight | Notes |
|--------|----------|--------|-------|
| Google | `g` | 1.2 | Primary, best quality |
| Google DE | `gde` | 1.1 | German results for B2B product search |
| Bing | `b` | 1.0 | Good secondary |
| DuckDuckGo | `ddg` | 0.8 | Fallback |

## Configuration

All configuration is in `searxng-configmap.yaml`. Key settings:

| Setting | Value | Reason |
|---------|-------|--------|
| `limiter` | `false` | Internal use only, no rate limiting needed |
| `default_lang` | `de` | German market (B2B electrical products) |
| `formats` | `html, json` | JSON format required for programmatic access |
| `safe_search` | `0` | Disabled for product search accuracy |
| `request_timeout` | `8s` | Per upstream engine timeout |
| `pool_connections` | `100` | Connection pool for concurrent requests |

## JSON API

Agents access SearXNG via its JSON API:

```bash
curl "http://searxng.sam-ent-k8s.svc.cluster.local:8080/search?q=50140.2K4&format=json"
```

Response structure:

```json
{
  "results": [
    {
      "title": "BEGA 50140.2K4 Wandleuchte",
      "url": "https://example.com/product/50140.2K4",
      "content": "BEGA Wandleuchte fuer den Innenbereich..."
    }
  ]
}
```

Typically returns 10-60 results per query (aggregated across all engines).

## File Structure

```text
Deployment/SearXNG/
|-- searxng-configmap.yaml   # SearXNG settings.yml as ConfigMap
|-- searxng-deployment.yaml  # Deployment + ClusterIP Service
+-- README.md                # This file
```

## Deployment

```bash
kubectl apply -f searxng-configmap.yaml
kubectl apply -f searxng-deployment.yaml
```

Verify:

```bash
# Check pod status
kubectl -n sam-ent-k8s get pods -l app=searxng

# Test from inside the cluster
kubectl -n sam-ent-k8s exec deployment/searxng -- \
  wget -qO- "http://localhost:8080/search?q=test&format=json" | head -200

# Test from an agent pod
kubectl -n sam-ent-k8s-agents exec deployment/sam-article-verification-agent -- \
  python3 -c "
from urllib.request import urlopen
import json
r = urlopen('http://searxng.sam-ent-k8s.svc.cluster.local:8080/search?q=test&format=json')
data = json.loads(r.read())
print(f'{len(data[\"results\"])} results')
"
```

## Known Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `SEARXNG_PORT` collision | K8s injects a service env var `SEARXNG_PORT=tcp://...` that overrides the port config | Explicit `SEARXNG_PORT: "8080"` env var in the Deployment |
| `chown: Read-only file system` | ConfigMap volume is read-only, SearXNG tries to write to `/etc/searxng/` | initContainer copies config from ConfigMap to a writable emptyDir volume |
| No results from Google | Google may block requests from datacenter IPs | SearXNG aggregates multiple engines; Bing and DDG provide fallback |

## Resource Usage

| Resource | Request | Limit |
|----------|---------|-------|
| CPU | 50m | 500m |
| Memory | 128Mi | 512Mi |

SearXNG is lightweight. A single replica handles the expected load (hundreds of
queries per day across all agents).

## Consuming Agents

Any agent can use SearXNG by making HTTP requests to the service URL. Currently
used by:

- **Article Verification Agent** -- product identification via `search_article` MCP tool
- **Web Research Agent** -- general web research (Agent Builder, uses `web_request`)

To use from a new agent, set the environment variable:
```yaml
SEARXNG_URL: "http://searxng.sam-ent-k8s.svc.cluster.local:8080"
```

Or use the URL directly in HTTP requests with `?format=json` parameter.
