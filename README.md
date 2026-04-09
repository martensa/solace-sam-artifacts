# Solace Agent Mesh -- Deployment Artifacts

Multi-agent system on Kubernetes using
[Solace Agent Mesh (SAM) Enterprise](https://solaceproducts.github.io/solace-agent-mesh-helm-quickstart/docs/).
Agents communicate via Solace PubSub+ event broker and are
orchestrated by a gateway that discovers agents through agent cards
published on the mesh. This repository contains all deployment
manifests, agent configurations, custom tool implementations, and
shared service definitions.

## Table of Contents

- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Agents](#agents)
  - [Core Agents](#core-agents)
  - [External Agents](#external-agents)
  - [Agent Builder Agents](#agent-builder-agents)
  - [Workflows](#workflows)
- [Shared Services](#shared-services)
- [Platform Deployment](#platform-deployment)
- [Environment](#environment)
- [Getting Started](#getting-started)
- [Testing Agents](#testing-agents)
- [Contributing](#contributing)

## Architecture

```text
                         +-----------+
                         |   User    |
                         +-----+-----+
                               |
                               v
+--------------------------------------------------------------+
|  sam-ent-k8s namespace                                       |
|                                                              |
|  +-------------+     +----------------+     +-----------+    |
|  | Gateway     |---->| Orchestrator   |---->| PubSub+   |    |
|  | (REST API)  |     | (agent card    |     | Broker    |    |
|  | port 80     |     |  discovery)    |     |           |    |
|  +-------------+     +----------------+     +-----+-----+    |
|                                                   |          |
|  +-------------+                                  |          |
|  | SearXNG     |                                  |          |
|  | (meta-search|                                  |          |
|  |  engine)    |                                  |          |
|  +-------------+                                  |          |
+--------------------------------------------------------------+
                                                    |
+--------------------------------------------------------------+
|  sam-ent-k8s-agents namespace                                |
|                                                              |
|  +------------------+  +------------------+                  |
|  | Web Research     |  | Article          |                  |
|  | Agent            |  | Verification     |                  |
|  +------------------+  +------------------+                  |
|  +------------------+  +------------------+                  |
|  | Web Scraper      |  | EAN Search       |                  |
|  | Agent            |  | Agent            |                  |
|  +------------------+  +------------------+                  |
|  +------------------+  +------------------+                  |
|  | Price Comparison |  | Datadog MCP      |                  |
|  | Agent            |  | Agent            |                  |
|  +------------------+  +------------------+                  |
|                                                              |
+--------------------------------------------------------------+
```

The orchestrator discovers agents automatically via agent cards
published every 10 seconds on the Solace event broker. No manual
routing configuration is required.

## Repository Structure

```text
solace-sam-artifacts/
|-- Agents/
|   |-- Core Agents/              # Gateway, orchestrator, core
|   |-- External Agents/          # Custom agents (K8s manifests)
|   |   |-- Article Verification Agent/
|   |   |-- Datadog MCP Agent/
|   |   |-- EAN Agent/
|   |   |-- Price Comparison Agent/
|   |   |-- Solace Broker MCP Agent/
|   |   |-- Web Research Agent/
|   |   +-- Web Scraper Agent/
|   |-- Agent Builder Agents/     # Agents managed via SAM UI
|   |   +-- Contract Management Agent/
|   +-- Workflows/                # Multi-agent workflows
|       +-- procurement-workflow.yaml
|-- Deployment/
|   |-- Helm/                     # SAM platform Helm chart config
|   +-- SearXNG/                  # Shared meta-search engine
|-- CLAUDE.md                     # Developer reference (env, patterns)
+-- README.md                     # This file
```

## Agents

### Core Agents

Deployed as part of the SAM platform Helm chart. Manifests in
[Agents/Core Agents/](Agents/Core%20Agents/).

| Agent | Role |
|-------|------|
| Gateway | REST API for external access (A2A protocol) |
| Orchestrator | Discovers agents, routes requests |
| MarkitdownAgent | Converts files to Markdown |
| MermaidAgent | Generates Mermaid diagrams |

### External Agents

Custom agents deployed as separate K8s pods in `sam-ent-k8s-agents`.
Each has its own `deploy/` directory with ConfigMap, Secret, and
Deployment manifests.

<!-- markdownlint-disable MD013 -->
| Agent | Tools | Description | Docs |
|-------|-------|-------------|------|
| [Web Research Agent](Agents/External%20Agents/Web%20Research%20Agent/) | Builtin (web, data_analysis) | General-purpose web research via SearXNG meta-search. Fallback when specialized agents cannot handle a request. | [README](Agents/External%20Agents/Web%20Research%20Agent/README.md) |
| [Article Verification Agent](Agents/External%20Agents/Article%20Verification%20Agent/) | MCP (check_article, search_article) | Verifies product articles against manufacturer databases via SearXNG search. | [README](Agents/External%20Agents/Article%20Verification%20Agent/README.md) |
| [Web Scraper Agent](Agents/External%20Agents/Web%20Scraper%20Agent/) | MCP (5 Playwright tools) | Headless browser for bot-protected sites. Fetches pages, downloads images and files, takes screenshots. | [README](Agents/External%20Agents/Web%20Scraper%20Agent/README.md) |
| [EAN Search Agent](Agents/External%20Agents/EAN%20Agent/) | MCP (4 EAN tools) | Barcode database lookup via ean-search.org and UPCitemdb. | [README](Agents/External%20Agents/EAN%20Agent/README.md) |
| [Price Comparison Agent](Agents/External%20Agents/Price%20Comparison%20Agent/) | Python (6 price tools) | Scrapes consumer prices from Idealo, Geizhals, and Google Shopping. | [README](Agents/External%20Agents/Price%20Comparison%20Agent/README.md) |
| [Datadog MCP Agent](Agents/External%20Agents/Datadog%20MCP%20Agent/) | MCP (73 Datadog tools) | Datadog monitoring integration for dashboards, metrics, and alerts. | [README](Agents/External%20Agents/Datadog%20MCP%20Agent/README.md) |
| [Solace Broker MCP Agent](Agents/External%20Agents/Solace%20Broker%20MCP%20Agent/) | MCP (456 SEMP v2 tools) | Solace PubSub+ broker management via SEMP v2 API. | [README](Agents/External%20Agents/Solace%20Broker%20MCP%20Agent/README.md) |
<!-- markdownlint-enable MD013 -->

### Agent Builder Agents

Agents created and managed via the SAM Agent Builder UI. Configuration
is stored as reference documentation, not as K8s manifests.

| Agent | Description | Docs |
|-------|-------------|------|
| [Contract Management Agent](Agents/Agent%20Builder%20Agents/Contract%20Management%20Agent/) | Contract lifecycle management with database backend | [Spec](Agents/Agent%20Builder%20Agents/Contract%20Management%20Agent/Contract%20Management%20Agent.md) |

### Workflows

Declarative multi-agent workflows that encode task sequences.
The orchestrator discovers workflows via agent cards, just like
individual agents.

| Workflow | Description | Status |
|----------|-------------|--------|
| [Procurement Workflow](Agents/Workflows/procurement-workflow.yaml) | Enriches articles, verifies EANs, compares prices, finds images, compiles report | Planned |

## Shared Services

Services deployed in `sam-ent-k8s` namespace, shared across all agents.

| Service | Purpose | Docs |
|---------|---------|------|
| [SearXNG](Deployment/SearXNG/) | Self-hosted meta-search engine (Google + Bing + DuckDuckGo). JSON API for agents. | [README](Deployment/SearXNG/README.md) |
| SeaweedFS | S3-compatible artifact storage for agent outputs | Deployed via Helm |
| PostgreSQL | Session and state persistence for agents | Deployed via Helm |

## Platform Deployment

The SAM platform is deployed via Helm chart. See
[Deployment/Helm/](Deployment/Helm/) for configuration.

```bash
# Install SAM platform
helm repo add solace-agent-mesh \
  https://solaceproducts.github.io/solace-agent-mesh-helm-quickstart/
helm install agent-mesh solace-agent-mesh/solace-agent-mesh \
  -f local-k8s-values.yaml --namespace sam-ent-k8s
```

For the full installation guide including image import and
namespace setup, see [Deployment/Helm/README.md](Deployment/Helm/README.md).

## Environment

| Component | Value |
|-----------|-------|
| K8s namespaces | `sam-ent-k8s` (platform), `sam-ent-k8s-agents` (agents) |
| Base image | `localhost:5000/solace-agent-mesh-enterprise:1.97.2` |
| LLM proxy | LiteLLM at `https://lite-llm.mymaas.net` |
| Default model | `openai/claude-sonnet-4-6` (via LiteLLM) |
| Artifact storage | SeaweedFS (S3-compatible) |
| Event broker | Solace PubSub+ (`ws://host.docker.internal:8008`) |
| Search engine | SearXNG (`http://searxng.sam-ent-k8s.svc.cluster.local:8080`) |

## Getting Started

### Prerequisites

- Kubernetes cluster (Rancher Desktop or similar)
- Helm 3
- Docker with local registry at `localhost:5000`
- Solace PubSub+ broker (deployed via Helm or standalone)

### Deploy an Agent

Each external agent follows the same pattern:

```bash
cd "Agents/External Agents/<Agent Name>/"

# Build custom image (skip for agents using base image only)
docker build -t localhost:5000/sam-<slug>-agent:1.0.0 .
docker push localhost:5000/sam-<slug>-agent:1.0.0

# Apply K8s manifests
kubectl apply -f deploy/sam-<slug>-agent-secret.yaml
kubectl apply -f deploy/sam-<slug>-agent-config.yaml
kubectl apply -f deploy/sam-<slug>-agent-deployment.yaml

# Verify
kubectl get pods -n sam-ent-k8s-agents | grep <slug>
```

Agents using only builtin tools (e.g., Web Research Agent) do not
require a custom Docker image -- they use the base SAM Enterprise
image directly.

### Deploy Shared Services

```bash
# SearXNG meta-search engine
kubectl apply -f Deployment/SearXNG/searxng-configmap.yaml
kubectl apply -f Deployment/SearXNG/searxng-deployment.yaml
```

## Testing Agents

Port-forward the gateway and send requests via JSON-RPC 2.0:

```bash
kubectl port-forward svc/agent-mesh 8081:80 -n sam-ent-k8s
```

```bash
curl -s -X POST http://localhost:8081/api/v1/message:send \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-001",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "Your prompt here"
        }],
        "messageId": "msg-001",
        "metadata": {
          "agent_name": "AgentNameHere"
        }
      }
    }
  }'
```

Retrieve results (may take 30-120 seconds):

```bash
curl -s http://localhost:8081/api/v1/tasks/<task_id>
```

The response is in YAML format. The agent's answer is in
`invocation_flow` -> events with `direction: response` ->
`payload.result.status.message.parts[].text`.

## Contributing

- **ASCII-only** in all files (no unicode dashes, arrows, or
  umlauts in code)
- **English** for code, comments, tool descriptions, and agent
  instructions
- **German** is acceptable in user-facing output (reports, display
  names for German workflows)
- Keep deploy YAMLs structurally consistent across agents
- Agent instructions should include speed constraints (max tool
  calls per request)
- See [CLAUDE.md](CLAUDE.md) for detailed developer reference
  including environment setup, common patterns, and pitfalls
