# Solace Agent Mesh (SAM) Artifacts

## Project Overview

Multi-agent procurement system on Kubernetes using Solace Agent Mesh Enterprise.
Agents communicate via Solace PubSub+ event broker and are orchestrated by a
gateway that discovers agents via agent cards published on the mesh.

## Environment

- **kubectl:** `/Users/alexandermartens/.rd/bin/kubectl`
- **Docker:** `/Users/alexandermartens/.rd/bin/docker` (Rancher Desktop)
- **Docker push requires:** `PATH="/Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin:$PATH"` for `docker-credential-osxkeychain`
- **Registry:** `localhost:5000` (local registry, no auth)
- **Base image:** `localhost:5000/solace-agent-mesh-enterprise:1.97.2`
- **K8s namespaces:**
  - `sam-ent-k8s` -- core platform (gateway, orchestrator, broker, SearXNG)
  - `sam-ent-k8s-agents` -- all external agents and workflows

## Architecture

```
User --> Gateway (port 80) --> Orchestrator --> Agent(s) --> Tools
                                   |
                            Solace PubSub+ Broker
                            (ws://host.docker.internal:8008)
```

- **Gateway:** REST API for external access (A2A protocol)
- **Orchestrator:** Discovers agents via agent cards, routes requests
- **Agents:** Each runs as a K8s pod, publishes an agent card every 10s
- **Tools:** MCP (stdio) or Python-based, per agent

## LLM Configuration

All agents use LiteLLM proxy at `https://lite-llm.mymaas.net`.
Model names are prefixed with `openai/` (LiteLLM convention).

- Default model: `openai/claude-sonnet-4-6`
- Planning model: `openai/claude-sonnet-4-6` (or `openai/claude-opus-4-6`)
- Image model: `openai/azure-dalle-3`

## Artifact Storage

S3-compatible via SeaweedFS:
- Endpoint: `http://agent-mesh-seaweedfs-0.agent-mesh-seaweedfs.sam-ent-k8s.svc.cluster.local:8333`
- Bucket: `sam-ent-k8s`
- Credentials: `sam-ent-k8s` / `sam-ent-k8s`

## Testing Agents via REST API

Port-forward the gateway:
```bash
kubectl port-forward svc/agent-mesh 8081:80 -n sam-ent-k8s
```

Send a request (JSON-RPC 2.0 format):
```bash
curl -s -X POST http://localhost:8081/api/v1/message:send \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-123",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "Your prompt here"}],
        "messageId": "msg-123",
        "metadata": {"agent_name": "AgentNameHere"}
      }
    }
  }'
```

Retrieve results (YAML response):
```bash
curl -s http://localhost:8081/api/v1/tasks/<task_id>
```

Response is in `invocation_flow` -> events with `direction: response` ->
`payload.result.status.message.parts[].text`.

**Common mistakes:**
- Missing `metadata.agent_name` field causes "Missing agent_name" error
- Missing `id` and `params` wrapper causes validation error
- `messageId` must be inside the `message` object
- Results may take 30-120s; poll the task endpoint

## Agent Naming Conventions

- **K8s resources:** `sam-<agent-slug>-agent-{config,secret,deployment}.yaml`
- **agent_name:** PascalCase with "Agent" suffix (e.g. `ArticleVerificationAgent`)
- **display_name:** English title (e.g. "Article Verification Agent")
- **Docker image:** `localhost:5000/sam-<slug>-agent:1.0.0`

## K8s Manifest Pattern

Every agent has three manifests in its `deploy/` directory:

1. **ConfigMap** -- contains the full agent YAML config (embedded as multiline string)
2. **Secret** -- environment variables (broker, LLM, S3, agent-specific)
3. **Deployment** -- pod spec with image, resources, volume mounts

ConfigMap is mounted at `/app/configs/agents/` and the pod runs:
```
solace-agent-mesh run configs/agents/<agent>.yaml
```

## Common Secret Variables (all agents share these)

```yaml
NAMESPACE: "sam-ent-k8s"
SOLACE_BROKER_URL: "ws://host.docker.internal:8008"
SOLACE_BROKER_VPN: "sam"
SOLACE_BROKER_USERNAME: "default"
SOLACE_BROKER_PASSWORD: "default"
LLM_SERVICE_ENDPOINT: "https://lite-llm.mymaas.net"
LLM_SERVICE_API_KEY: "<key>"
LLM_SERVICE_GENERAL_MODEL_NAME: "openai/claude-sonnet-4-6"
S3_BUCKET_NAME: "sam-ent-k8s"
S3_ENDPOINT_URL: "http://agent-mesh-seaweedfs-0.agent-mesh-seaweedfs.sam-ent-k8s.svc.cluster.local:8333"
AWS_ACCESS_KEY_ID: "sam-ent-k8s"
AWS_SECRET_ACCESS_KEY: "sam-ent-k8s"
```

## MCP Tool Integration Pattern

Agents use MCP servers over stdio. Common pattern in agent config YAML:
```yaml
tools:
  - tool_type: mcp
    connection_params:
      type: stdio
      command: "uv"
      args: ["--directory", "/opt/<agent>-mcp/", "run", "/opt/<agent>-mcp/<server>.py"]
      timeout: 30
    environment_variables:
      MCP_LOG_LEVEL: "${MCP_LOG_LEVEL}"
      MCP_LOG_FILE: "${MCP_LOG_FILE}"
  - tool_type: builtin-group
    group_name: "artifact_management"
```

## Docker Build Pattern

```dockerfile
FROM localhost:5000/solace-agent-mesh-enterprise:1.97.2
RUN pip install --upgrade pip && pip install --upgrade uv
WORKDIR /opt/<agent>-mcp
COPY src/<server>.py .
COPY requirements.txt .
RUN uv venv && uv pip install -r requirements.txt
RUN chmod +x <server>.py
WORKDIR /app
```

## Build, Push, Deploy Cycle

```bash
# Build
cd "Agents/External Agents/<Agent Name>/"
DOCKER_CONFIG=/Users/alexandermartens/.docker \
  /Users/alexandermartens/.rd/bin/docker build -t localhost:5000/sam-<slug>-agent:1.0.0 .

# Push (needs credential helper in PATH)
PATH="/Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin:$PATH" \
  DOCKER_CONFIG=/Users/alexandermartens/.docker \
  /Users/alexandermartens/.rd/bin/docker push localhost:5000/sam-<slug>-agent:1.0.0

# Deploy (apply manifests if changed, then restart)
kubectl apply -f deploy/sam-<slug>-agent-secret.yaml
kubectl apply -f deploy/sam-<slug>-agent-config.yaml
kubectl apply -f deploy/sam-<slug>-agent-deployment.yaml

# Or just restart if only the image changed
kubectl rollout restart deployment/sam-<slug>-agent -n sam-ent-k8s-agents
kubectl rollout status deployment/sam-<slug>-agent -n sam-ent-k8s-agents --timeout=60s
```

## Shared Services

### SearXNG (Meta-Search Engine)

Deployed in `sam-ent-k8s` namespace as a shared service for all agents.
- **Service URL:** `http://searxng.sam-ent-k8s.svc.cluster.local:8080`
- **Engines:** Google (weight 1.2), Bing (1.0), DuckDuckGo (0.8), Google-DE (1.1)
- **Config:** `Deployment/SearXNG/searxng-configmap.yaml`
- **Deployment:** `Deployment/SearXNG/searxng-deployment.yaml`
- **JSON API:** `GET /search?q=<query>&format=json`
- **Rate limiting:** Disabled for internal use

## Agent Inventory

| Agent | Type | Tools | Namespace |
|-------|------|-------|-----------|
| MarkitdownAgent | Core | convert_file_to_markdown | sam-ent-k8s-agents |
| MermaidAgent | Core | mermaid_diagram_generator | sam-ent-k8s-agents |
| WebResearchAgent | Builtin | web_request, data_analysis | sam-ent-k8s-agents |
| ArticleVerificationAgent | MCP | check_article, search_article | sam-ent-k8s-agents |
| WebScraperAgent | MCP | 5 Playwright tools | sam-ent-k8s-agents |
| EANSearchAgent | MCP | 4 EAN tools | sam-ent-k8s-agents |
| PriceComparisonAgent | Python | 6 price tools | sam-ent-k8s-agents |
| DatadogMCPAgent | MCP | 73 Datadog tools | sam-ent-k8s-agents |
| SolaceBrokerMCPAgent | MCP | 456 SEMP v2 tools | sam-ent-k8s-agents |

## Code Standards

- **ASCII-only** in all files (no unicode dashes, arrows, umlauts in code)
- **English** for code, comments, tool descriptions, agent instructions
- **German** is acceptable in user-facing output (reports, display names for German workflows)
- Keep deploy YAMLs structurally consistent across agents
- Agent instructions should include speed constraints (max tool calls)

## Common Pitfalls

- **IntelligentMCPCallback:** SAM may save large MCP tool results as S3 artifacts
  instead of inline. Agent instructions must mention `load_artifact` as a fallback.
- **SearXNG port collision:** K8s injects `SEARXNG_PORT` env var from the service,
  which collides with SearXNG's own config. Fix: explicit `SEARXNG_PORT: "8080"` in
  the deployment env.
- **web_search_google builtin:** Requires Google API keys configured in tool_config.
  If not configured, it silently fails. Prefer SearXNG via MCP instead.
- **Session service "memory":** Does not persist messages across pod restarts.
  Use `sql` type with SQLite for persistence.
- **Port-forward target:** Gateway service is `svc/agent-mesh` not `svc/sam-ent-k8s`.
- **web_request private IP block:** The `web` builtin-group blocks requests to
  cluster-internal IPs (private ranges) by default. Set `tool_config.allow_loopback: true`
  on the `web` builtin-group to allow access to cluster-internal services like SearXNG.
- **max_llm_calls_per_task:** SAM supports programmatic tool budget enforcement via
  `app_config.max_llm_calls_per_task` (default 20). Counts LLM round-trips, not raw
  tool calls. For 3 tool calls, set to 6 (3 calls + 3 responses).
- **max_output_tokens:** The `model.max_output_tokens` config does not work with
  the LiteLLM-to-Anthropic chain (rejected as "Extra inputs not permitted").
  Do not set this parameter for Claude models via LiteLLM.
