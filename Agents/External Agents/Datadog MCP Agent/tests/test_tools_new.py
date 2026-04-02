"""Tests for new v2.0 tool modules (APM, RUM, security, incidents, containers, etc.)."""

from __future__ import annotations

import pytest
import httpx
import respx

from mcp.server.fastmcp import FastMCP
from datadog_mcp.client import DatadogClient
from datadog_mcp.tools import apm, rum, security, incidents, containers, composite, slos, error_tracking


@pytest.fixture
def mcp_server() -> FastMCP:
    return FastMCP(name="test")


# ── APM ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_spans(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    apm.register(mcp_server, dd_client)
    mock_api.post("/api/v2/spans/events/search").mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "span-1",
            "attributes": {
                "service": "api", "host": "web-01", "timestamp": "2024-03-01T12:00:00Z",
                "attributes": {"trace_id": "abc123", "span_id": "def456",
                               "resource_name": "GET /users", "operation_name": "http.request",
                               "duration": 15000000, "status": "ok"},
            },
        }]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_spans"].run({"query": "service:api"})
    assert result["total_count"] == 1
    assert result["data"][0]["service"] == "api"


@pytest.mark.asyncio
async def test_aggregate_spans(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    apm.register(mcp_server, dd_client)
    mock_api.post("/api/v2/spans/analytics/aggregate").mock(return_value=httpx.Response(200, json={
        "data": {"buckets": [{"by": {"service": "api"}, "computes": {"c0": 100}}]},
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["aggregate_spans"].run({"query": "*", "group_by": ["service"]})
    assert result["total_count"] == 1


# ── RUM ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_rum_applications(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    rum.register(mcp_server, dd_client)
    mock_api.get("/api/v2/rum/applications").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "app-1", "attributes": {"name": "My Web App", "type": "browser", "client_token": "tok123"}}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_rum_applications"].run({})
    assert "My Web App" in result["summary"]


@pytest.mark.asyncio
async def test_search_rum_events(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    rum.register(mcp_server, dd_client)
    mock_api.post("/api/v2/rum/events/search").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "ev-1", "attributes": {"type": "error", "timestamp": "2024-03-01T12:00:00Z",
                  "application": {"name": "app"}, "view": {"url": "/checkout"},
                  "error": {"message": "timeout"}, "action": {}, "session": {}, "tags": []}}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_rum_events"].run({"query": "@type:error"})
    assert result["total_count"] == 1


# ── Security ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_security_signals(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    security.register(mcp_server, dd_client)
    mock_api.post("/api/v2/security_monitoring/signals/search").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "sig-1", "attributes": {"title": "Brute force attempt", "status": "high",
                  "severity": "high", "timestamp": "2024-03-01", "source": "cloudtrail",
                  "tags": ["env:prod"], "attributes": {"workflow": {"rule": {"name": "brute_force"}}}}}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_security_signals"].run({"query": "*"})
    assert result["total_count"] == 1
    assert "high: 1" in result["summary"]


@pytest.mark.asyncio
async def test_triage_security_signal(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    security.register(mcp_server, dd_client)
    mock_api.patch("/api/v2/security_monitoring/signals/sig-1/state").mock(
        return_value=httpx.Response(200, json={"data": {"attributes": {"state": "archived"}}})
    )

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["triage_security_signal"].run({"signal_id": "sig-1", "state": "archived", "archive_reason": "false_positive"})
    assert "archived" in result["summary"]


# ── Incidents ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_incident(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    incidents.register(mcp_server, dd_client)
    mock_api.post("/api/v2/incidents").mock(return_value=httpx.Response(201, json={
        "data": {"id": "inc-1", "type": "incidents", "attributes": {
            "title": "Database outage", "state": "active", "severity": "SEV-1",
            "created": "2024-03-01", "modified": "2024-03-01",
        }}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["create_incident"].run({"title": "Database outage", "severity": "SEV-1"})
    assert "Database outage" in result["summary"]
    assert "SEV-1" in result["summary"]


# ── Containers ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_containers(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    containers.register(mcp_server, dd_client)
    mock_api.get("/api/v2/containers").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "c-1", "attributes": {"name": "nginx-1", "image_name": "nginx",
                  "image_tag": "latest", "state": "running", "host": "web-01",
                  "started": "2024-03-01", "tags": ["env:prod"]}}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_containers"].run({})
    assert "1 containers" in result["summary"]


# ── Composite ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_service_health(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    composite.register(mcp_server, dd_client)

    # Mock all 5 metric queries
    mock_api.get("/api/v1/query").mock(return_value=httpx.Response(200, json={
        "series": [{"metric": "trace.http.request.duration", "pointlist": [[1700000000, 15000000]]}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_service_health"].run({"service": "web-api", "env": "prod"})
    assert "web-api" in result["summary"]
    assert "latency" in result["data"]
    assert "traffic" in result["data"]
    assert "errors" in result["data"]


# ── SLOs ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_slo(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    slos.register(mcp_server, dd_client)
    mock_api.post("/api/v1/slo").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "slo-1", "name": "API Availability", "type": "metric",
                  "description": "99.9% uptime", "tags": ["env:prod"],
                  "thresholds": [{"target": 99.9, "timeframe": "30d"}]}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["create_slo"].run({
        "name": "API Availability", "slo_type": "metric",
        "numerator": "sum:good_events{service:api}", "denominator": "sum:total_events{service:api}",
    })
    assert "API Availability" in result["summary"]
    assert "99.9%" in result["summary"]


# ── Error Tracking ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_error_tracking_issues(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    error_tracking.register(mcp_server, dd_client)
    mock_api.post("/api/v2/error-tracking/issues/search").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "et-1", "attributes": {"title": "NullPointerException", "status": "open",
                  "level": "error", "first_seen": "2024-01-01", "last_seen": "2024-03-01",
                  "count": 42, "service": "api", "env": "prod", "platform": "java"}}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_error_tracking_issues"].run({"query": "*"})
    assert result["total_count"] == 1
    assert "open: 1" in result["summary"]
