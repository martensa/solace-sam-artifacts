"""Tests for monitor tools."""

from __future__ import annotations

import pytest
import httpx
import respx

from mcp.server.fastmcp import FastMCP
from datadog_mcp.client import DatadogClient
from datadog_mcp.tools import monitors

SAMPLE_MONITOR = {
    "id": 12345, "name": "High CPU", "type": "metric alert",
    "query": "avg(last_5m):avg:system.cpu.user{env:prod} > 90",
    "overall_state": "Alert", "message": "CPU high!", "tags": ["env:prod"],
    "created": "2024-01-01T00:00:00Z", "modified": "2024-03-01T00:00:00Z",
    "priority": 2, "options": {"thresholds": {"critical": 90, "warning": 80}},
}


@pytest.fixture
def mcp_server() -> FastMCP:
    return FastMCP(name="test")


@pytest.mark.asyncio
async def test_list_monitors(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    mock_api.get("/api/v1/monitor").mock(return_value=httpx.Response(200, json=[SAMPLE_MONITOR]))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_monitors"].run({"query": "", "tags": "", "monitor_type": "", "page": 1, "page_size": 25})
    assert result["total_count"] == 1
    assert "Alert: 1" in result["summary"]


@pytest.mark.asyncio
async def test_get_monitor(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    mock_api.get("/api/v1/monitor/12345").mock(return_value=httpx.Response(200, json=SAMPLE_MONITOR))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_monitor"].run({"monitor_id": 12345})
    assert "High CPU" in result["summary"]
    assert result["data"]["thresholds"]["critical"] == 90


@pytest.mark.asyncio
async def test_create_monitor(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    mock_api.post("/api/v1/monitor").mock(return_value=httpx.Response(200, json={**SAMPLE_MONITOR, "id": 99999}))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["create_monitor"].run({
        "name": "High CPU", "monitor_type": "metric alert",
        "query": "avg(last_5m):avg:system.cpu.user{env:prod} > 90",
    })
    assert "99999" in result["summary"]


@pytest.mark.asyncio
async def test_delete_monitor(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    mock_api.delete("/api/v1/monitor/12345").mock(return_value=httpx.Response(204))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["delete_monitor"].run({"monitor_id": 12345})
    assert "Deleted" in result["summary"]


@pytest.mark.asyncio
async def test_mute_monitor(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    monitors.register(mcp_server, dd_client)
    mock_api.post("/api/v1/monitor/12345/mute").mock(return_value=httpx.Response(200, json=SAMPLE_MONITOR))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["mute_monitor"].run({"monitor_id": 12345, "scope": "host:web-01"})
    assert "Muted" in result["summary"]
    assert "web-01" in result["summary"]
