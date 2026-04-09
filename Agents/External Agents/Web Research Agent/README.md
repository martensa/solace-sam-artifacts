# Web Research Agent

General-purpose web research agent for Solace Agent Mesh (SAM).
Searches the web through a self-hosted SearXNG meta-search engine
(aggregating Google, Bing, and DuckDuckGo) and delivers accurate,
cited answers. Serves as a fallback when specialized agents in the
mesh are unavailable or returned incomplete results.

## Architecture

```text
+-----------------------------------------------------+
|  sam-ent-k8s-agents namespace                        |
|                                                      |
|  +-------------------------+                         |
|  | Web Research Agent Pod  |                         |
|  | (manual deployment)     |                         |
|  |                         |                         |
|  | builtin: web            |                         |
|  |   (allow_loopback)      |                         |
|  | builtin: data_analysis  |                         |
|  | builtin: artifact_mgmt  |                         |
|  +-------+-----------------+                         |
|          |                                           |
|          | Solace PubSub+                             |
|          |                                           |
+-----------------------------------------------------+
           |                          |
           v                          v
+---------------------+   +----------------------+
| sam-ent-k8s         |   | sam-ent-k8s          |
| Orchestrator        |   | SearXNG Pod          |
| (agent card         |   | searxng:8080         |
|  discovery)         |   | (ClusterIP)          |
+---------------------+   +----------------------+
```

## Deployment Model

Manually deployed via kubectl manifests (ConfigMap, Secret,
Deployment). Uses the base SAM Enterprise image with builtin
tools only -- no custom Dockerfile or Docker build required.

| Property | Value |
|----------|-------|
| Namespace | `sam-ent-k8s-agents` |
| Deployment | `sam-web-research-agent` |
| Image | `localhost:5000/solace-agent-mesh-enterprise:1.97.2` |
| agent_name | `WebResearchAgent` |
| display_name | `Web Research Agent` |
| Model | `openai/claude-opus-4-6` (via LiteLLM) |

## Programmatic Enforcement

This agent is the first in the mesh to use SAM platform features
for programmatic enforcement beyond LLM instructions:

| Feature | Config Key | Value |
|---------|-----------|-------|
| Tool budget | `max_llm_calls_per_task` | 6 |
| Temperature | `model.temperature` | 0.2 |
| Loopback | `allow_loopback` | true |

`max_llm_calls_per_task` counts LLM round-trips. With a limit
of 6, the agent can make at most 3 tool calls before SAM forces
a COMPLETED response with accumulated results.

## Tools

| Tool | Source | Purpose |
|------|--------|---------|
| web_request | builtin: web | HTTP requests (SearXNG, pages) |
| data_analysis | builtin: data_analysis | Query and visualize data |
| artifact_mgmt | builtin: artifact_management | Artifact CRUD |

The `web` builtin-group is configured with `allow_loopback: true`
to permit requests to cluster-internal services (SearXNG).

## SearXNG Dependency

This agent requires SearXNG as its primary search backend. The
endpoint is configured via the `SEARXNG_URL` environment variable
in the Secret manifest, and referenced as `${SEARXNG_URL}` in the
agent instruction (resolved by SAM at startup).

| Environment | SEARXNG_URL value |
|-------------|-------------------|
| Current | `http://searxng.sam-ent-k8s.svc.cluster.local:8080` |

When deploying to a different environment, update `SEARXNG_URL`
in `sam-web-research-agent-secret.yaml`. No instruction changes
needed.

SearXNG deployment manifests: `Deployment/SearXNG/`.

## Key Behaviour

| Feature | Detail |
|---------|--------|
| Tool call budget | Hard limit: 6 LLM calls (programmatic) |
| Search backend | SearXNG JSON API (never raw Google/DDG) |
| Data extraction | Must analyze content before further calls |
| URL policy | Never guess URLs -- only follow search results |
| Training data | Must mark as "unverified" if used |
| Quick Search | 1-2 calls, target less than 15 seconds |
| Deep Research | Up to 3 calls, target less than 60 seconds |
| Early exit | Stops when high-confidence answer found |
| Language | Responds in query language |

## Environment Variables

| Variable | Value | Notes |
|----------|-------|-------|
| `LLM_SERVICE_GENERAL_MODEL_NAME` | `openai/claude-opus-4-6` | Opus |
| `LLM_SERVICE_ENDPOINT` | `https://lite-llm.mymaas.net` | LiteLLM |
| `LLM_SERVICE_API_KEY` | (secret) | Shared key |
| `SEARXNG_URL` | `http://searxng....:8080` | SearXNG |
| `SOLACE_BROKER_URL` | `ws://host.docker.internal:8008` | Broker |
| `NAMESPACE` | `sam-ent-k8s` | Mesh namespace |
| `S3_ENDPOINT_URL` | `http://agent-mesh-seaweedfs-0...:8333` | S3 |
| `S3_BUCKET_NAME` | `sam-ent-k8s` | Shared bucket |

## File Structure

```text
Agents/External Agents/Web Research Agent/
|-- deploy/
|   |-- sam-web-research-agent-config.yaml       # ConfigMap
|   |-- sam-web-research-agent-secret.yaml        # Secret
|   +-- sam-web-research-agent-deployment.yaml    # Deployment
+-- README.md                # This file
```

## Deployment

```bash
# Apply manifests
kubectl apply -f deploy/sam-web-research-agent-secret.yaml
kubectl apply -f deploy/sam-web-research-agent-config.yaml
kubectl apply -f deploy/sam-web-research-agent-deployment.yaml

# Verify
kubectl get pods -n sam-ent-k8s-agents -l app=sam-external-agents \
  | grep web-research

# Check logs
kubectl logs deployment/sam-web-research-agent \
  -n sam-ent-k8s-agents --tail=50
```

## Migration from Agent Builder

This agent was previously deployed via Agent Builder (Helm chart
`sam-agent-1.2.4`) in `sam-ent-k8s` namespace. After deploying
the manual version, scale down or delete the Agent Builder pod:

```bash
# Delete the Agent Builder deployment
kubectl delete deployment \
  sam-agent-019d4e5e-bdb1-7440-979a-ba781c28a537 \
  -n sam-ent-k8s

# Verify the new agent is publishing its agent card
kubectl logs deployment/sam-web-research-agent \
  -n sam-ent-k8s-agents --tail=20 | grep "agent_card"
```

The agent_name changes from
`agent_019d4e5e_bdb1_7440_979a_ba781c28a537` to
`WebResearchAgent`. The orchestrator discovers agents by agent
card, so it will pick up the new name automatically.

## Verification

Port-forward the gateway and send a test request:

```bash
kubectl port-forward svc/agent-mesh 8081:80 -n sam-ent-k8s
```

```bash
curl -s -X POST http://localhost:8081/api/v1/message:send \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-wr-001",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "What is SearXNG?"
        }],
        "messageId": "msg-wr-001",
        "metadata": {
          "agent_name": "WebResearchAgent"
        }
      }
    }
  }'
```

```bash
curl -s http://localhost:8081/api/v1/tasks/<task_id>
```

**Success criteria:**

- Agent uses SearXNG JSON API (not raw Google/DuckDuckGo)
- Response includes numbered citations with URLs
- Completes in less than 15 seconds for Quick Search
- Maximum 3 web_request calls (6 LLM calls)

## Changelog

| Date | Change |
|------|--------|
| 2026-04-09 | Migrated from Agent Builder to manual deployment |
| 2026-04-09 | Added SearXNG as primary search backend |
| 2026-04-09 | Added programmatic tool budget (max_llm_calls_per_task) |
| 2026-04-09 | Added data extraction discipline |
| 2026-04-09 | Added allow_loopback for cluster-internal access |
| 2026-04-09 | Lowered temperature to 0.2 for determinism |
| 2026-04-02 | Initial deployment via Agent Builder |
