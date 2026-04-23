# Solace Agent Mesh -- Deployment Artifacts

Multi-agent system on Kubernetes using
[Solace Agent Mesh (SAM) Enterprise](https://solaceproducts.github.io/solace-agent-mesh-helm-quickstart/docs/).
Agents communicate via Solace PubSub+ event broker and are
orchestrated by a gateway that discovers agents through agent cards
published on the mesh. This repository contains all deployment
manifests, agent configurations, custom tool implementations, and
shared service definitions.

**Documentation:**
[Getting Started](https://solacelabs.github.io/solace-agent-mesh/docs/documentation/getting-started/) |
[Enterprise Edition](https://solacelabs.github.io/solace-agent-mesh/docs/documentation/enterprise/)

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
|  sam-solace-lab namespace (core platform)                    |
|                                                              |
|  +-------------+     +----------------+     +-----------+    |
|  | Gateway     |---->| Orchestrator   |---->| PubSub+   |    |
|  | (REST API)  |     | (agent card    |     | Broker    |    |
|  | port 80     |     |  discovery)    |     |           |    |
|  +-------------+     +----------------+     +-----+-----+    |
+--------------------------------------------------------------+
                                                    |
+--------------------------------------------------------------+
|  sam-solace-lab-agents namespace                             |
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
+--------------------------------------------------------------+
                                                    |
+--------------------------------------------------------------+
|  sam-solace-lab-workflows namespace                          |
|                                                              |
|  +----------------------+                                    |
|  | Procurement Workflow |                                    |
|  +----------------------+                                    |
+--------------------------------------------------------------+
                                                    |
+--------------------------------------------------------------+
|  sam-solace-lab-shared namespace                             |
|                                                              |
|  +-------------+                                             |
|  | SearXNG     |                                             |
|  | (meta-search|                                             |
|  |  engine)    |                                             |
|  +-------------+                                             |
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
|   |   |-- namespace.yaml        # sam-solace-lab-agents namespace
|   |   |-- Article Verification Agent/
|   |   |-- Datadog MCP Agent/
|   |   |-- EAN Agent/
|   |   |-- Price Comparison Agent/
|   |   |-- Solace Broker MCP Agent/
|   |   |-- Web Research Agent/
|   |   +-- Web Scraper Agent/
|   +-- Agent Builder Agents/     # Agents managed via SAM UI
|       +-- Contract Management Agent/
|-- Workflows/                    # Multi-agent workflows (sequences of agents)
|   |-- namespace.yaml            # sam-solace-lab-workflows namespace
|   +-- procurement-workflow.yaml
|-- Shared Services/
|   |-- namespace.yaml            # sam-solace-lab-shared namespace
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

Custom agents deployed as separate K8s pods in `sam-solace-lab-agents`.
Each has its own `deploy/` directory with ConfigMap, Secret, and
Deployment manifests.

<!-- markdownlint-disable MD013 -->
| Agent | Tools | Description | Docs |
|-------|-------|-------------|------|
| [Web Research Agent](Agents/External%20Agents/Web%20Research%20Agent/) | Builtin (web, data_analysis) | General-purpose web research via SearXNG meta-search. Fallback when specialized agents cannot handle a request. | [README](Agents/External%20Agents/Web%20Research%20Agent/README.md) |
| [Article Verification Agent](Agents/External%20Agents/Article%20Verification%20Agent/) | MCP (check_article, search_article) | Verifies product articles against manufacturer databases via SearXNG search. | [README](Agents/External%20Agents/Article%20Verification%20Agent/README.md) |
| [Web Scraper Agent](Agents/External%20Agents/Web%20Scraper%20Agent/) | MCP (5 Playwright tools) | Headless browser for bot-protected sites. Fetches pages, downloads images and files, takes screenshots. | [README](Agents/External%20Agents/Web%20Scraper%20Agent/README.md) |
| [EAN Search Agent](Agents/External%20Agents/EAN%20Agent/) | MCP (4 EAN tools) | Barcode database lookup via ean-search.org and UPCitemdb. | [README](Agents/External%20Agents/EAN%20Agent/README.md) |
| [Price Comparison Agent](Agents/External%20Agents/Price%20Comparison%20Agent/) | MCP (3 price tools) | B2B-first price comparison. 72 curated portals (incl. Sonepar, Rexel, Conrad, RS, Mercateo) via SearXNG + Playwright stealth. Trust-anchored outlier detection. Optional SerpAPI. | [README](Agents/External%20Agents/Price%20Comparison%20Agent/README.md) |
| [Datadog MCP Agent](Agents/External%20Agents/Datadog%20MCP%20Agent/) | MCP (73 Datadog tools) | Datadog monitoring integration for dashboards, metrics, and alerts. | [README](Agents/External%20Agents/Datadog%20MCP%20Agent/README.md) |
| [Solace Broker MCP Agent](Agents/External%20Agents/Solace%20Broker%20MCP%20Agent/) | MCP (456 SEMP v2 tools) | Solace PubSub+ broker management via SEMP v2 API. | [README](Agents/External%20Agents/Solace%20Broker%20MCP%20Agent/README.md) |
<!-- markdownlint-enable MD013 -->

### Agent Builder Agents

Agents created and managed via the SAM Agent Builder UI. Configuration
is stored as reference documentation, not as K8s manifests.

| Agent | Description | Docs |
|-------|-------------|------|
| [Contract Management Agent](Agents/Agent%20Builder%20Agents/Contract%20Management%20Agent/) | Contract lifecycle management with database backend | [Spec](Agents/Agent%20Builder%20Agents/Contract%20Management%20Agent/Contract%20Management%20Agent.md) |

## Workflows

Declarative multi-agent workflows that define sequences of agents.
A workflow is discovered by the orchestrator via agent cards, just like
individual agents, but orchestrates multiple agents in a defined order.

| Workflow | Description | Status |
|----------|-------------|--------|
| [Procurement Workflow](Workflows/procurement-workflow.yaml) | Enriches articles, verifies EANs, compares prices, finds images, compiles report | Planned |

## Shared Services

Services deployed in `sam-solace-lab` namespace, shared across all agents.

| Service | Purpose | Docs |
|---------|---------|------|
| [SearXNG](Shared%20Services/SearXNG/) | Self-hosted meta-search engine (Google + Bing + DuckDuckGo). JSON API for agents. | [README](Shared%20Services/SearXNG/README.md) |
| SeaweedFS | S3-compatible artifact storage for agent outputs | Deployed via Helm |
| PostgreSQL | Session and state persistence for agents | Deployed via Helm |

## Platform Deployment

This repository assumes the SAM platform is already running. It contains
only agent source code, configurations, per-agent `deploy/` manifests,
and shared service definitions (e.g. SearXNG).

The platform is set up through two upstream repositories:

| Repository | Purpose |
|------------|---------|
| [solace-lab-infrastructure](https://github.com/martensa/solace-lab-infrastructure) | Base infrastructure for Solace Event Mesh and Solace Agent Mesh (K8s cluster, networking, storage, etc.) |
| [solace-demo-artifacts](https://github.com/martensa/solace-demo-artifacts) | Deployment artifacts for the full Solace demo environment (Event Mesh, Agent Mesh, Distributed Tracing, Kafka Bridge, Event Portal, Schema Registry) |

The SAM-specific deployment (Helm chart, broker, orchestrator, gateway)
that is the direct prerequisite for this repository lives at:
[solace-demo-artifacts/agent-mesh-deployment](https://github.com/martensa/solace-demo-artifacts/tree/master/agent-mesh-deployment)

## Environment

| Component | Value |
|-----------|-------|
| K8s namespaces | `sam-solace-lab` (platform), `sam-solace-lab-agents` (agents), `sam-solace-lab-workflows` (workflows), `sam-solace-lab-shared` (shared services) |
| Base image | `registry.solace.lab/solace-agent-mesh-enterprise:1.97.2` |
| LLM proxy | LiteLLM at `https://lite-llm.mymaas.net` |
| Default model | `openai/claude-sonnet-4-6` (via LiteLLM) |
| Artifact storage | SeaweedFS (S3-compatible) |
| Event broker | Solace PubSub+ (`ws://host.docker.internal:8008`) |
| Search engine | SearXNG (`http://searxng.sam-solace-lab-shared.svc.cluster.local:8080`) |

## Getting Started

### Prerequisites

- Infrastructure provisioned via [solace-lab-infrastructure](https://github.com/martensa/solace-lab-infrastructure)
- SAM platform deployed via [solace-demo-artifacts/agent-mesh-deployment](https://github.com/martensa/solace-demo-artifacts/tree/master/agent-mesh-deployment)
- Kubernetes cluster (Rancher Desktop or similar)
- Helm 3
- Authenticated container registry at `registry.solace.lab` (`docker login` once)

### Create Namespaces (one-time)

Create the three repository-managed namespaces:

```bash
kubectl apply -f "Agents/External Agents/namespace.yaml"
kubectl apply -f "Workflows/namespace.yaml"
kubectl apply -f "Shared Services/namespace.yaml"
```

The `sam-solace-lab` platform namespace is created by the upstream
[agent-mesh-deployment](https://github.com/martensa/solace-demo-artifacts/tree/master/agent-mesh-deployment)
repository.

### Deploy an Agent

Each external agent follows the same pattern:

```bash
cd "Agents/External Agents/<Agent Name>/"

# Build custom image (skip for agents using base image only)
docker build -t registry.solace.lab/sam-<slug>-agent:1.0.0 .
docker push registry.solace.lab/sam-<slug>-agent:1.0.0

# Apply K8s manifests
kubectl apply -f deploy/sam-<slug>-agent-secret.yaml
kubectl apply -f deploy/sam-<slug>-agent-config.yaml
kubectl apply -f deploy/sam-<slug>-agent-deployment.yaml

# Verify
kubectl get pods -n sam-solace-lab-agents | grep <slug>
```

Agents using only builtin tools (e.g., Web Research Agent) do not
require a custom Docker image -- they use the base SAM Enterprise
image directly.

### Deploy Shared Services

```bash
# SearXNG meta-search engine
kubectl apply -f "Shared Services/SearXNG/searxng-configmap.yaml"
kubectl apply -f "Shared Services/SearXNG/searxng-deployment.yaml"
```

## Testing Agents

Port-forward the gateway and send requests via JSON-RPC 2.0:

```bash
kubectl port-forward svc/agent-mesh 8081:80 -n sam-solace-lab
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
