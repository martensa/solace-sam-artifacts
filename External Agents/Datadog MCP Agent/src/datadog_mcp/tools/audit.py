"""Datadog Audit tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register audit tools on the MCP server."""

    @mcp.tool()
    async def search_audit_logs(
        query: str = "*",
        from_time: str = "now-24h",
        to_time: str = "now",
        limit: int = 25,
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Search Datadog organization audit logs for compliance and activity tracking.

        Args:
            query: Audit search query (e.g. "@action:modified @asset.type:monitor",
                   "@usr.email:admin@example.com", "@asset.type:dashboard").
            from_time: Start  - relative ("now-24h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            limit: Max results (default 25, max 1000).
            sort_order: "asc" or "desc" (default "desc").

        Returns:
            Audit events with user, action, asset type, and timestamp.
        """
        body: dict[str, Any] = {
            "filter": {"query": query, "from": from_time, "to": to_time},
            "sort": {"order": sort_order},
            "page": {"limit": min(limit, 1000)},
        }

        try:
            data = await dd.post("/api/v2/audit/events/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search audit logs: {e}")

        events = data.get("data", [])
        formatted = []
        for ev in events:
            attrs = ev.get("attributes", {})
            formatted.append({
                "id": ev.get("id"),
                "timestamp": attrs.get("timestamp"),
                "action": attrs.get("evt", {}).get("name"),
                "user_email": attrs.get("usr", {}).get("email"),
                "user_name": attrs.get("usr", {}).get("name"),
                "asset_type": attrs.get("asset", {}).get("type"),
                "asset_id": attrs.get("asset", {}).get("id"),
                "asset_name": attrs.get("asset", {}).get("name"),
                "org_id": attrs.get("org", {}).get("id"),
            })

        return tool_response(
            summary=f"Found {len(formatted)} audit events matching '{query}'.",
            data=formatted, total_count=len(formatted),
        )
