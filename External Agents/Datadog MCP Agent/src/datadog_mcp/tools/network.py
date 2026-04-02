"""Datadog Network Monitoring tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register network monitoring tools on the MCP server."""

    @mcp.tool()
    async def get_network_connections(
        tags: str = "",
    ) -> dict[str, Any]:
        """Get aggregated network connection data between services and hosts.

        Args:
            tags: Comma-separated tags to filter (e.g. "env:prod,service:api").

        Returns:
            Network connection aggregates: bytes sent/received, retransmits, latency.
        """
        params: dict[str, Any] = {}
        if tags:
            params["tags"] = tags

        try:
            data = await dd.get("/api/v2/network/connections/aggregate", **params)
        except Exception as e:
            return error_response(f"Failed to get network connections: {e}")

        connections = data.get("data", [])
        return tool_response(
            summary=f"Retrieved {len(connections)} network connection aggregates.",
            data=connections[:50],
            total_count=len(connections),
        )

    @mcp.tool()
    async def get_network_dns(
        tags: str = "",
    ) -> dict[str, Any]:
        """Get aggregated DNS resolution analytics.

        Args:
            tags: Comma-separated tags to filter (e.g. "env:prod").

        Returns:
            DNS analytics: resolution times, failure rates, top queried domains.
        """
        params: dict[str, Any] = {}
        if tags:
            params["tags"] = tags

        try:
            data = await dd.get("/api/v2/network/dns/aggregate", **params)
        except Exception as e:
            return error_response(f"Failed to get DNS analytics: {e}")

        dns_data = data.get("data", [])
        return tool_response(
            summary=f"Retrieved {len(dns_data)} DNS analytics aggregates.",
            data=dns_data[:50],
            total_count=len(dns_data),
        )
