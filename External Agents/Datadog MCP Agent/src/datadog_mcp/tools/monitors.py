"""Datadog Monitors tools  - ALERT stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_monitor, count_by, counts_str,
)
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register monitor tools on the MCP server."""

    @mcp.tool()
    async def list_monitors(
        query: str = "",
        tags: str = "",
        monitor_type: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List and search Datadog monitors (alert rules).

        Args:
            query: Search string to filter by name or attributes.
            tags: Comma-separated tags (e.g. "env:prod,service:api").
            monitor_type: Filter by type: "metric", "log", "apm", "synthetics", "process".
            page: Page number (default 1).
            page_size: Results per page (default 25, max 100).

        Returns:
            Monitors with status, name, type, and configuration.
        """
        params: dict[str, Any] = {}
        if tags:
            params["tags"] = tags
        query_parts: list[str] = []
        if monitor_type:
            query_parts.append(f"type:{monitor_type}")
        if query:
            query_parts.append(query)
        if query_parts:
            params["query"] = " ".join(query_parts)

        try:
            data = await dd.get("/api/v1/monitor", **params)
        except Exception as e:
            return error_response(f"Failed to list monitors: {e}")

        monitors = data if isinstance(data, list) else data.get("monitors", [])
        formatted = [format_monitor(m) for m in monitors]
        page_items, total, has_more = paginate_list(formatted, page, page_size)
        status_str = counts_str(count_by(formatted, "status"))

        return tool_response(
            summary=f"Found {total} monitors. Status: {status_str}.",
            data=page_items,
            total_count=total,
            page=page,
            has_more=has_more,
        )

    @mcp.tool()
    async def get_monitor(monitor_id: int) -> dict[str, Any]:
        """Get detailed status and configuration of a specific monitor.

        Args:
            monitor_id: The numeric monitor ID.

        Returns:
            Full monitor config including query, thresholds, notification settings, status.
        """
        try:
            data = await dd.get(f"/api/v1/monitor/{monitor_id}")
        except Exception as e:
            return error_response(f"Failed to get monitor {monitor_id}: {e}")

        m = format_monitor(data)
        m["options"] = data.get("options", {})
        m["thresholds"] = data.get("options", {}).get("thresholds", {})
        return tool_response(
            summary=f"Monitor '{m['name']}' (ID: {m['id']}): status={m['status']}, type={m['type']}.",
            data=m,
        )

    @mcp.tool()
    async def create_monitor(
        name: str,
        monitor_type: str,
        query: str,
        message: str = "",
        tags: list[str] | None = None,
        priority: int | None = None,
        thresholds: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Create a new Datadog monitor (alert rule).

        Args:
            name: Human-readable name.
            monitor_type: "metric alert", "log alert", "query alert", "service check",
                          "event alert", "process alert", "composite".
            query: Alert condition query (e.g. "avg(last_5m):avg:system.cpu.user{env:prod} > 90").
            message: Notification message. Supports @-mentions (e.g. "@slack-ops @pagerduty").
            tags: Tags to attach (e.g. ["env:prod", "team:platform"]).
            priority: Priority 1-5 (1 = highest).
            thresholds: Alert thresholds (e.g. {"critical": 90, "warning": 80}).

        Returns:
            Created monitor details.
        """
        body: dict[str, Any] = {
            "name": name, "type": monitor_type, "query": query,
            "message": message, "tags": tags or [], "options": {},
        }
        if priority is not None:
            body["priority"] = priority
        if thresholds:
            body["options"]["thresholds"] = thresholds

        try:
            data = await dd.post("/api/v1/monitor", body=body)
        except Exception as e:
            return error_response(f"Failed to create monitor: {e}")

        return tool_response(
            summary=f"Created monitor '{name}' (ID: {data.get('id')}) of type '{monitor_type}'.",
            data=format_monitor(data),
        )

    @mcp.tool()
    async def update_monitor(
        monitor_id: int,
        name: str | None = None,
        query: str | None = None,
        message: str | None = None,
        tags: list[str] | None = None,
        priority: int | None = None,
        thresholds: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Update an existing monitor's configuration.

        Args:
            monitor_id: The numeric monitor ID.
            name: New name (optional).
            query: New query (optional).
            message: New notification message (optional).
            tags: New tags (optional).
            priority: New priority 1-5 (optional).
            thresholds: New thresholds dict (optional).

        Returns:
            Updated monitor details.
        """
        body: dict[str, Any] = {}
        if name is not None: body["name"] = name
        if query is not None: body["query"] = query
        if message is not None: body["message"] = message
        if tags is not None: body["tags"] = tags
        if priority is not None: body["priority"] = priority
        if thresholds is not None: body.setdefault("options", {})["thresholds"] = thresholds
        if not body:
            return error_response("No fields provided to update.")

        try:
            data = await dd.put(f"/api/v1/monitor/{monitor_id}", body=body)
        except Exception as e:
            return error_response(f"Failed to update monitor {monitor_id}: {e}")

        return tool_response(summary=f"Updated monitor {monitor_id}.", data=format_monitor(data))

    @mcp.tool()
    async def delete_monitor(monitor_id: int) -> dict[str, Any]:
        """Delete a Datadog monitor.

        Args:
            monitor_id: The numeric monitor ID to delete.

        Returns:
            Confirmation of deletion.
        """
        try:
            await dd.delete(f"/api/v1/monitor/{monitor_id}")
        except Exception as e:
            return error_response(f"Failed to delete monitor {monitor_id}: {e}")

        return tool_response(summary=f"Deleted monitor {monitor_id}.")

    @mcp.tool()
    async def mute_monitor(
        monitor_id: int,
        scope: str = "",
        end_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Mute a monitor to suppress its notifications.

        Args:
            monitor_id: The numeric monitor ID.
            scope: Scope to mute (e.g. "host:web-01", "env:staging"). Empty = all scopes.
            end_timestamp: Unix timestamp when mute auto-expires.

        Returns:
            Mute confirmation.
        """
        body: dict[str, Any] = {}
        if scope: body["scope"] = scope
        if end_timestamp is not None: body["end"] = end_timestamp

        try:
            data = await dd.post(f"/api/v1/monitor/{monitor_id}/mute", body=body)
        except Exception as e:
            return error_response(f"Failed to mute monitor {monitor_id}: {e}")

        return tool_response(
            summary=f"Muted monitor {monitor_id}" + (f" for scope '{scope}'" if scope else "") + ".",
            data=format_monitor(data),
        )

    @mcp.tool()
    async def unmute_monitor(
        monitor_id: int,
        scope: str = "",
    ) -> dict[str, Any]:
        """Unmute a monitor to re-enable its notifications.

        Args:
            monitor_id: The numeric monitor ID.
            scope: Scope to unmute. Empty = all scopes.

        Returns:
            Unmute confirmation.
        """
        body: dict[str, Any] = {}
        if scope: body["scope"] = scope

        try:
            data = await dd.post(f"/api/v1/monitor/{monitor_id}/unmute", body=body)
        except Exception as e:
            return error_response(f"Failed to unmute monitor {monitor_id}: {e}")

        return tool_response(
            summary=f"Unmuted monitor {monitor_id}" + (f" for scope '{scope}'" if scope else "") + ".",
            data=format_monitor(data),
        )
