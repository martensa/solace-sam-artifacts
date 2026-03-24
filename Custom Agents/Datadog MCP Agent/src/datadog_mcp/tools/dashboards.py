"""Datadog Dashboards tools  - ANALYZE stage."""

from __future__ import annotations

import time
from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, format_dashboard
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register dashboard tools on the MCP server."""

    @mcp.tool()
    async def list_dashboards(
        query: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List Datadog dashboards with optional title search.

        Args:
            query: Search dashboards by title substring.
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Dashboards with title, URL, and layout type.
        """
        try:
            data = await dd.get("/api/v1/dashboard")
        except Exception as e:
            return error_response(f"Failed to list dashboards: {e}")

        dashboards = data.get("dashboards", [])
        if query:
            q = query.lower()
            dashboards = [d for d in dashboards if q in (d.get("title") or "").lower()]

        formatted = [format_dashboard(d) for d in dashboards]
        page_items, total, has_more = paginate_list(formatted, page, page_size)

        return tool_response(
            summary=f"Found {total} dashboards" + (f" matching '{query}'" if query else "") + ".",
            data=page_items, total_count=total, page=page, has_more=has_more,
        )

    @mcp.tool()
    async def get_dashboard(dashboard_id: str) -> dict[str, Any]:
        """Get detailed dashboard definition including widgets and layout.

        Args:
            dashboard_id: The dashboard ID string.

        Returns:
            Dashboard with widgets, template variables, and layout.
        """
        try:
            data = await dd.get(f"/api/v1/dashboard/{dashboard_id}")
        except Exception as e:
            return error_response(f"Failed to get dashboard {dashboard_id}: {e}")

        widgets = data.get("widgets", [])
        widget_summary = [{
            "id": w.get("id"),
            "type": w.get("definition", {}).get("type"),
            "title": w.get("definition", {}).get("title", ""),
        } for w in widgets[:30]]

        result = format_dashboard(data)
        result["template_variables"] = data.get("template_variables", [])
        result["widget_count"] = len(widgets)
        result["widgets"] = widget_summary

        return tool_response(
            summary=f"Dashboard '{result['title']}' ({result['layout_type']}): {len(widgets)} widgets.",
            data=result,
        )

    @mcp.tool()
    async def get_graph_snapshot(
        metric_query: str,
        from_seconds_ago: int = 3600,
        to_seconds_ago: int = 0,
        title: str = "",
    ) -> dict[str, Any]:
        """Capture a point-in-time graph snapshot as an image URL.

        Args:
            metric_query: Metric query to graph (e.g. "avg:system.cpu.user{env:prod}").
            from_seconds_ago: Start as seconds ago (default: 3600 = 1h).
            to_seconds_ago: End as seconds ago (default: 0 = now).
            title: Optional graph title.

        Returns:
            URL to the generated graph image snapshot.
        """
        now = int(time.time())

        params: dict[str, Any] = {
            "metric_query": metric_query,
            "start": now - from_seconds_ago,
            "end": now - to_seconds_ago,
        }
        if title:
            params["title"] = title

        try:
            data = await dd.get("/api/v1/graph/snapshot", **params)
        except Exception as e:
            return error_response(f"Failed to get graph snapshot: {e}")

        return tool_response(
            summary=f"Graph snapshot for '{metric_query}': {data.get('snapshot_url', 'generating...')}",
            data={"snapshot_url": data.get("snapshot_url"), "graph_url": data.get("graph_url")},
        )
