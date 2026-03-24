"""Datadog Real User Monitoring (RUM) tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, truncate, build_aggregate_body


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register RUM tools on the MCP server."""

    @mcp.tool()
    async def list_rum_applications() -> dict[str, Any]:
        """List all Real User Monitoring applications configured in Datadog.

        Returns:
            RUM applications with name, client token, type, and creation date.
        """
        try:
            data = await dd.get("/api/v2/rum/applications")
        except Exception as e:
            return error_response(f"Failed to list RUM applications: {e}")

        apps = data.get("data", [])
        formatted = []
        for a in apps:
            attrs = a.get("attributes", {})
            formatted.append({
                "id": a.get("id"),
                "name": attrs.get("name"),
                "type": attrs.get("type"),
                "client_token": attrs.get("client_token"),
                "created_at": attrs.get("created_at"),
                "updated_at": attrs.get("updated_at"),
                "org_id": attrs.get("org_id"),
            })

        names = ", ".join(a["name"] or "?" for a in formatted[:10])
        return tool_response(
            summary=f"Found {len(formatted)} RUM applications: {names}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def search_rum_events(
        query: str = "*",
        from_time: str = "now-1h",
        to_time: str = "now",
        limit: int = 25,
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Search Real User Monitoring events (page views, actions, errors, resources).

        Args:
            query: RUM search query (e.g. "@type:error @application.name:my-app",
                   "@view.url_path:/checkout", "@action.type:click").
            from_time: Start  - relative ("now-1h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            limit: Max events (default 25, max 1000).
            sort_order: "asc" or "desc" (default "desc").

        Returns:
            RUM events with type, application, view, action, and timing data.
        """
        body: dict[str, Any] = {
            "filter": {"query": query, "from": from_time, "to": to_time},
            "sort": {"type": "timestamp", "order": sort_order},
            "page": {"limit": min(limit, 1000)},
        }

        try:
            data = await dd.post("/api/v2/rum/events/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search RUM events: {e}")

        events = data.get("data", [])
        formatted = []
        for ev in events:
            attrs = ev.get("attributes", {})
            formatted.append({
                "id": ev.get("id"),
                "type": attrs.get("type"),
                "timestamp": attrs.get("timestamp"),
                "application": attrs.get("application", {}).get("name"),
                "view_url": attrs.get("view", {}).get("url"),
                "action_type": attrs.get("action", {}).get("type"),
                "error_message": truncate(attrs.get("error", {}).get("message"), 300),
                "session_type": attrs.get("session", {}).get("type"),
                "tags": attrs.get("tags", []),
            })

        return tool_response(
            summary=f"Found {len(formatted)} RUM events matching '{query}'.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def aggregate_rum_events(
        query: str = "*",
        from_time: str = "now-1h",
        to_time: str = "now",
        group_by: list[str] | None = None,
        compute_metric: str = "count",
        compute_type: str = "total",
    ) -> dict[str, Any]:
        """Aggregate RUM analytics (page load times, error rates, user sessions).

        Args:
            query: RUM search query (same syntax as search_rum_events).
            from_time: Start  - relative ("now-1h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            group_by: Fields to group by (e.g. ["@application.name", "@view.url_path", "@type"]).
            compute_metric: Metric: "count", "@view.loading_time", "@action.loading_time".
            compute_type: Aggregation: "total", "avg", "min", "max", "pc75", "pc90", "pc95", "pc99".

        Returns:
            Aggregated RUM analytics with buckets.
        """
        body = build_aggregate_body(query, from_time, to_time, group_by, compute_metric, compute_type)

        try:
            data = await dd.post("/api/v2/rum/analytics/aggregate", body=body)
        except Exception as e:
            return error_response(f"Failed to aggregate RUM events: {e}")

        buckets = data.get("data", {}).get("buckets", [])
        return tool_response(
            summary=f"RUM aggregation for '{query}': {len(buckets)} buckets.",
            data={"buckets": buckets[:50]}, total_count=len(buckets),
        )
