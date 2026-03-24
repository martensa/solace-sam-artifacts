"""Datadog Events tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, format_event, format_event_v2


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register event tools on the MCP server."""

    @mcp.tool()
    async def search_events(
        query: str = "*",
        from_time: str = "now-24h",
        to_time: str = "now",
        limit: int = 25,
        sort: str = "timestamp",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Search the Datadog event stream using query syntax.

        Args:
            query: Event search query (e.g. "source:deploy", "priority:normal",
                   "tags:env:prod", "status:error").
            from_time: Start time  - relative ("now-24h", "now-1h") or ISO 8601.
            to_time: End time  - relative ("now") or ISO 8601.
            limit: Max events to return (default 25, max 1000).
            sort: Sort field (default "timestamp").
            sort_order: "asc" or "desc" (default "desc").

        Returns:
            Events with title, source, priority, and tags.
        """
        body: dict[str, Any] = {
            "filter": {"query": query, "from": from_time, "to": to_time},
            "sort": {"field": sort, "order": sort_order},
            "page": {"limit": min(limit, 1000)},
        }

        try:
            data = await dd.post("/api/v2/events/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search events: {e}")

        events = data.get("data", [])
        formatted = [format_event_v2(e) for e in events]

        return tool_response(
            summary=f"Found {len(formatted)} events matching '{query}'.",
            data=formatted,
            total_count=len(formatted),
        )

    @mcp.tool()
    async def create_event(
        title: str,
        text: str = "",
        alert_type: str = "info",
        priority: str = "normal",
        tags: list[str] | None = None,
        source_type_name: str = "custom",
    ) -> dict[str, Any]:
        """Post a custom event to the Datadog event stream.

        Args:
            title: Event title (e.g. "Deployment completed", "Maintenance started").
            text: Event body  - supports Markdown.
            alert_type: "info", "warning", "error", or "success".
            priority: "normal" or "low".
            tags: Tags (e.g. ["env:prod", "deploy:v2.1"]).
            source_type_name: Source type (default "custom").

        Returns:
            Created event details.
        """
        body: dict[str, Any] = {
            "title": title, "text": text, "alert_type": alert_type,
            "priority": priority, "tags": tags or [],
            "source_type_name": source_type_name,
        }

        try:
            data = await dd.post("/api/v1/events", body=body)
        except Exception as e:
            return error_response(f"Failed to create event: {e}")

        event = data.get("event", data)
        return tool_response(
            summary=f"Created event '{title}' (ID: {event.get('id')}) with alert_type='{alert_type}'.",
            data=format_event(event),
        )
