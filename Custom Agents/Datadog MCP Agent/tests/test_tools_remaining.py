"""Tests for remaining tool modules: events, hosts, dashboards, notebooks,
downtimes, synthetics, services, processes, network, cicd, audit,
teams_oncall, workflows, cost_usage.
"""

from __future__ import annotations

import pytest
import httpx
import respx

from mcp.server.fastmcp import FastMCP
from datadog_mcp.client import DatadogClient
from datadog_mcp.tools import (
    events, hosts, dashboards, notebooks, downtimes, synthetics,
    services, processes, network, cicd, audit, teams_oncall,
    workflows, cost_usage,
)


@pytest.fixture
def mcp_server() -> FastMCP:
    return FastMCP(name="test")


# ── Events ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_events(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    events.register(mcp_server, dd_client)
    mock_api.post("/api/v2/events/search").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "evt-1", "attributes": {
            "title": "Deploy started", "message": "Deploying v2.0",
            "timestamp": "2024-03-01T12:00:00Z", "tags": ["env:prod"],
            "evt": {"name": "deploy", "source": "github"},
            "status": "info",
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_events"].run({"query": "*"})
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_create_event(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    events.register(mcp_server, dd_client)
    mock_api.post("/api/v1/events").mock(return_value=httpx.Response(200, json={
        "event": {"id": 123, "title": "Test event", "text": "Event body"}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["create_event"].run({"title": "Test event", "text": "Event body"})
    assert "Test event" in result["summary"]


# ── Hosts ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_hosts(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    hosts.register(mcp_server, dd_client)
    mock_api.get("/api/v1/hosts").mock(return_value=httpx.Response(200, json={
        "host_list": [
            {"name": "web-01", "id": 1, "is_muted": False, "up": True,
             "apps": ["agent"], "tags_by_source": {}, "last_reported_time": 1700000000,
             "meta": {"platform": "linux", "agent_version": "7.50"}},
            {"name": "web-02", "id": 2, "is_muted": False, "up": False,
             "apps": [], "tags_by_source": {}, "last_reported_time": 1699990000,
             "meta": {"platform": "linux", "agent_version": "7.49"}},
        ]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_hosts"].run({})
    assert result["total_count"] == 2
    assert "1 up" in result["summary"]
    assert "1 down" in result["summary"]


@pytest.mark.asyncio
async def test_get_host_totals(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    hosts.register(mcp_server, dd_client)
    mock_api.get("/api/v1/hosts/totals").mock(return_value=httpx.Response(200, json={
        "total_up": 42, "total_active": 45
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_host_totals"].run({})
    assert "42 up" in result["summary"]


@pytest.mark.asyncio
async def test_add_host_tags(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    hosts.register(mcp_server, dd_client)
    mock_api.post("/api/v1/tags/hosts/web-01").mock(return_value=httpx.Response(200, json={
        "tags": ["env:prod", "role:web"]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["add_host_tags"].run({"host_name": "web-01", "tags": ["env:prod", "role:web"]})
    assert "2 tags" in result["summary"]


@pytest.mark.asyncio
async def test_mute_host(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    hosts.register(mcp_server, dd_client)
    mock_api.post("/api/v1/host/mute").mock(return_value=httpx.Response(200, json={
        "hostname": "web-01", "action": "Muted"
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["mute_host"].run({"host_name": "web-01", "message": "Maintenance"})
    assert "Muted" in result["summary"]
    assert "Maintenance" in result["summary"]


# ── Dashboards ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_dashboards(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    dashboards.register(mcp_server, dd_client)
    mock_api.get("/api/v1/dashboard").mock(return_value=httpx.Response(200, json={
        "dashboards": [
            {"id": "d-1", "title": "API Overview", "layout_type": "ordered",
             "url": "/dashboard/d-1", "author_handle": "admin@example.com"},
            {"id": "d-2", "title": "Infrastructure", "layout_type": "free",
             "url": "/dashboard/d-2", "author_handle": "admin@example.com"},
        ]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_dashboards"].run({})
    assert result["total_count"] == 2

    # Test with query filter
    result_filtered = await tools["list_dashboards"].run({"query": "API"})
    assert result_filtered["total_count"] == 1


@pytest.mark.asyncio
async def test_get_dashboard(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    dashboards.register(mcp_server, dd_client)
    mock_api.get("/api/v1/dashboard/d-1").mock(return_value=httpx.Response(200, json={
        "id": "d-1", "title": "API Overview", "layout_type": "ordered",
        "widgets": [{"id": 1, "definition": {"type": "timeseries", "title": "CPU"}}],
        "template_variables": [{"name": "env", "default": "prod"}],
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_dashboard"].run({"dashboard_id": "d-1"})
    assert result["data"]["widget_count"] == 1
    assert result["data"]["template_variables"][0]["name"] == "env"


# ── Notebooks ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_notebooks(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    notebooks.register(mcp_server, dd_client)
    mock_api.get("/api/v1/notebooks").mock(return_value=httpx.Response(200, json={
        "data": [{"id": 1, "attributes": {
            "name": "Incident Runbook", "author": {"handle": "admin@example.com"},
            "status": "published", "created": "2024-01-01", "modified": "2024-03-01",
            "cells": [{"id": 1}, {"id": 2}],
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_notebooks"].run({})
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_get_notebook(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    notebooks.register(mcp_server, dd_client)
    mock_api.get("/api/v1/notebooks/1").mock(return_value=httpx.Response(200, json={
        "data": {"id": 1, "attributes": {
            "name": "Incident Runbook", "author": {"handle": "admin@example.com"},
            "status": "published", "created": "2024-01-01", "modified": "2024-03-01",
            "cells": [{"id": 1, "attributes": {"definition": {"type": "markdown", "text": "# Runbook"}}}],
            "time": {"live_span": "1h"},
        }}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_notebook"].run({"notebook_id": 1})
    assert "Incident Runbook" in result["summary"]
    assert result["data"]["cells"][0]["type"] == "markdown"


# ── Downtimes ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_downtimes(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    downtimes.register(mcp_server, dd_client)
    mock_api.get("/api/v2/downtime").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "dt-1", "attributes": {
            "display_name": "Maintenance", "status": "active",
            "monitor_identifier": {"type": "monitor_tags", "monitor_tags": ["env:staging"]},
            "message": "Planned maintenance", "schedule": {"current_downtime": {"start": "2024-03-01", "end": "2024-03-02"}},
            "canceled": None, "created": "2024-03-01", "modified": "2024-03-01",
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_downtimes"].run({})
    assert result["total_count"] == 1
    assert "active: 1" in result["summary"]


@pytest.mark.asyncio
async def test_create_downtime(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    downtimes.register(mcp_server, dd_client)
    mock_api.post("/api/v2/downtime").mock(return_value=httpx.Response(200, json={
        "data": {"id": "dt-2", "attributes": {
            "display_name": "Deploy window", "status": "scheduled",
            "monitor_identifier": {"type": "monitor_tags", "monitor_tags": ["service:api"]},
            "message": "Deploy", "schedule": {"current_downtime": {}},
            "canceled": None, "created": "2024-03-01", "modified": "2024-03-01",
        }}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["create_downtime"].run({
        "monitor_identifier_type": "monitor_tags", "monitor_identifier": "service:api",
        "scope": "env:staging", "display_name": "Deploy window",
    })
    assert "Deploy window" in result["summary"]


@pytest.mark.asyncio
async def test_cancel_downtime(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    downtimes.register(mcp_server, dd_client)
    mock_api.delete("/api/v2/downtime/dt-1").mock(return_value=httpx.Response(204))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["cancel_downtime"].run({"downtime_id": "dt-1"})
    assert "Cancelled" in result["summary"]


# ── Synthetics ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_synthetics_tests(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    synthetics.register(mcp_server, dd_client)
    mock_api.get("/api/v1/synthetics/tests").mock(return_value=httpx.Response(200, json={
        "tests": [{"public_id": "abc-123", "name": "API Health Check", "type": "api",
                   "status": "live", "tags": ["env:prod"], "locations": ["aws:us-east-1"],
                   "message": "Alert on failure", "monitor_id": 1}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_synthetics_tests"].run({})
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_trigger_synthetics_test(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    synthetics.register(mcp_server, dd_client)
    mock_api.post("/api/v1/synthetics/tests/trigger").mock(return_value=httpx.Response(200, json={
        "results": [{"result_id": "res-1", "public_id": "abc-123", "status": "triggered"}],
        "triggered_check_ids": ["abc-123"],
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["trigger_synthetics_test"].run({"public_ids": ["abc-123"]})
    assert "triggered" in result["summary"].lower() or "Triggered" in result["summary"]


# ── Services ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_services(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    services.register(mcp_server, dd_client)
    mock_api.get("/api/v2/services/definitions").mock(return_value=httpx.Response(200, json={
        "data": [{"attributes": {"schema": {
            "dd-service": "payment-api", "description": "Payment processing",
            "team": "payments", "contacts": [], "links": [], "tags": ["env:prod"],
        }}}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_services"].run({})
    assert result["total_count"] == 1


# ── Processes ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_processes(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    processes.register(mcp_server, dd_client)
    mock_api.get("/api/v2/processes").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "proc-1", "attributes": {
            "name": "nginx", "pid": 1234, "host": "web-01",
            "user": "www-data", "state": "running",
            "cpu_percent": 2.5, "memory_rss": 50000000,
            "started": "2024-01-01", "tags": ["env:prod"],
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_processes"].run({"query": "nginx"})
    assert result["total_count"] == 1


# ── Network ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_network_connections(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    network.register(mcp_server, dd_client)
    mock_api.get("/api/v2/network/connections/aggregate").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "conn-1", "attributes": {
            "source": {"service": "api"}, "destination": {"service": "db"},
            "bytes_sent": 1024, "bytes_received": 2048,
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_network_connections"].run({})
    assert result["total_count"] == 1


# ── CI/CD ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_ci_pipeline_events(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    cicd.register(mcp_server, dd_client)
    mock_api.post("/api/v2/ci/pipelines/events/search").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "ci-1", "attributes": {
            "timestamp": "2024-03-01",
            "ci": {"pipeline": {"name": "deploy", "duration": 120000},
                   "status": "success", "level": "pipeline",
                   "provider": {"name": "github"},
                   "git": {"branch": "main", "commit": {"sha": "abc123"}}},
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_ci_pipeline_events"].run({"query": "*"})
    assert result["total_count"] == 1
    assert result["data"][0]["pipeline_name"] == "deploy"


@pytest.mark.asyncio
async def test_aggregate_ci_pipelines(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    cicd.register(mcp_server, dd_client)
    mock_api.post("/api/v2/ci/pipelines/analytics/aggregate").mock(return_value=httpx.Response(200, json={
        "data": {"buckets": [{"by": {}, "computes": {"c0": 50}}]},
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["aggregate_ci_pipelines"].run({"query": "*"})
    assert result["total_count"] == 1


# ── Audit ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_audit_logs(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    audit.register(mcp_server, dd_client)
    mock_api.post("/api/v2/audit/events/search").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "audit-1", "attributes": {
            "timestamp": "2024-03-01", "type": "audit",
            "message": "User modified monitor",
            "attributes": {"usr": {"email": "admin@example.com"}, "action": "monitor.update"},
            "tags": [],
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_audit_logs"].run({"query": "*"})
    assert result["total_count"] == 1


# ── Teams & On-Call ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_teams(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    teams_oncall.register(mcp_server, dd_client)
    mock_api.get("/api/v2/team").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "t-1", "attributes": {
            "name": "Platform", "handle": "platform",
            "summary": "Platform team", "description": "Core platform",
            "user_count": 5, "link_count": 3,
        }}],
        "meta": {"pagination": {"total_count": 1}},
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_teams"].run({})
    assert result["total_count"] == 1
    assert result["data"][0]["name"] == "Platform"


# ── Workflows ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_workflows(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    workflows.register(mcp_server, dd_client)
    mock_api.get("/api/v2/workflows").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "wf-1", "attributes": {
            "name": "Auto-remediation", "description": "Restart crashed pods",
            "state": "active", "created_at": "2024-01-01", "modified_at": "2024-03-01",
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_workflows"].run({})
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_trigger_workflow(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    workflows.register(mcp_server, dd_client)
    mock_api.post("/api/v2/workflows/wf-1/instances").mock(return_value=httpx.Response(200, json={
        "data": {"id": "run-1", "type": "workflow_instance", "attributes": {"status": "running"}}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["trigger_workflow"].run({"workflow_id": "wf-1"})
    assert "wf-1" in result["summary"]


# ── Cost & Usage ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_usage_summary(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    cost_usage.register(mcp_server, dd_client)
    mock_api.get("/api/v2/usage/hourly_usage").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "u-1", "attributes": {
            "product_family": "infra_hosts",
            "timestamp": "2024-03-01T00:00:00Z",
            "org_name": "test-org",
            "measurements": [{"usage_type": "host_count", "value": 100}],
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_usage_summary"].run({})
    assert result["total_count"] == 1


# ── Client retry for 5xx ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_retries_on_500(dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    """Verify client retries on 5xx server errors."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(502, json={"error": "Bad Gateway"})
        return httpx.Response(200, json={"status": "ok"})

    mock_api.get("/api/v1/test").mock(side_effect=handler)
    result = await dd_client.get("/api/v1/test")
    assert result["status"] == "ok"
    assert call_count == 3
