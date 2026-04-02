# Solace Broker MCP Agent v5.0

Solace PubSub+ broker management and monitoring agent via SEMP v2.
Works with Gemini, Claude, and GPT models.

## Overview

This agent connects an LLM to a Solace PubSub+ Event Broker through
the SEMP v2 management API. It dynamically generates MCP (Model Context
Protocol) tools from an OpenAPI/Swagger spec file, allowing the LLM to
query broker health, manage queues, inspect clients, create resources,
and more -- all through natural language.

### How it works

1. **MCP Server** (`solace_monitoring_mcp_server.py`) loads a SEMP v2
   OpenAPI spec at startup and exposes every API operation as an MCP
   tool with correctly typed JSON-Schema parameters.
2. **Solace Agent Mesh** framework hosts the agent, connects it to the
   Agent Mesh event broker, and routes user requests to the LLM.
3. **The LLM** receives the tool definitions, the agent instruction
   prompt, and the user's question, then calls the appropriate SEMP
   tools to answer.

### Key features

- Supports both Swagger 2.0 (config spec) and OpenAPI 3.0 (monitor spec)
- Cross-LLM compatible: tested with Gemini, Claude, and GPT
- Server-side response trimming for list endpoints (reduces token usage)
- Hard character cap prevents LLM context overflow
- Compact JSON output (no indent, no unnecessary whitespace)
- Token-optimised tool descriptions with HTTP method prefix
  (`[GET]`, `[CREATE]`, `[UPDATE]`, `[DELETE]`)
- Server-side tool filtering via 8 environment variables
  (method, tag, path, tool name)
- `additionalProperties: false` on all tool schemas for GPT strict
  mode compatibility
- Automatic pagination handling via agent instruction prompt
- Error responses include targeted hints for common SEMP error codes

### Repository structure

| File | Purpose |
| --- | --- |
| `solace_monitoring_mcp_server.py` | MCP server (Python). Loads the OpenAPI spec and serves tools over stdio. |
| `Dockerfile` | Container image build. Based on `solace-agent-mesh-enterprise`. |
| `sam-solace-broker-mcp-agent-config.yaml` | Kubernetes ConfigMap. Agent config, instruction prompt, tool wiring. |
| `sam-solace-broker-mcp-agent-secret.yaml` | Kubernetes Secret. Credentials, model selection, env var configuration. |
| `sam-solace-broker-mcp-agent-deployment.yaml` | Kubernetes Deployment. Pod spec, volume mounts, image reference. |
| `semp-v2-swagger-config.json` | SEMP v2 Config API spec (Swagger 2.0). 456 tools: GET + POST + PUT + PATCH + DELETE. |
| `semp-v2-swagger-monitor.json` | SEMP v2 Monitor API spec (OpenAPI 3.0). 234 tools: GET only (read-only). |
| `requirements-mcp-server.txt` | Python dependency (`requests`). |

---

## Build & Push

```bash
docker build -t sam-solace-broker-mcp-agent:1.0.0 .
docker tag sam-solace-broker-mcp-agent:1.0.0 localhost:5000/sam-solace-broker-mcp-agent:1.0.0
docker push localhost:5000/sam-solace-broker-mcp-agent:1.0.0
```

## Deploy to Kubernetes

```bash
kubectl apply -f sam-solace-broker-mcp-agent-secret.yaml
kubectl apply -f sam-solace-broker-mcp-agent-config.yaml
kubectl apply -f sam-solace-broker-mcp-agent-deployment.yaml
```

## Verify

```bash
kubectl -n sam-ent-k8s-agents get pods -l app=sam-custom-agents
kubectl -n sam-ent-k8s-agents logs deployment/sam-solace-broker-mcp-agent
```

On successful startup, the MCP server logs will show:

```text
Loaded 456 tools from /opt/solace-broker-mcp-server/semp-v2-swagger-config.json (detected: config) [no filters]
Solace SEMP MCP v5.0 ready: 456 tools (config), base=http://host.docker.internal:8080
```

## Redeploy after configuration changes

After editing the secret or config YAML, re-apply and restart:

```bash
kubectl apply -f sam-solace-broker-mcp-agent-secret.yaml
kubectl apply -f sam-solace-broker-mcp-agent-config.yaml
kubectl -n sam-ent-k8s-agents rollout restart deployment/sam-solace-broker-mcp-agent
```

---

## Environment Variables Reference

All environment variables are set in
`sam-solace-broker-mcp-agent-secret.yaml` and injected into the pod via
`envFrom: secretRef`. The config YAML passes MCP-specific variables
through to the MCP server subprocess via `environment_variables`.

### Solace Agent Mesh (framework-level)

These configure the Agent Mesh framework and the connection to the
Agent Mesh event broker (not the managed broker).

| Variable | Description | Default | Example |
| --- | --- | --- | --- |
| `NAMESPACE` | Agent Mesh namespace. Must match your cluster. | *(required)* | `sam-ent-k8s` |
| `SOLACE_BROKER_URL` | WebSocket URL of the Agent Mesh event broker. | `ws://localhost:8080` | `ws://host.docker.internal:8008` |
| `SOLACE_BROKER_VPN` | VPN name on the Agent Mesh broker. | `default` | `default` |
| `SOLACE_BROKER_USERNAME` | Username for the Agent Mesh broker. | `default` | `default` |
| `SOLACE_BROKER_PASSWORD` | Password for the Agent Mesh broker. | `default` | `default` |
| `SOLACE_DEV_MODE` | Enable dev mode (local testing without full mesh). | `false` | `true` |

### LLM Configuration

These configure which LLM the agent uses. The agent connects to the
LLM via a LiteLLM-compatible endpoint.

| Variable | Description | Default | Example |
| --- | --- | --- | --- |
| `LLM_SERVICE_ENDPOINT` | LiteLLM-compatible API base URL. | *(required)* | `https://lite-llm.mymaas.net` |
| `LLM_SERVICE_API_KEY` | API key for the LLM endpoint. | *(required)* | `sk-cFW9MYGP60...` |
| `LLM_SERVICE_GENERAL_MODEL_NAME` | Model identifier. See [LLM Model Selection](#llm-model-selection) for recommendations. | *(required)* | `openai/gemini-2.5-pro` |

### Artifact Storage (S3)

These configure the S3-compatible storage used by the artifact
management skill.

| Variable | Description | Default | Example |
| --- | --- | --- | --- |
| `S3_BUCKET_NAME` | S3 bucket name. | *(required)* | `sam-ent-local` |
| `S3_ENDPOINT_URL` | S3-compatible endpoint URL. | *(required)* | `http://agent-mesh-seaweedfs-0....:8333` |
| `AWS_ACCESS_KEY_ID` | S3 access key. | *(required)* | `sam-ent-local` |
| `AWS_SECRET_ACCESS_KEY` | S3 secret key. | *(required)* | `sam-ent-local` |
| `AWS_REGION` | AWS region for S3. | `us-east-1` | `us-east-1` |

### SEMP v2 Broker Connection

These configure the connection from the MCP server to the Solace
PubSub+ broker being managed. This is the target broker the agent
queries and configures.

| Variable | Description | Default | Example |
| --- | --- | --- | --- |
| `SOLACE_SEMPV2_BASE_URL` | Base URL of the SEMP v2 API on the managed broker. | `http://localhost:8080` | `http://host.docker.internal:8080` |
| `SOLACE_SEMPV2_AUTH_METHOD` | Authentication method: `basic` or `bearer`. | `basic` | `basic` |
| `SOLACE_SEMPV2_USERNAME` | SEMP username (used when `AUTH_METHOD=basic`). | `admin` | `admin` |
| `SOLACE_SEMPV2_PASSWORD` | SEMP password (used when `AUTH_METHOD=basic`). | `admin` | `admin` |
| `SOLACE_SEMPV2_BEARER_TOKEN` | Bearer token (used when `AUTH_METHOD=bearer`). | *(empty)* | `eyJhbGci...` |

### MCP Server Behaviour

These control the MCP server's runtime behaviour.

| Variable | Description | Default | Recommendation |
| --- | --- | --- | --- |
| `OPENAPI_SPEC` | Path to the OpenAPI/Swagger spec file inside the container. Determines which SEMP API is exposed. See [SEMP Spec Selection](#semp-spec-selection-monitor-vs-config). | *(required)* | `/opt/solace-broker-mcp-server/semp-v2-swagger-config.json` |
| `MCP_MAX_RESPONSE_CHARS` | Hard character cap on tool responses. Responses exceeding this limit are truncated with a hint to use `count` or `where` to narrow results. See [Recommended settings per model](#recommended-settings-per-model). | `25000` | `25000` for Claude/Gemini. `10000` for GPT. |
| `MCP_LOG_LEVEL` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Set to `DEBUG` to see every filtered tool and every SEMP HTTP request. | `INFO` | `INFO` for production, `DEBUG` for troubleshooting. |
| `MCP_LOG_FILE` | Log file path (inside the container). Logs never go to stdout because MCP uses stdout for JSON-RPC. | `solace_mcp.log` | `solace_broker_mcp_server.log` |
| `ENABLE_EMBED_RESOLUTION` | When `true`, artifact content is resolved and embedded into the LLM context. Set to `false` for GPT to reduce prompt size on multi-tool flows. | `true` | `true` for Claude/Gemini. `false` for GPT. |
| `ENABLE_ARTIFACT_CONTENT_INSTRUCTION` | When `true`, artifact content instructions are injected into the LLM prompt. Set to `false` for GPT to reduce prompt size. | `true` | `true` for Claude/Gemini. `false` for GPT. |

### Server-Side Tool Filtering

These 8 environment variables control which OpenAPI operations are
registered as MCP tools. They use the same names as the
[SolaceLabs reference implementation](https://github.com/SolaceLabs/solace-platform-mcp/tree/main/solace-monitoring-mcp-server)
for consistency.

All are optional. When unset or blank, all tools from the spec are
registered (default behaviour). Values are comma-separated lists.
**Exclusion takes precedence** if both include and exclude match the
same tool.

Tools tagged `deprecated` in the OpenAPI spec are always excluded
regardless of filter settings.

#### Filter by HTTP method

| Variable | Description | Example |
| --- | --- | --- |
| `MCP_API_INCLUDE_METHODS` | Only register tools matching these HTTP methods. | `GET` (read-only), `GET,POST` (read + create) |
| `MCP_API_EXCLUDE_METHODS` | Do not register tools matching these HTTP methods. | `DELETE,PUT` (prevent destructive operations) |

#### Filter by OpenAPI tag

| Variable | Description | Example |
| --- | --- | --- |
| `MCP_API_INCLUDE_TAGS` | Only register tools associated with these OpenAPI tags. | `msgVpn,queue` |
| `MCP_API_EXCLUDE_TAGS` | Do not register tools associated with these tags. | `about,certAuthority` |

#### Filter by path substring

| Variable | Description | Example |
| --- | --- | --- |
| `MCP_API_INCLUDE_PATHS` | Only register tools whose API path contains any of these substrings. | `/msgVpns/` |
| `MCP_API_EXCLUDE_PATHS` | Do not register tools whose path matches. | `/dmrBridges/,/certAuthorities/` |

#### Filter by tool name (operationId)

| Variable | Description | Example |
| --- | --- | --- |
| `MCP_API_INCLUDE_TOOLS` | Only register these tool names (comma-separated operationIds). | `getMsgVpns,getMsgVpnQueues` |
| `MCP_API_EXCLUDE_TOOLS` | Do not register these tool names. | `deleteMsgVpn` |

#### Filter evaluation order

Filters are applied in this order. The first failing check stops
evaluation:

1. HTTP method include/exclude
2. Tool name include/exclude
3. Tag include/exclude
4. Path include/exclude

#### Filtering examples

**Read-only mode** (no create/update/delete):

```yaml
MCP_API_INCLUDE_METHODS: "GET"
```

Result: 177 tools (config spec) or 234 tools (monitor spec).

**Prevent destructive operations only** (allow read + create + update,
block delete + replace):

```yaml
MCP_API_EXCLUDE_METHODS: "DELETE,PUT"
```

**GPT-optimised preset** (35 essential tools):

```yaml
MCP_API_INCLUDE_TOOLS: "getMsgVpns,getMsgVpn,getMsgVpnQueues,getMsgVpnQueue,createMsgVpnQueue,updateMsgVpnQueue,deleteMsgVpnQueue,getMsgVpnQueueSubscriptions,createMsgVpnQueueSubscription,deleteMsgVpnQueueSubscription,getMsgVpnClientProfiles,getMsgVpnClientProfile,createMsgVpnClientProfile,updateMsgVpnClientProfile,getMsgVpnClientUsernames,getMsgVpnClientUsername,createMsgVpnClientUsername,updateMsgVpnClientUsername,getMsgVpnAclProfiles,getMsgVpnAclProfile,createMsgVpnAclProfile,updateMsgVpnAclProfile,getMsgVpnAclProfileClientConnectExceptions,createMsgVpnAclProfileClientConnectException,getMsgVpnAclProfilePublishTopicExceptions,createMsgVpnAclProfilePublishTopicException,getMsgVpnAclProfileSubscribeTopicExceptions,createMsgVpnAclProfileSubscribeTopicException,getMsgVpnTopicEndpoints,getMsgVpnTopicEndpoint,createMsgVpnTopicEndpoint,updateMsgVpnTopicEndpoint,deleteMsgVpnTopicEndpoint,getMsgVpnBridges,getMsgVpnBridge"
```

Result: 35 tools. Covers VPNs, queues, subscriptions, client profiles,
ACL profiles, topic endpoints, and bridges.

**Focus on queues and VPNs only**:

```yaml
MCP_API_INCLUDE_PATHS: "/msgVpns/{msgVpnName}/queues,/msgVpns/{msgVpnName}"
MCP_API_EXCLUDE_PATHS: "/clientProfiles,/aclProfiles,/bridges"
```

---

## SEMP Spec Selection (Monitor vs Config)

The agent loads a single OpenAPI spec at startup. Two specs are bundled
in the container:

| Spec | File | Tools | HTTP Methods | Use Case |
| --- | --- | --- | --- | --- |
| **Config** | `semp-v2-swagger-config.json` | 456 | GET, POST, PUT, PATCH, DELETE | Full management: read + create + update + delete resources. |
| **Monitor** | `semp-v2-swagger-monitor.json` | 234 | GET only | Read-only monitoring: health checks, queue depths, client stats. |

Edit `OPENAPI_SPEC` in `sam-solace-broker-mcp-agent-secret.yaml`:

```yaml
# Config spec (456 tools -- full read/write management)
OPENAPI_SPEC: "/opt/solace-broker-mcp-server/semp-v2-swagger-config.json"

# Monitor spec (234 tools -- read-only monitoring)
#OPENAPI_SPEC: "/opt/solace-broker-mcp-server/semp-v2-swagger-monitor.json"
```

**Recommendation:** Use the config spec for most deployments. The agent
instruction prompt includes a "confirm writes" rule that prevents
unintended modifications. If you need strict read-only access, use the
monitor spec or set `MCP_API_INCLUDE_METHODS=GET`.

---

## LLM Model Selection

Edit `LLM_SERVICE_GENERAL_MODEL_NAME` in
`sam-solace-broker-mcp-agent-secret.yaml`:

```yaml
# GPT
LLM_SERVICE_GENERAL_MODEL_NAME: "openai/azure-gpt-5-mini"

# Gemini
#LLM_SERVICE_GENERAL_MODEL_NAME: "openai/gemini-2.5-pro"

# Claude
#LLM_SERVICE_GENERAL_MODEL_NAME: "openai/claude-3-5-sonnet-v2"
```

### LLM compatibility and recommendations

| Model | Max Tools | Tool Filtering Needed? | Config Spec (456 tools) | Monitor Spec (234 tools) | Notes |
| --- | --- | --- | --- | --- | --- |
| **Claude 3.5 Sonnet** | Hundreds | No | Works out of the box | Works out of the box | Best tool selection accuracy. Handles 456 tools and large payloads with ease. Recommended for complex management tasks. |
| **Gemini 2.5 Pro** | Hundreds | No | Works out of the box | Works out of the box | Strong tool handling. Function names are auto-truncated to 64 chars (Gemini limit) with hash suffix to avoid collisions. |
| **GPT-5-mini** | ~50 | **Yes** | Requires tool filtering | May work unfiltered, but filtering recommended | GPT models degrade above ~50 tools. 456 tool definitions consume ~61K tokens before the user's question is even seen. See [GPT-specific setup](#gpt-specific-setup). |

### GPT-specific setup

GPT models (including GPT-5-mini) have architectural limitations with
large tool counts and large context payloads:

1. **Tool definitions consume context:** 456 tools = ~61K tokens of
   JSON Schema definitions in the system prompt, leaving little room
   for the conversation, instruction prompt, and tool responses.
2. **Tool selection degrades:** Above ~50 tools, GPT increasingly picks
   wrong tools, makes phantom tool calls, or fails to call any tool.
3. **Strict mode requires `additionalProperties: false`:** Already
   included on all tool schemas in this server.
4. **Synthesis timeouts on large payloads:** After multi-tool flows
   (e.g., health reports), GPT must process all accumulated tool
   results in one completion call. GPT is significantly slower than
   Claude and Gemini at processing large contexts, causing LiteLLM
   timeout errors (default 120s). See
   [GPT timeout tuning](#gpt-timeout-tuning) below.

**To use GPT, uncomment the GPT-optimised preset** in
`sam-solace-broker-mcp-agent-secret.yaml`:

```yaml
MCP_API_INCLUDE_TOOLS: "getMsgVpns,getMsgVpn,getMsgVpnQueues,getMsgVpnQueue,createMsgVpnQueue,updateMsgVpnQueue,deleteMsgVpnQueue,getMsgVpnQueueSubscriptions,createMsgVpnQueueSubscription,deleteMsgVpnQueueSubscription,getMsgVpnClientProfiles,getMsgVpnClientProfile,createMsgVpnClientProfile,updateMsgVpnClientProfile,getMsgVpnClientUsernames,getMsgVpnClientUsername,createMsgVpnClientUsername,updateMsgVpnClientUsername,getMsgVpnAclProfiles,getMsgVpnAclProfile,createMsgVpnAclProfile,updateMsgVpnAclProfile,getMsgVpnAclProfileClientConnectExceptions,createMsgVpnAclProfileClientConnectException,getMsgVpnAclProfilePublishTopicExceptions,createMsgVpnAclProfilePublishTopicException,getMsgVpnAclProfileSubscribeTopicExceptions,createMsgVpnAclProfileSubscribeTopicException,getMsgVpnTopicEndpoints,getMsgVpnTopicEndpoint,createMsgVpnTopicEndpoint,updateMsgVpnTopicEndpoint,deleteMsgVpnTopicEndpoint,getMsgVpnBridges,getMsgVpnBridge"
```

This reduces from 456 to 35 tools (~61K to ~4K tokens of tool
definitions). The 35 tools cover:

| Category | Tools included |
| --- | --- |
| VPNs | `getMsgVpns`, `getMsgVpn` |
| Queues | get, create, update, delete queue + subscriptions (7 tools) |
| Client Profiles | get list, get single, create, update (4 tools) |
| Client Usernames | get list, get single, create, update (4 tools) |
| ACL Profiles | get list, get single, create, update + connect/publish/subscribe exceptions (10 tools) |
| Topic Endpoints | get list, get single, create, update, delete (5 tools) |
| Bridges | get list, get single (2 tools) |

Set `MCP_MAX_RESPONSE_CHARS` to `10000` for GPT to balance report
detail with processing speed. See
[Recommended settings per model](#recommended-settings-per-model).

### GPT timeout tuning

Multi-tool flows like health reports require multiple SEMP queries.
After all tools execute, the LLM receives a single completion call
containing the full conversation: system prompt, instruction prompt,
tool definitions, and all tool-call results. For GPT, this accumulated
context can be large enough that the model takes longer than LiteLLM's
default 120-second timeout to produce the first token, resulting in:

```
litellm.Timeout: APITimeoutError - Request timed out. LiteLLM Retried: 3 times
```

**Fix 1 -- Increase the LLM timeout.** Add `timeout` to the model
section in `sam-solace-broker-mcp-agent-config.yaml`:

```yaml
model:
  model: "${LLM_SERVICE_GENERAL_MODEL_NAME}"
  api_base: "${LLM_SERVICE_ENDPOINT}"
  api_key: "${LLM_SERVICE_API_KEY}"
  timeout: 300   # seconds (default is 120)
```

A value of 300 (5 minutes) is a safe starting point.

**Fix 2 -- Reduce context pressure.** Set `MCP_MAX_RESPONSE_CHARS` to
`10000` in the secret YAML. This keeps enough data for anomaly
detection while keeping the synthesis call under ~2 minutes.
Additionally, uncomment `ENABLE_EMBED_RESOLUTION: "false"` and
`ENABLE_ARTIFACT_CONTENT_INSTRUCTION: "false"` in the secret YAML to
prevent artifact content from being injected into the LLM context,
which can significantly reduce prompt size. See
[Recommended settings per model](#recommended-settings-per-model) for
the full set of GPT-optimised values.

**Fix 3 -- Switch model (recommended).** Claude and Gemini process
large tool-result contexts much faster than GPT. A health report that
times out on GPT typically completes in 30-60 seconds on Claude 3.5
Sonnet. See [LLM compatibility and recommendations](#llm-compatibility-and-recommendations).

### Claude-specific notes

Claude works with the full tool set out of the box. No filtering is
needed. Claude's function-calling architecture handles hundreds of tools
without degradation. It is the recommended model for complex broker
management tasks involving large numbers of queues or multi-step
operations.

### Gemini-specific notes

Gemini works with the full tool set out of the box. No filtering is
needed. One technical detail: Gemini limits function names to 64
characters. The MCP server automatically truncates long operationIds and
appends a 4-character MD5 hash suffix to avoid name collisions (e.g.,
`getMsgVpnAclProfilePublishTopicExcept_a1b2`). This is handled
transparently.

### Recommended settings per model

The table below summarises the optimal configuration for each LLM.
A health report flow typically makes 6-10 tool calls. Each tool
response can be up to `MCP_MAX_RESPONSE_CHARS` characters. Larger caps
produce richer reports but increase the accumulated context the LLM
must process in the final synthesis call.

**Token budget estimation** (health report, 8 tool calls at max cap):

| Cap | Tokens per call | 8 calls total | Claude / Gemini | GPT-5 |
| --- | --- | --- | --- | --- |
| `25000` | ~6,250 | ~50K | Comfortable (200K-1M context) | Fits but very slow (~3-4 min) |
| `10000` | ~2,500 | ~20K | Comfortable | Good balance (~1-2 min) |

**Recommended secret YAML values per model:**

| Setting | Claude 3.5 Sonnet | Gemini 2.5 Pro | GPT-5 |
| --- | --- | --- | --- |
| `MCP_MAX_RESPONSE_CHARS` | `25000` | `25000` | `10000` |
| `MCP_API_INCLUDE_TOOLS` | *(unset -- all 456 tools)* | *(unset -- all 456 tools)* | GPT-optimised 35-tool preset |
| `ENABLE_EMBED_RESOLUTION` | `true` (default) | `true` (default) | `false` |
| `ENABLE_ARTIFACT_CONTENT_INSTRUCTION` | `true` (default) | `true` (default) | `false` |

**Recommended config YAML values (all models):**

| Setting | Value | Notes |
| --- | --- | --- |
| `timeout` | `300` | In the `model:` section. Prevents LiteLLM timeout on slow models. Safe for all models (fast models finish well under this). |

---

## Server-Side Response Optimisations

The MCP server applies several optimisations to SEMP responses before
returning them to the LLM to reduce token usage and prevent context
overflow:

### Response trimming (list endpoints)

For list GET endpoints (queues, clients, VPNs, flows, connections), the
server strips each object down to the most operationally relevant
fields. This prevents the LLM from being overwhelmed by dozens of
rarely-needed fields per object.

Single-object GET endpoints (e.g., `getMsgVpnQueue` for one specific
queue) are **not** trimmed -- the LLM receives all fields so it can
answer questions about TLS, authentication, MQTT settings, replication,
etc.

### Links stripping

SEMP responses include a `links` object with sub-resource URIs on every
object. These are never useful to the LLM and are always stripped.

### Parameter suppression

Two SEMP query parameters are hidden from the LLM:

- `opaquePassword` -- config-only, never needed by agents.
- `select` -- LLMs consistently guess wrong field names, causing 400
  errors. Server-side trimming handles payload reduction instead.

### Character cap

All tool responses are capped at `MCP_MAX_RESPONSE_CHARS` (default
`25000`). If a response exceeds the cap, it is truncated with a
message: `...TRUNCATED (N chars). Use count or where params to narrow
results.`

### Compact output

All JSON responses use compact separators (`","` and `":"`) with no
indentation to minimise token usage.

---

## Agent Instruction Prompt

The agent instruction prompt (in `sam-solace-broker-mcp-agent-config.yaml`)
teaches the LLM how to use the SEMP tools correctly. It enforces 5
rules:

1. **Paginate completely** -- Follow `meta.paging.cursorQuery` across
   all pages before analysing. Use `count=100` to reduce round-trips.
2. **Use filters** -- Apply `where` filters and `count` on list calls
   to narrow results.
3. **Retry on error** (max 3 attempts) -- Read error hints, fix
   parameters, retry.
4. **Real data only** -- Never invent values. Report "Unavailable" if
   data cannot be retrieved.
5. **Confirm writes** -- Before any create/update/delete, state the
   exact change and wait for user confirmation.

The response format section instructs the LLM to use tables for lists,
sort by severity, aggregate healthy items, and name specific resources.

---

## Switch LLM Model

Edit `sam-solace-broker-mcp-agent-secret.yaml` -- uncomment the desired
model:

```yaml
# Gemini (default)
LLM_SERVICE_GENERAL_MODEL_NAME: "openai/gemini-2.5-pro"

# Claude
#LLM_SERVICE_GENERAL_MODEL_NAME: "openai/claude-3-5-sonnet-v2"

# GPT
#LLM_SERVICE_GENERAL_MODEL_NAME: "openai/azure-gpt-5-mini"
```

Then re-apply:

```bash
kubectl apply -f sam-solace-broker-mcp-agent-secret.yaml
kubectl -n sam-ent-k8s-agents rollout restart deployment/sam-solace-broker-mcp-agent
```

## Switch SEMP Spec (Monitor vs Config)

Edit the `OPENAPI_SPEC` value in
`sam-solace-broker-mcp-agent-secret.yaml`:

```yaml
# Config spec (456 tools -- read + write)
OPENAPI_SPEC: "/opt/solace-broker-mcp-server/semp-v2-swagger-config.json"

# Monitor spec (234 tools -- read-only)
#OPENAPI_SPEC: "/opt/solace-broker-mcp-server/semp-v2-swagger-monitor.json"
```

---

## Troubleshooting

### Check MCP server logs

```bash
kubectl -n sam-ent-k8s-agents exec deployment/sam-solace-broker-mcp-agent -- \
  cat /opt/solace-broker-mcp-server/solace_broker_mcp_server.log
```

### Enable debug logging

Set `MCP_LOG_LEVEL: "DEBUG"` in the secret YAML to see:

- Every tool that was filtered out (and why)
- Every SEMP HTTP request (method, URL, query params)
- Full error details for failed requests

### Common issues

| Symptom | Cause | Fix |
| --- | --- | --- |
| Agent starts but LLM never calls tools | GPT with too many tools (token overload) | Enable GPT tool filtering preset |
| `litellm.Timeout: APITimeoutError` after tools execute successfully | GPT too slow processing large accumulated tool results (health reports, multi-step queries) | Increase `timeout` to 300 in model config, lower `MCP_MAX_RESPONSE_CHARS`, or switch to Claude/Gemini. See [GPT timeout tuning](#gpt-timeout-tuning). |
| `401/403` errors in MCP logs | Wrong SEMP credentials | Check `SOLACE_SEMPV2_USERNAME` / `PASSWORD` |
| `404` errors | Wrong VPN name or resource name | Check `msgVpnName` / `queueName` spelling |
| `Connection refused` | MCP server can't reach broker | Check `SOLACE_SEMPV2_BASE_URL` (use `host.docker.internal` from Docker) |
| Truncated responses | Response exceeds character cap | Increase `MCP_MAX_RESPONSE_CHARS` or use `where` filters |
| Tool names truncated with hash | Gemini 64-char function name limit | Normal behaviour. No action needed. |
