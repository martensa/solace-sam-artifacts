"""Tests for metrics tools."""

from __future__ import annotations

import pytest
import httpx
import respx

from mcp.server.fastmcp import FastMCP
from datadog_mcp.client import DatadogClient
from datadog_mcp.tools import metrics


@pytest.fixture
def mcp_server() -> FastMCP:
    return FastMCP(name="test")


@pytest.mark.asyncio
async def test_query_metrics(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    metrics.register(mcp_server, dd_client)
    mock_api.get("/api/v1/query").mock(return_value=httpx.Response(200, json={
        "series": [{
            "metric": "system.cpu.user", "scope": "host:web-01",
            "display_name": "system.cpu.user", "unit": [{"name": "percent"}],
            "pointlist": [[1700000000, 45.2], [1700000060, 47.8]],
        }]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["query_metrics"].run({"query": "avg:system.cpu.user{*}", "from_seconds_ago": 3600, "to_seconds_ago": 0})
    assert "system.cpu.user" in result["summary"]
    assert len(result["data"]) == 1
    assert result["data"][0]["point_count"] == 2


@pytest.mark.asyncio
async def test_list_metrics(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    metrics.register(mcp_server, dd_client)
    mock_api.get("/api/v1/search").mock(return_value=httpx.Response(200, json={
        "results": {"metrics": ["system.cpu.user", "system.cpu.system", "system.cpu.idle"]}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_metrics"].run({"query": "system.cpu"})
    assert result["total_count"] == 3


@pytest.mark.asyncio
async def test_get_metric_metadata(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    metrics.register(mcp_server, dd_client)
    mock_api.get("/api/v1/metrics/system.cpu.user").mock(return_value=httpx.Response(200, json={
        "type": "gauge", "unit": "percent", "description": "CPU user time",
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["get_metric_metadata"].run({"metric_name": "system.cpu.user"})
    assert "gauge" in result["summary"]


@pytest.mark.asyncio
async def test_list_metric_tags(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    metrics.register(mcp_server, dd_client)
    mock_api.get("/api/v2/metrics/system.cpu.user/tags").mock(return_value=httpx.Response(200, json={
        "data": {"attributes": {"tags": ["host", "env", "service"]}}
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_metric_tags"].run({"metric_name": "system.cpu.user"})
    assert "3 active tag keys" in result["summary"]


@pytest.mark.asyncio
async def test_submit_custom_metrics(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    metrics.register(mcp_server, dd_client)
    mock_api.post("/api/v2/series").mock(return_value=httpx.Response(202, json={}))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["submit_custom_metrics"].run({"series": [{"metric": "custom.test", "type": 3, "points": [{"timestamp": 1700000000, "value": 42.0}]}]})
    assert "Submitted 1 metric" in result["summary"]
