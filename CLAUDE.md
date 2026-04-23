# Solace Agent Mesh (SAM) Artifacts

## Project Overview

Multi-agent procurement system on Kubernetes using Solace Agent Mesh Enterprise.
Agents communicate via Solace PubSub+ event broker and are orchestrated by a
gateway that discovers agents via agent cards published on the mesh.

## Official Documentation

- **Getting Started:** <https://solacelabs.github.io/solace-agent-mesh/docs/documentation/getting-started/>
- **Enterprise Edition:** <https://solacelabs.github.io/solace-agent-mesh/docs/documentation/enterprise/>

## Environment

- **kubectl:** `/Users/alexandermartens/.rd/bin/kubectl`
- **Docker:** `/Users/alexandermartens/.rd/bin/docker` (Rancher Desktop)
- **Docker push requires:**
  `PATH="/Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin:$PATH"`
  for `docker-credential-osxkeychain`
- **Registry:** `registry.solace.lab` (auth required, see [solace-lab-infrastructure/registry](https://github.com/martensa/solace-lab-infrastructure/tree/master/registry))
- **Base image:** `registry.solace.lab/solace-agent-mesh-enterprise:1.97.2`
- **K8s namespaces:**
  - `sam-solace-lab` -- core platform (gateway, orchestrator, broker)
  - `sam-solace-lab-agents` -- all external agents
  - `sam-solace-lab-workflows` -- multi-agent workflows
  - `sam-solace-lab-shared` -- shared services (SearXNG, etc.)

## Architecture

```text
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

- Endpoint: `http://agent-mesh-seaweedfs-0.agent-mesh-seaweedfs.sam-solace-lab.svc.cluster.local:8333`
- Bucket: `sam-solace-lab`
- Credentials: `sam-solace-lab` / `sam-solace-lab`

## Testing Agents via REST API

Port-forward the gateway:

```bash
kubectl port-forward svc/agent-mesh 8081:80 -n sam-solace-lab
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
- **Docker image:** `registry.solace.lab/sam-<slug>-agent:1.0.0`

## K8s Manifest Pattern

Every agent has three manifests in its `deploy/` directory:

1. **ConfigMap** -- contains the full agent YAML config (embedded as multiline string)
2. **Secret** -- agent-specific env vars (`LLM_SERVICE_GENERAL_MODEL_NAME`,
   MCP settings, API keys). Common values come from the **shared secret**.
3. **Deployment** -- pod spec with image, resources, volume mounts. The
   `envFrom` references TWO Secrets: `sam-shared-secret` first (base), then
   `sam-<agent>-agent-secret` (overrides). K8s resolves later entries after
   earlier ones, so the agent-specific Secret wins on conflicts.

ConfigMap is mounted at `/app/configs/agents/` and the pod runs:

```bash
solace-agent-mesh run configs/agents/<agent>.yaml
```

## Secrets & Credentials (template pattern)

Real credential values (LiteLLM API key, S3 access keys, Datadog tokens, …)
live ONLY in a local, gitignored `.env` at repo root and are rendered into
K8s Secrets via `envsubst`. Only `*.yaml.template` files are committed.

### File structure

```text
.env                                             # gitignored, real values
.env.example                                     # committed, placeholders
Makefile                                         # convenience targets
scripts/render-secrets.sh                        # envsubst runner
deploy/shared/
  sam-shared-secret.yaml.template                # committed
  sam-shared-secret.sam-solace-lab-agents.yaml   # gitignored (rendered)
  sam-shared-secret.sam-solace-lab-workflows.yaml
Agents/<Agent Name>/deploy/
  sam-<agent>-secret.yaml.template               # committed
  sam-<agent>-secret.yaml                        # gitignored (rendered)
```

`.gitignore` patterns:

```text
.env
.env.*
!.env.example

**/*-secret.yaml
**/*-secret.*.yaml
!**/*-secret.yaml.template
```

### Workflow

**First time on a machine:**

```bash
cp .env.example .env
$EDITOR .env                # fill in real values from password vault
make secrets                # renders all *-secret.yaml from templates
make apply-secrets          # kubectl apply on all rendered secrets
```

**Rotate a credential:** edit `.env`, then `make apply-secrets` and
`kubectl rollout restart deployment/<name> -n <namespace>`.

**Add a new agent:** create `sam-<agent>-secret.yaml.template` with
`${VAR}` placeholders for any secret values; add missing vars to
`.env.example` and `.env`; run `make secrets` + `make apply-secrets`.

### Shared Secret (`sam-shared-secret`)

Rendered once per namespace from `deploy/shared/sam-shared-secret.yaml.template`.
Contains the values common to ALL agents & workflows:

| Key | Value |
|-----|-------|
| `NAMESPACE` | `sam-solace-lab` (SAM event addressing, NOT the K8s namespace) |
| `LLM_SERVICE_ENDPOINT` | `https://lite-llm.mymaas.net` |
| `LLM_SERVICE_API_KEY` | from `.env` |
| `SOLACE_BROKER_URL` | `ws://host.docker.internal:8008` |
| `SOLACE_BROKER_VPN` | `sam` (default; Datadog + Broker MCP override to `default`) |
| `SOLACE_BROKER_USERNAME` / `SOLACE_BROKER_PASSWORD` | `default` |
| `SOLACE_DEV_MODE` | `false` |
| `ARTIFACT_SERVICE_TYPE` | `s3` |
| `S3_BUCKET_NAME` | `sam-solace-lab` (Datadog + Broker MCP override to `sam-ent-local`) |
| `S3_ENDPOINT_URL` | SeaweedFS cluster endpoint |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | `sam-solace-lab` / `us-east-1` |
| `ENABLE_EMBED_RESOLUTION` / `ENABLE_ARTIFACT_CONTENT_INSTRUCTION` | `true` |

Agent-specific Secrets should contain ONLY deviations from these defaults
plus variables unique to the agent (e.g. `DD_API_KEY`, `PRICE_SERPAPI_KEY`,
`SOLACE_SEMPV2_PASSWORD`, `EAN_SEARCH_API_TOKEN`, `OPENAPI_SPEC`).

### Safety guarantees

- `render-secrets.sh` **fails** if `.env` is missing, if `envsubst` is not
  installed, or if any `${VAR}` placeholder remains unsubstituted in the
  rendered output.
- `make verify-secrets` parses every rendered YAML with `PyYAML` before
  `apply`.
- `.gitignore` excludes rendered files; only `.template` files are
  commit-eligible. `git check-ignore .env` and
  `git check-ignore <rendered-file>` must both return non-zero before
  committing.

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
FROM registry.solace.lab/solace-agent-mesh-enterprise:1.97.2
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
  /Users/alexandermartens/.rd/bin/docker build -t registry.solace.lab/sam-<slug>-agent:1.0.0 .

# Push (needs credential helper in PATH)
PATH="/Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin:$PATH" \
  DOCKER_CONFIG=/Users/alexandermartens/.docker \
  /Users/alexandermartens/.rd/bin/docker push registry.solace.lab/sam-<slug>-agent:1.0.0

# Deploy (apply manifests if changed, then restart)
kubectl apply -f deploy/sam-<slug>-agent-secret.yaml
kubectl apply -f deploy/sam-<slug>-agent-config.yaml
kubectl apply -f deploy/sam-<slug>-agent-deployment.yaml

# Or just restart if only the image changed
kubectl rollout restart deployment/sam-<slug>-agent -n sam-solace-lab-agents
kubectl rollout status deployment/sam-<slug>-agent -n sam-solace-lab-agents --timeout=60s
```

## Related Repositories

| Repository | Purpose |
|------------|---------|
| [solace-lab-infrastructure](https://github.com/martensa/solace-lab-infrastructure) | Base infrastructure (K8s, networking, storage) |
| [solace-demo-artifacts](https://github.com/martensa/solace-demo-artifacts) | Full Solace demo environment (Event Mesh, Agent Mesh, Tracing, Kafka Bridge, Event Portal, Schema Registry) |
| [solace-demo-artifacts/agent-mesh-deployment](https://github.com/martensa/solace-demo-artifacts/tree/master/agent-mesh-deployment) | SAM platform Helm chart, broker, orchestrator, gateway -- direct prerequisite for this repo |

## Shared Services

### SearXNG (Meta-Search Engine)

Deployed in `sam-solace-lab-shared` namespace as a shared service for all agents.

- **Service URL:** `http://searxng.sam-solace-lab-shared.svc.cluster.local:8080`
- **Engines:** Google (weight 1.2), Bing (1.0), DuckDuckGo (0.8), Google-DE (1.1)
- **JSON API:** `GET /search?q=<query>&format=json`
- **Rate limiting:** Disabled for internal use

## Agent Inventory

| Agent | Type | Tools | Namespace |
|-------|------|-------|-----------|
| MarkitdownAgent | Core | convert_file_to_markdown | sam-solace-lab-agents |
| MermaidAgent | Core | mermaid_diagram_generator | sam-solace-lab-agents |
| WebResearchAgent | Builtin | web_request, data_analysis | sam-solace-lab-agents |
| ArticleVerificationAgent | MCP | check_article, search_article | sam-solace-lab-agents |
| WebScraperAgent | MCP | 5 Playwright tools | sam-solace-lab-agents |
| EANSearchAgent | MCP | 4 EAN tools | sam-solace-lab-agents |
| PriceComparisonAgent | MCP | 3 price tools | sam-solace-lab-agents |
| DatadogMCPAgent | MCP | 73 Datadog tools | sam-solace-lab-agents |
| SolaceBrokerMCPAgent | MCP | 456 SEMP v2 tools | sam-solace-lab-agents |

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
- **Port-forward target:** Gateway service is `svc/agent-mesh` not `svc/sam-solace-lab`.
- **web_request private IP block:** The `web` builtin-group blocks requests to
  cluster-internal IPs (private ranges) by default. Set `tool_config.allow_loopback: true`
  on the `web` builtin-group to allow access to cluster-internal services like SearXNG.
- **max_llm_calls_per_task:** SAM supports programmatic tool budget enforcement via
  `app_config.max_llm_calls_per_task` (default 20). Counts LLM round-trips, not raw
  tool calls. For 3 tool calls, set to 6 (3 calls + 3 responses).
- **max_output_tokens:** The `model.max_output_tokens` config does not work with
  the LiteLLM-to-Anthropic chain (rejected as "Extra inputs not permitted").
  Do not set this parameter for Claude models via LiteLLM.
