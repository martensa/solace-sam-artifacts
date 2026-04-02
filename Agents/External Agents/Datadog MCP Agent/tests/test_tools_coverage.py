"""Tests for previously untested tool functions - fills coverage gaps."""

from __future__ import annotations

import pytest
import httpx
import respx

from mcp.server.fastmcp import FastMCP
from datadog_mcp.client import DatadogClient
from datadog_mcp.tools import (
    containers, dashboards, hosts, incidents, monitors, rum, security,
    services, slos, synthetics, teams_oncall, cost_usage,
    error_tracking, network,
)


@pytest.fixture
def mcp_server() -> FastMCP:
    return FastMCP(name="test")


# ── Containers: list_container_images ────────────────────────────────

@pytest.mark.asyncio
async def test_list_container_images(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    containers.register(mcp_server, dd_client)
    mock_api.get("/api/v2/container_images").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "ci-1", "attributes": {
            "name": "nginx", "tags": ["latest"], "image_id": "sha256:abc",
            "repo_digest": "sha256:def", "short_image": "nginx",
            "os_name": "linux", "os_version": "alpine3.18", "vulnerability_count": 2,
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_container_images"].run({})
    assert result["total_count"] == 1
    assert result["data"][0]["name"] == "nginx"


# ── Hosts: get_host_tags, unmute_host ────────────────────────────────

@pytest.mark.asyncio
async def test_get_host_tags(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    hosts.register(mcp_server, dd_client)
    mock_api.get("/api/v1/tags/hosts/web-01").mock(return_value=httpx.Response(200, json={
        "tags": {"datadog": ["env:prod", "role:web"], "aws": ["region:us-east-1"]}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_host_tags"].run({"host_name": "web-01"})
    assert "3 tags" in result["summary"]
    assert "2 sources" in result["summary"]


@pytest.mark.asyncio
async def test_unmute_host(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    hosts.register(mcp_server, dd_client)
    mock_api.post("/api/v1/host/unmute").mock(return_value=httpx.Response(200, json={
        "hostname": "web-01", "action": "Unmuted"
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["unmute_host"].run({"host_name": "web-01"})
    assert "Unmuted" in result["summary"]


# ── Incidents: list_incidents, get_incident, update_incident ─────────

@pytest.mark.asyncio
async def test_list_incidents(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    incidents.register(mcp_server, dd_client)
    mock_api.get("/api/v2/incidents").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "inc-1", "attributes": {
            "title": "DB outage", "state": "active", "severity": "SEV-1",
            "created": "2024-03-01", "modified": "2024-03-01",
            "commander": {"data": {"attributes": {"name": "Alice"}}},
        }}],
        "meta": {"pagination": {"total": 1}},
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_incidents"].run({})
    assert result["total_count"] == 1
    assert result["data"][0]["severity"] == "SEV-1"


@pytest.mark.asyncio
async def test_get_incident(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    incidents.register(mcp_server, dd_client)
    mock_api.get("/api/v2/incidents/inc-1").mock(return_value=httpx.Response(200, json={
        "data": {"id": "inc-1", "attributes": {
            "title": "DB outage", "state": "active", "severity": "SEV-1",
            "created": "2024-03-01", "modified": "2024-03-01",
            "commander": {"data": {"attributes": {"name": "Alice"}}},
            "timeline": {}, "notification_handles": [], "fields": {},
        }}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_incident"].run({"incident_id": "inc-1"})
    assert "DB outage" in result["summary"]
    assert result["data"]["severity"] == "SEV-1"


@pytest.mark.asyncio
async def test_update_incident(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    incidents.register(mcp_server, dd_client)
    mock_api.patch("/api/v2/incidents/inc-1").mock(return_value=httpx.Response(200, json={
        "data": {"id": "inc-1", "attributes": {
            "title": "DB outage", "state": "resolved", "severity": "SEV-1",
            "created": "2024-03-01", "modified": "2024-03-02",
            "commander": {"data": {"attributes": {"name": "Alice"}}},
        }}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["update_incident"].run({"incident_id": "inc-1", "status": "resolved"})
    assert "Updated" in result["summary"]


@pytest.mark.asyncio
async def test_update_incident_no_fields(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    incidents.register(mcp_server, dd_client)
    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["update_incident"].run({"incident_id": "inc-1"})
    assert result["error"] is True


# ── Monitors: update_monitor, unmute_monitor ─────────────────────────

@pytest.mark.asyncio
async def test_update_monitor(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    mock_api.put("/api/v1/monitor/123").mock(return_value=httpx.Response(200, json={
        "id": 123, "name": "Updated Monitor", "type": "metric alert",
        "query": "avg:cpu{*} > 80", "overall_state": "OK", "message": "",
        "tags": [], "created": "2024-01-01", "modified": "2024-03-01", "priority": 2,
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["update_monitor"].run({"monitor_id": 123, "name": "Updated Monitor"})
    assert "Updated monitor 123" in result["summary"]


@pytest.mark.asyncio
async def test_update_monitor_no_fields(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["update_monitor"].run({"monitor_id": 123})
    assert result["error"] is True


@pytest.mark.asyncio
async def test_unmute_monitor(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    mock_api.post("/api/v1/monitor/123/unmute").mock(return_value=httpx.Response(200, json={
        "id": 123, "name": "Test Monitor", "type": "metric alert",
        "query": "avg:cpu{*} > 90", "overall_state": "OK", "message": "",
        "tags": [], "created": "2024-01-01", "modified": "2024-03-01", "priority": 3,
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["unmute_monitor"].run({"monitor_id": 123})
    assert "Unmuted monitor 123" in result["summary"]


# ── RUM: aggregate_rum_events ────────────────────────────────────────

@pytest.mark.asyncio
async def test_aggregate_rum_events(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    rum.register(mcp_server, dd_client)
    mock_api.post("/api/v2/rum/analytics/aggregate").mock(return_value=httpx.Response(200, json={
        "data": {"buckets": [{"by": {"@application.name": "my-app"}, "computes": {"c0": 500}}]},
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["aggregate_rum_events"].run({"query": "*", "group_by": ["@application.name"]})
    assert result["total_count"] == 1


# ── Security: get_security_signal ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_security_signal(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    security.register(mcp_server, dd_client)
    mock_api.get("/api/v2/security_monitoring/signals/sig-1").mock(return_value=httpx.Response(200, json={
        "data": {"id": "sig-1", "attributes": {
            "title": "SSH brute force", "status": "high", "severity": "critical",
            "timestamp": "2024-03-01", "source": "cloudtrail", "tags": ["env:prod"],
            "message": "Detected brute force on SSH",
            "workflow": {"rule": {"name": "ssh_brute_force"}},
        }}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_security_signal"].run({"signal_id": "sig-1"})
    assert "SSH brute force" in result["summary"]
    assert result["data"]["message"] == "Detected brute force on SSH"


# ── Services: get_service_definition, get_service_dependencies ───────

@pytest.mark.asyncio
async def test_get_service_definition(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    services.register(mcp_server, dd_client)
    mock_api.get("/api/v2/services/definitions/payment-api").mock(return_value=httpx.Response(200, json={
        "data": {"attributes": {"schema": {
            "dd-service": "payment-api", "description": "Payment processing",
            "team": "payments", "contacts": [{"type": "slack", "contact": "#payments"}],
            "links": [{"name": "Runbook", "url": "https://wiki/runbook"}], "tags": ["env:prod"],
        }}}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_service_definition"].run({"service_name": "payment-api"})
    assert result["data"]["team"] == "payments"
    assert len(result["data"]["contacts"]) == 1


@pytest.mark.asyncio
async def test_get_service_dependencies(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    services.register(mcp_server, dd_client)
    mock_api.get("/api/v2/catalog/relation").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "rel-1", "attributes": {
            "type": "depends_on", "source": {"name": "api"}, "target": {"name": "db"},
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_service_dependencies"].run({})
    assert result["total_count"] == 1
    assert result["data"][0]["source"] == "api"


# ── SLOs: list_slos, get_slo, get_slo_history ────────────────────────

@pytest.mark.asyncio
async def test_list_slos(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    slos.register(mcp_server, dd_client)
    mock_api.get("/api/v1/slo").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "slo-1", "name": "API Availability", "type": "metric",
                  "description": "99.9%", "tags": [], "thresholds": [{"target": 99.9}]}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_slos"].run({})
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_get_slo(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    slos.register(mcp_server, dd_client)
    mock_api.get("/api/v1/slo/slo-1").mock(return_value=httpx.Response(200, json={
        "data": {"id": "slo-1", "name": "API Availability", "type": "metric",
                 "description": "99.9%", "tags": [], "thresholds": [{"target": 99.9}],
                 "monitor_ids": [1, 2], "groups": [], "query": {"numerator": "sum:good", "denominator": "sum:total"}}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_slo"].run({"slo_id": "slo-1"})
    assert "API Availability" in result["summary"]
    assert result["data"]["monitor_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_get_slo_history(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    slos.register(mcp_server, dd_client)
    mock_api.get("/api/v1/slo/slo-1/history").mock(return_value=httpx.Response(200, json={
        "data": {"overall": {"sli_value": 99.95, "error_budget_remaining": 45.2},
                 "thresholds": {}, "series": {}}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_slo_history"].run({"slo_id": "slo-1"})
    assert "99.95" in result["summary"]
    assert "45.20" in result["summary"]


# ── SLOs: create_slo with empty data list (IndexError regression) ────

@pytest.mark.asyncio
async def test_create_slo_empty_data(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    slos.register(mcp_server, dd_client)
    mock_api.post("/api/v1/slo").mock(return_value=httpx.Response(200, json={
        "data": []
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    # Should not raise IndexError even with empty data list
    result = await tools["create_slo"].run({
        "name": "Test SLO", "slo_type": "metric",
        "numerator": "sum:good{*}", "denominator": "sum:total{*}",
    })
    assert "Test SLO" in result["summary"]


# ── Synthetics: get_synthetics_test, get_synthetics_results ──────────

@pytest.mark.asyncio
async def test_get_synthetics_test(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    synthetics.register(mcp_server, dd_client)
    mock_api.get("/api/v1/synthetics/tests/abc-123").mock(return_value=httpx.Response(200, json={
        "public_id": "abc-123", "name": "API Health", "type": "api",
        "status": "live", "tags": [], "locations": ["aws:us-east-1"],
        "message": "Alert", "monitor_id": 1,
        "config": {"request": {"url": "https://api.example.com"}},
        "options": {"tick_every": 60},
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_synthetics_test"].run({"public_id": "abc-123"})
    assert "API Health" in result["summary"]
    assert result["data"]["config"]["request"]["url"] == "https://api.example.com"


@pytest.mark.asyncio
async def test_get_synthetics_results(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    synthetics.register(mcp_server, dd_client)
    mock_api.get("/api/v1/synthetics/tests/abc-123/results").mock(return_value=httpx.Response(200, json={
        "results": [
            {"result_id": "r-1", "status": 0, "check_time": 1700000000, "dc_id": "aws:us-east-1",
             "result": {"timings": {"total": 150}}},
            {"result_id": "r-2", "status": 1, "check_time": 1700000060, "dc_id": "aws:eu-west-1",
             "result": {"timings": {"total": 5000}}},
        ]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_synthetics_results"].run({"public_id": "abc-123"})
    assert "1 passed" in result["summary"]
    assert "1 failed" in result["summary"]
    assert result["total_count"] == 2


# ── Teams & On-Call: list_oncall_schedules, get_current_oncall ───────

@pytest.mark.asyncio
async def test_list_oncall_schedules(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    teams_oncall.register(mcp_server, dd_client)
    mock_api.get("/api/v2/on-call/schedules").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "sched-1", "attributes": {
            "name": "Primary On-Call", "timezone": "US/Eastern",
            "layers": [{"name": "Layer 1"}],
            "created_at": "2024-01-01", "modified_at": "2024-03-01",
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_oncall_schedules"].run({})
    assert result["total_count"] == 1
    assert "Primary On-Call" in result["summary"]


@pytest.mark.asyncio
async def test_get_current_oncall(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    teams_oncall.register(mcp_server, dd_client)
    mock_api.get("/api/v2/on-call/schedules/sched-1").mock(return_value=httpx.Response(200, json={
        "data": {"id": "sched-1", "attributes": {
            "name": "Primary On-Call", "timezone": "US/Eastern", "layers": [],
        }},
        "included": [
            {"type": "users", "id": "u-1", "attributes": {"name": "Alice", "email": "alice@example.com"}},
        ]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_current_oncall"].run({"schedule_id": "sched-1"})
    assert "1 user(s)" in result["summary"]
    assert result["data"]["current_oncall_users"][0]["name"] == "Alice"


# ── Cost & Usage: get_estimated_cost, get_hourly_usage ───────────────

@pytest.mark.asyncio
async def test_get_estimated_cost(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    cost_usage.register(mcp_server, dd_client)
    mock_api.get("/api/v2/usage/estimated_cost").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "c-1", "attributes": {
            "org_name": "test-org", "date": "2024-03-01", "total_cost": 5000.0,
            "charges": [{"product_name": "infra_hosts", "charge_type": "committed", "cost": 3000.0}],
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_estimated_cost"].run({})
    assert result["total_count"] == 1
    assert result["data"][0]["total_cost"] == 5000.0


@pytest.mark.asyncio
async def test_get_hourly_usage(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    cost_usage.register(mcp_server, dd_client)
    mock_api.get("/api/v2/usage/hourly_usage").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "u-1", "attributes": {
            "product_family": "infra_hosts", "timestamp": "2024-03-01T00:00:00Z",
            "measurements": [{"usage_type": "host_count", "value": 42}],
        }}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_hourly_usage"].run({
        "product_family": "infra_hosts", "from_time": "2024-03-01T00:00:00Z",
    })
    assert result["total_count"] == 1
    assert "infra_hosts" in result["summary"]


# ── Error Tracking: update_error_tracking_issue ──────────────────────

@pytest.mark.asyncio
async def test_update_error_tracking_issue(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    error_tracking.register(mcp_server, dd_client)
    mock_api.patch("/api/v2/error-tracking/issues/et-1/state").mock(return_value=httpx.Response(200, json={
        "data": {"id": "et-1", "attributes": {"status": "resolved"}}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["update_error_tracking_issue"].run({"issue_id": "et-1", "status": "resolved"})
    assert "resolved" in result["summary"]


# ── Dashboards: get_graph_snapshot ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_graph_snapshot(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    dashboards.register(mcp_server, dd_client)
    mock_api.get("/api/v1/graph/snapshot").mock(return_value=httpx.Response(200, json={
        "snapshot_url": "https://p.datadoghq.com/snapshot/abc123.png",
        "graph_url": "https://app.datadoghq.com/graph/abc123",
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_graph_snapshot"].run({"metric_query": "avg:system.cpu.user{env:prod}"})
    assert "snapshot" in result["summary"].lower() or "snapshot_url" in str(result["data"])
    assert result["data"]["snapshot_url"] == "https://p.datadoghq.com/snapshot/abc123.png"


# ── Network: get_network_dns ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_network_dns(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    network.register(mcp_server, dd_client)
    mock_api.get("/api/v2/network/dns/aggregate").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "dns-1", "attributes": {"domain": "api.example.com", "dns_lookups": 1000}}]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_network_dns"].run({})
    assert result["total_count"] == 1
