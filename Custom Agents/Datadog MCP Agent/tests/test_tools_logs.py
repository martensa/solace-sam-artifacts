"""Tests for log tools."""

from __future__ import annotations

import pytest
import httpx
import respx

from mcp.server.fastmcp import FastMCP
from datadog_mcp.client import DatadogClient
from datadog_mcp.tools import logs


@pytest.fixture
def mcp_server() -> FastMCP:
    return FastMCP(name="test")


@pytest.mark.asyncio
async def test_search_logs(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    logs.register(mcp_server, dd_client)
    mock_api.post("/api/v2/logs/events/search").mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "log-1",
            "attributes": {
                "timestamp": "2024-03-01T12:00:00Z", "status": "error",
                "service": "web-app", "message": "Connection timeout",
                "host": "web-01", "tags": ["env:prod"], "attributes": {},
            },
        }]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["search_logs"].run({"query": "service:web-app status:error"})
    assert result["total_count"] == 1
    assert result["data"][0]["service"] == "web-app"


@pytest.mark.asyncio
async def test_aggregate_logs(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    logs.register(mcp_server, dd_client)
    mock_api.post("/api/v2/logs/analytics/aggregate").mock(return_value=httpx.Response(200, json={
        "data": {"buckets": [{"by": {"service": "api"}, "computes": {"c0": 42}}]},
        "meta": {},
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["aggregate_logs"].run({"query": "*", "group_by": ["service"]})
    assert result["total_count"] == 1
    assert "1 buckets" in result["summary"]


@pytest.mark.asyncio
async def test_list_log_indexes(mcp_server: FastMCP, dd_client: DatadogClient, mock_api: respx.MockRouter) -> None:
    logs.register(mcp_server, dd_client)
    mock_api.get("/api/v1/logs/config/indexes").mock(return_value=httpx.Response(200, json={
        "indexes": [
            {"name": "main", "filter": {"query": "*"}, "num_retention_days": 15, "daily_limit": 1000000, "is_rate_limited": False},
        ]
    }))

    tools = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    result = await tools["list_log_indexes"].run({})
    assert "main" in result["summary"]
