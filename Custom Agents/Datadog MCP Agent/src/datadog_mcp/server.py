"""Datadog MCP Server v1.0.0 - entry point.

Exposes 73 Datadog monitoring and management tools organized around
the SRE operational lifecycle: OBSERVE > ANALYZE > ALERT > RESPOND > REVIEW.

Designed for integration with Solace Agent Mesh and other MCP clients.
Compatible with Claude, Gemini, and GPT model families.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from datadog_mcp.config import DatadogConfig
from datadog_mcp.client import DatadogClient
from datadog_mcp.tools import (
    # OBSERVE stage (28 tools)
    metrics,        # query_metrics, list_metrics, get_metric_metadata, list_metric_tags, submit_custom_metrics
    logs,           # search_logs, aggregate_logs, list_log_indexes
    apm,            # search_spans, aggregate_spans
    rum,            # list_rum_applications, search_rum_events, aggregate_rum_events
    events,         # search_events, create_event
    hosts,          # list_hosts, get_host_totals, get_host_tags, add_host_tags, mute_host, unmute_host
    containers,     # list_containers, list_container_images
    processes,      # list_processes
    network,        # get_network_connections, get_network_dns
    cicd,           # search_ci_pipeline_events, aggregate_ci_pipelines
    security,       # search_security_signals, get_security_signal, triage_security_signal
    audit,          # search_audit_logs
    # ANALYZE stage (11 tools)
    services,       # list_services, get_service_definition, get_service_dependencies
    dashboards,     # list_dashboards, get_dashboard, get_graph_snapshot
    notebooks,      # list_notebooks, get_notebook
    error_tracking, # search_error_tracking_issues, update_error_tracking_issue
    composite,      # get_service_health (Four Golden Signals)
    # ALERT stage (15 tools)
    monitors,       # list_monitors, get_monitor, create_monitor, update_monitor, delete_monitor, mute_monitor, unmute_monitor
    slos,           # list_slos, get_slo, get_slo_history, create_slo
    synthetics,     # list_synthetics_tests, get_synthetics_test, get_synthetics_results (+ trigger in RESPOND)
    # RESPOND stage (13 tools)
    incidents,      # list_incidents, get_incident, create_incident, update_incident
    downtimes,      # list_downtimes, create_downtime, cancel_downtime
    teams_oncall,   # list_teams, list_oncall_schedules, get_current_oncall
    workflows,      # list_workflows, trigger_workflow
    # REVIEW stage (3 tools)
    cost_usage,     # get_usage_summary, get_estimated_cost, get_hourly_usage
)

SERVER_NAME = "datadog-mcp-server"
SERVER_VERSION = "1.0.0"

mcp = FastMCP(
    name=SERVER_NAME,
    instructions=(
        "Datadog monitoring server -- 73 tools across the SRE lifecycle.\n\n"
        "RESPONSE FORMAT: Every tool returns {summary, data}. "
        "Paginated results add {page, has_more, total_count}.\n"
        "TIME PARAMS: Accept relative ('now-1h', 'now-7d') or ISO 8601. "
        "Metrics use from_seconds_ago (3600=1h).\n\n"
        "QUICK START:\n"
        "- Service health: get_service_health (Four Golden Signals in one call)\n"
        "- Investigation: search_logs/search_spans, then aggregate_* for patterns\n"
        "- Alerting status: list_monitors with status/tag filters\n"
        "- Incidents: list_incidents then get_incident\n"
        "- Costs: get_estimated_cost or get_usage_summary"
    ),
)


def _init() -> DatadogClient:
    """Initialise config + client, fail fast if credentials are missing."""
    try:
        config = DatadogConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
    return DatadogClient(config)


dd = _init()

# Register all tool modules
ALL_MODULES = [
    metrics, logs, apm, rum, events, hosts, containers, processes, network,
    cicd, security, audit, services, dashboards, notebooks, error_tracking,
    composite, monitors, slos, synthetics, incidents, downtimes, teams_oncall,
    workflows, cost_usage,
]

for module in ALL_MODULES:
    module.register(mcp, dd)


def main() -> None:
    """Run the MCP server (default: stdio transport)."""
    import argparse

    parser = argparse.ArgumentParser(description="Datadog MCP Server v1.0")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport type (default: stdio)",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Port for HTTP-based transports (default: 8080)",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)
    elif args.transport == "sse":
        mcp.run(transport="sse", host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
