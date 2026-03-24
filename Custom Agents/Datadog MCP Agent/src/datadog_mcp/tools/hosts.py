"""Datadog Hosts & Infrastructure tools  - OBSERVE + RESPOND stages."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, format_host
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register host tools on the MCP server."""

    @mcp.tool()
    async def list_hosts(
        filter_query: str = "",
        sort_field: str = "",
        sort_dir: str = "asc",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List hosts monitored by Datadog with optional filtering.

        Args:
            filter_query: Filter string (e.g. "env:prod", "host:web-*").
            sort_field: Sort by "name", "status", "apps", or "cpu".
            sort_dir: "asc" or "desc".
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Hosts with status, tags, and metadata.
        """
        params: dict[str, Any] = {
            "count": 1000,
            "include_muted_hosts_data": True,
            "include_hosts_metadata": True,
        }
        if filter_query: params["filter"] = filter_query
        if sort_field:
            params["sort_field"] = sort_field
            params["sort_dir"] = sort_dir

        try:
            data = await dd.get("/api/v1/hosts", **params)
        except Exception as e:
            return error_response(f"Failed to list hosts: {e}")

        host_list = data.get("host_list", [])
        formatted = [format_host(h) for h in host_list]
        page_items, total, has_more = paginate_list(formatted, page, page_size)
        up = sum(1 for h in formatted if h["status"] == "up")

        return tool_response(
            summary=f"Found {total} hosts ({up} up, {total - up} down).",
            data=page_items, total_count=total, page=page, has_more=has_more,
        )

    @mcp.tool()
    async def get_host_totals() -> dict[str, Any]:
        """Get summary counts of hosts (total up and total down).

        Returns:
            Total host counts by status.
        """
        try:
            data = await dd.get("/api/v1/hosts/totals")
        except Exception as e:
            return error_response(f"Failed to get host totals: {e}")

        return tool_response(
            summary=f"Host totals: {data.get('total_up', 0)} up, {data.get('total_active', 0)} active.",
            data=data,
        )

    @mcp.tool()
    async def get_host_tags(host_name: str) -> dict[str, Any]:
        """Get all tags for a specific host, organized by source.

        Args:
            host_name: The hostname.

        Returns:
            Tags organized by source (e.g. datadog agent, AWS, user).
        """
        try:
            data = await dd.get(f"/api/v1/tags/hosts/{host_name}")
        except Exception as e:
            return error_response(f"Failed to get tags for host '{host_name}': {e}")

        tags = data.get("tags", {})
        all_tags = [t for src_tags in tags.values() for t in src_tags] if isinstance(tags, dict) else tags
        return tool_response(
            summary=f"Host '{host_name}' has {len(all_tags)} tags across {len(tags) if isinstance(tags, dict) else 1} sources.",
            data=tags,
        )

    @mcp.tool()
    async def add_host_tags(
        host_name: str,
        tags: list[str],
        source: str = "users",
    ) -> dict[str, Any]:
        """Add tags to a host.

        Args:
            host_name: The hostname.
            tags: Tags to add (e.g. ["env:prod", "role:web", "team:platform"]).
            source: Tag source (default "users"). Other options: "datadog", "chef", "puppet".

        Returns:
            Updated host tags.
        """
        body = {"tags": tags}
        try:
            data = await dd.post(f"/api/v1/tags/hosts/{host_name}", body=body, source=source)
        except Exception as e:
            return error_response(f"Failed to add tags to host '{host_name}': {e}")

        return tool_response(
            summary=f"Added {len(tags)} tags to host '{host_name}': {', '.join(tags[:5])}.",
            data=data,
        )

    @mcp.tool()
    async def mute_host(
        host_name: str,
        message: str = "",
        end_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Mute a host to suppress all monitor notifications for it.

        Args:
            host_name: The hostname to mute.
            message: Reason for muting.
            end_timestamp: Unix timestamp when mute auto-expires.

        Returns:
            Mute confirmation.
        """
        body: dict[str, Any] = {"hostname": host_name}
        if message: body["message"] = message
        if end_timestamp is not None: body["end"] = end_timestamp

        try:
            data = await dd.post("/api/v1/host/mute", body=body)
        except Exception as e:
            return error_response(f"Failed to mute host '{host_name}': {e}")

        return tool_response(
            summary=f"Muted host '{host_name}'." + (f" Reason: {message}" if message else ""),
            data=data,
        )

    @mcp.tool()
    async def unmute_host(host_name: str) -> dict[str, Any]:
        """Unmute a host to re-enable monitor notifications.

        Args:
            host_name: The hostname to unmute.

        Returns:
            Unmute confirmation.
        """
        try:
            data = await dd.post("/api/v1/host/unmute", body={"hostname": host_name})
        except Exception as e:
            return error_response(f"Failed to unmute host '{host_name}': {e}")

        return tool_response(summary=f"Unmuted host '{host_name}'.", data=data)
