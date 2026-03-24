# Datadog MCP Server v1.0

A Python MCP (Model Context Protocol) server that wraps the
Datadog REST API, providing **73 monitoring and management tools**
organized around the SRE operational lifecycle. Designed for
integration with [Solace Agent Mesh][sam] and other MCP-compatible
clients.

Compatible with **Claude**, **Gemini**, and **GPT** model families.

[sam]: https://solacelabs.github.io/solace-agent-mesh/

## Quick Start

### 1. Install

```bash
pip install .
```

Or with [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv venv && uv pip install .
```

### 2. Configure

```bash
export DD_API_KEY="your-api-key"
export DD_APP_KEY="your-application-key"
export DD_SITE="datadoghq.com"
```

Get your API and Application keys from
[Datadog Organization Settings][dd-keys].

[dd-keys]: https://app.datadoghq.com/organization-settings/api-keys

### 3. Run

```bash
# stdio transport (default, for SAM or MCP clients)
datadog-mcp-server

# HTTP transport (for remote deployment)
datadog-mcp-server --transport streamable-http --port 8080

# SSE transport
datadog-mcp-server --transport sse --port 8080
```

## Solace Agent Mesh Integration

### Local Development

```bash
sam run sam/datadog_agent.yaml
```

See `sam/datadog_agent.yaml` for configuration options including
stdio, streamable-http, and SSE transports, plus a read-only
tool allow-list for restricted environments.

### Kubernetes Deployment

Build and push the Docker image:

```bash
docker build -t localhost:5000/sam-datadog-mcp-agent:1.0.0 .
docker push localhost:5000/sam-datadog-mcp-agent:1.0.0
```

Deploy to your SAM cluster:

```bash
# Edit the secret with your actual credentials first
vi deploy/sam-datadog-mcp-agent-secret.yaml

# Apply all manifests
kubectl apply -f deploy/
```

The deployment consists of three Kubernetes manifests in
`deploy/`:

| File                                    | Purpose                        |
| --------------------------------------- | ------------------------------ |
| `sam-datadog-mcp-agent-secret.yaml`     | Credentials and env vars       |
| `sam-datadog-mcp-agent-configmap.yaml`  | SAM agent YAML configuration   |
| `sam-datadog-mcp-agent-deployment.yaml` | Kubernetes Deployment          |

The agent runs inside the SAM Enterprise base image. The MCP
server is invoked via stdio transport as a subprocess, and
registers itself on the Solace event mesh for agent-to-agent
communication.

## Configuration

### Environment Variables

| Variable     | Required | Default         | Description             |
| ------------ | -------- | --------------- | ----------------------- |
| `DD_API_KEY` | Yes      | --              | Datadog API key         |
| `DD_APP_KEY` | Yes      | --              | Datadog Application key |
| `DD_SITE`    | No       | `datadoghq.com` | Datadog site region     |

Supported `DD_SITE` values:

- `datadoghq.com` (US1)
- `datadoghq.eu` (EU1)
- `us3.datadoghq.com` (US3)
- `us5.datadoghq.com` (US5)
- `ap1.datadoghq.com` (AP1)
- `ddog-gov.com` (US1-FED / Gov)

### GPT Compatibility

GPT models may degrade with more than 50 tools. For GPT, use the
read-only tool subset (~40 tools) documented in
`sam/datadog_agent.yaml` under the `allow_list` section, or set
`ENABLE_EMBED_RESOLUTION=false` and
`ENABLE_ARTIFACT_CONTENT_INSTRUCTION=false` to reduce prompt size.

Claude and Gemini handle all 73 tools without issues.

## Tool Reference

73 tools across 5 SRE lifecycle stages.

### OBSERVE (28 tools)

Data collection and visibility.

| Tool                          | Description                        |
| ----------------------------- | ---------------------------------- |
| `query_metrics`               | Query timeseries metric data       |
| `list_metrics`                | Search available metric names      |
| `get_metric_metadata`         | Get metric type, unit, description |
| `list_metric_tags`            | List tag keys for a metric         |
| `submit_custom_metrics`       | Submit custom metric data points   |
| `search_logs`                 | Search logs using query syntax     |
| `aggregate_logs`              | Aggregate log analytics            |
| `list_log_indexes`            | List log indexes with retention    |
| `search_spans`                | Search distributed trace spans     |
| `aggregate_spans`             | Aggregate span analytics           |
| `list_rum_applications`       | List RUM applications              |
| `search_rum_events`           | Search RUM events                  |
| `aggregate_rum_events`        | Aggregate RUM analytics            |
| `search_events`               | Search Datadog events (v2)         |
| `create_event`                | Post a custom event                |
| `list_hosts`                  | List hosts with filtering          |
| `get_host_totals`             | Get host up/down summary counts    |
| `get_host_tags`               | Get all tags for a host            |
| `add_host_tags`               | Add tags to a host                 |
| `mute_host`                   | Mute a host                        |
| `unmute_host`                 | Unmute a host                      |
| `list_containers`             | List running containers            |
| `list_container_images`       | List container images              |
| `list_processes`              | List running processes             |
| `get_network_connections`     | Get network flow connections       |
| `get_network_dns`             | Get DNS resolution data            |
| `search_ci_pipeline_events`   | Search CI/CD pipeline events       |
| `aggregate_ci_pipelines`      | Aggregate CI/CD analytics          |

### OBSERVE -- Security and Compliance (4 tools)

| Tool                          | Description                        |
| ----------------------------- | ---------------------------------- |
| `search_security_signals`     | Search security signals            |
| `get_security_signal`         | Get security signal details        |
| `triage_security_signal`      | Update signal triage state         |
| `search_audit_logs`           | Search audit trail logs            |

### ANALYZE (11 tools)

Investigation and understanding.

| Tool                            | Description                      |
| ------------------------------- | -------------------------------- |
| `list_services`                 | List service catalog entries     |
| `get_service_definition`        | Get service definition           |
| `get_service_dependencies`      | Get service dependency map       |
| `list_dashboards`               | List dashboards with search      |
| `get_dashboard`                 | Get dashboard widgets and layout |
| `get_graph_snapshot`            | Capture graph as image URL       |
| `list_notebooks`                | List notebooks and runbooks      |
| `get_notebook`                  | Get notebook content and cells   |
| `search_error_tracking_issues`  | Search error tracking issues     |
| `update_error_tracking_issue`   | Update issue status              |
| `get_service_health`            | Four Golden Signals summary      |

### ALERT (15 tools)

Detection and notification.

| Tool                          | Description                        |
| ----------------------------- | ---------------------------------- |
| `list_monitors`               | List and search monitors           |
| `get_monitor`                 | Get monitor details and config     |
| `create_monitor`              | Create a new monitor               |
| `update_monitor`              | Update monitor configuration       |
| `delete_monitor`              | Delete a monitor                   |
| `mute_monitor`                | Mute monitor notifications         |
| `unmute_monitor`              | Unmute monitor notifications       |
| `list_slos`                   | List Service Level Objectives      |
| `get_slo`                     | Get SLO configuration and status   |
| `get_slo_history`             | Get SLO history and error budget   |
| `create_slo`                  | Create a new SLO                   |
| `list_synthetics_tests`       | List synthetic tests               |
| `get_synthetics_test`         | Get synthetic test configuration   |
| `get_synthetics_results`      | Get synthetic test results         |
| `trigger_synthetics_test`     | Trigger on-demand synthetic test   |

### RESPOND (13 tools)

Incident response and remediation.

| Tool                          | Description                        |
| ----------------------------- | ---------------------------------- |
| `list_incidents`              | List incidents with status         |
| `get_incident`                | Get incident details and timeline  |
| `create_incident`             | Create a new incident              |
| `update_incident`             | Update incident status/severity    |
| `list_downtimes`              | List maintenance windows           |
| `create_downtime`             | Schedule a new downtime            |
| `cancel_downtime`             | Cancel a scheduled downtime        |
| `list_teams`                  | List teams in the organization     |
| `list_oncall_schedules`       | List on-call schedules             |
| `get_current_oncall`          | Get who is currently on-call       |
| `list_workflows`              | List workflow automations          |
| `trigger_workflow`            | Trigger a workflow on-demand       |
| `trigger_synthetics_test`     | (shared with ALERT)                |

### REVIEW (3 tools)

Cost and usage analysis.

| Tool                          | Description                        |
| ----------------------------- | ---------------------------------- |
| `get_usage_summary`           | Get usage summary across products  |
| `get_estimated_cost`          | Get estimated monthly cost         |
| `get_hourly_usage`            | Get hourly usage by product        |

## Architecture

```text
./
|-- Dockerfile                   # Container build
|-- pyproject.toml               # Package definition
|-- uv.lock                      # Pinned dependencies
|-- LICENSE                      # MIT license
|-- deploy/                      # Kubernetes manifests
|   |-- sam-datadog-mcp-agent-configmap.yaml
|   |-- sam-datadog-mcp-agent-deployment.yaml
|   +-- sam-datadog-mcp-agent-secret.yaml
|-- sam/
|   +-- datadog_agent.yaml       # SAM local dev config
|-- src/datadog_mcp/
|   |-- server.py                # FastMCP entry point
|   |-- config.py                # Pydantic config (env vars)
|   |-- client.py                # Async httpx with retry
|   |-- tools/                   # 25 tool modules
|   +-- utils/                   # Formatting, pagination
+-- tests/                       # 92 pytest tests
```

### Response Format

Every tool returns a consistent structure:

```json
{
  "summary": "Found 12 monitors. Status: 10 OK, 2 Alert.",
  "data": [ ... ],
  "total_count": 12,
  "page": 1,
  "has_more": false
}
```

- `summary` -- human-readable one-liner for the LLM
- `data` -- structured result payload
- `total_count`, `page`, `has_more` -- present on paginated
  results

### Key Design Decisions

- **LLM-friendly responses**: Consistent `summary` + `data`
  structure across all 73 tools
- **SRE lifecycle organization**: Tools follow
  OBSERVE > ANALYZE > ALERT > RESPOND > REVIEW
- **Four Golden Signals**: `get_service_health` provides
  latency, traffic, errors, and saturation in one call
- **Lightweight client**: Async `httpx` instead of the heavy
  `datadog-api-client` SDK (faster cold start, smaller image)
- **Automatic retry**: Rate-limit (429) and 5xx retry with
  exponential backoff (3 attempts max)
- **Credential safety**: API keys masked in repr/str output,
  never logged

## Development

```bash
# Install with dev dependencies
uv venv && uv pip install -e ".[dev]"

# Run tests
pytest

# Run tests with verbose output
pytest -v
```

### Test Coverage

92 tests across 8 test files covering all 73 tools:

| Test file                  | Scope                         |
| -------------------------- | ----------------------------- |
| `test_config.py`           | Config loading, key masking   |
| `test_formatting.py`       | Response builders, helpers    |
| `test_tools_metrics.py`    | Metrics tools                 |
| `test_tools_logs.py`       | Logs tools                    |
| `test_tools_monitors.py`   | Monitor CRUD and muting       |
| `test_tools_new.py`        | Events, hosts, dashboards     |
| `test_tools_remaining.py`  | Network, teams, CI/CD, cost   |
| `test_tools_coverage.py`   | All remaining tool coverage   |

## License

MIT
