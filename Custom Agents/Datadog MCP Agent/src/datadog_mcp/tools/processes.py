"""Datadog Processes tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, truncate


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register process tools on the MCP server."""

    @mcp.tool()
    async def list_processes(
        search: str = "",
        tags: str = "",
        page_limit: int = 25,
        page_cursor: str = "",
    ) -> dict[str, Any]:
        """List running processes across monitored hosts.

        Args:
            search: Search string to filter processes by name or command.
            tags: Comma-separated tags to filter (e.g. "host:web-01,env:prod").
            page_limit: Results per page (default 25).
            page_cursor: Pagination cursor from previous response.

        Returns:
            Processes with name, PID, host, CPU%, memory, and command.
        """
        params: dict[str, Any] = {"page[limit]": page_limit}
        if search:
            params["search"] = search
        if tags:
            params["tags"] = tags
        if page_cursor:
            params["page[cursor]"] = page_cursor

        try:
            data = await dd.get("/api/v2/processes", **params)
        except Exception as e:
            return error_response(f"Failed to list processes: {e}")

        processes = data.get("data", [])
        formatted = []
        for p in processes:
            attrs = p.get("attributes", {})
            formatted.append({
                "id": p.get("id"),
                "name": attrs.get("name"),
                "pid": attrs.get("pid"),
                "host": attrs.get("host"),
                "command": truncate(attrs.get("command"), 200),
                "user": attrs.get("user"),
                "cpu_percent": attrs.get("cpu_percent"),
                "memory_rss": attrs.get("memory_rss"),
                "state": attrs.get("state"),
                "started": attrs.get("started"),
                "tags": attrs.get("tags", []),
            })

        return tool_response(
            summary=f"Found {len(formatted)} processes" + (f" matching '{search}'" if search else "") + ".",
            data=formatted, total_count=len(formatted),
        )
