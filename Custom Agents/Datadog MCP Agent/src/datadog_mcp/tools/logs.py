"""Datadog Logs tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, truncate, build_aggregate_body


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register log tools on the MCP server."""

    @mcp.tool()
    async def search_logs(
        query: str = "*",
        from_time: str = "now-1h",
        to_time: str = "now",
        limit: int = 25,
        sort: str = "timestamp",
        sort_order: str = "desc",
        indexes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search Datadog logs using log query syntax.

        Args:
            query: Log search query (e.g. "service:api status:error",
                   "@http.status_code:>=500", "source:nginx @duration:>1000").
            from_time: Start  - relative ("now-1h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            limit: Max logs (default 25, max 1000).
            sort: Sort field (default "timestamp").
            sort_order: "asc" or "desc" (default "desc").
            indexes: Log indexes to search (omit for all).

        Returns:
            Log entries with message, status, service, and attributes.
        """
        body: dict[str, Any] = {
            "filter": {
                "query": query, "from": from_time, "to": to_time,
                "indexes": indexes or ["*"],
            },
            "sort": {"field": sort, "order": sort_order},
            "page": {"limit": min(limit, 1000)},
        }

        try:
            data = await dd.post("/api/v2/logs/events/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search logs: {e}")

        logs_data = data.get("data", [])
        formatted = []
        for log in logs_data:
            attrs = log.get("attributes", {})
            formatted.append({
                "id": log.get("id"),
                "timestamp": attrs.get("timestamp"),
                "status": attrs.get("status"),
                "service": attrs.get("service"),
                "message": truncate(attrs.get("message"), 500),
                "host": attrs.get("host"),
                "tags": attrs.get("tags", []),
            })

        return tool_response(
            summary=f"Found {len(formatted)} log entries matching '{query}'.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def aggregate_logs(
        query: str = "*",
        from_time: str = "now-1h",
        to_time: str = "now",
        group_by: list[str] | None = None,
        compute_metric: str = "count",
        compute_type: str = "total",
    ) -> dict[str, Any]:
        """Aggregate and analyze logs using analytics (facets, counts, percentiles).

        Args:
            query: Log search query (same syntax as search_logs).
            from_time: Start  - relative ("now-1h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            group_by: Fields to group by (e.g. ["service", "status"]). Max 4 dimensions.
            compute_metric: Metric to compute: "count", "@duration", or any numeric attribute.
            compute_type: Aggregation type: "total", "avg", "min", "max", "cardinality", "pc75", "pc90", "pc95", "pc99".

        Returns:
            Aggregated results with buckets and values.
        """
        body = build_aggregate_body(query, from_time, to_time, group_by, compute_metric, compute_type)

        try:
            data = await dd.post("/api/v2/logs/analytics/aggregate", body=body)
        except Exception as e:
            return error_response(f"Failed to aggregate logs: {e}")

        buckets = data.get("data", {}).get("buckets", [])
        return tool_response(
            summary=f"Log aggregation for '{query}': {len(buckets)} buckets. Grouped by: {', '.join(group_by or ['none'])}.",
            data={"buckets": buckets[:50], "meta": data.get("meta", {})},
            total_count=len(buckets),
        )

    @mcp.tool()
    async def list_log_indexes() -> dict[str, Any]:
        """List all configured Datadog log indexes with retention and filters.

        Returns:
            Log indexes with name, filter query, retention days, and daily limits.
        """
        try:
            data = await dd.get("/api/v1/logs/config/indexes")
        except Exception as e:
            return error_response(f"Failed to list log indexes: {e}")

        indexes = data.get("indexes", [])
        formatted = [{
            "name": idx.get("name"),
            "filter": idx.get("filter", {}).get("query"),
            "num_retention_days": idx.get("num_retention_days"),
            "daily_limit": idx.get("daily_limit"),
            "is_rate_limited": idx.get("is_rate_limited"),
        } for idx in indexes]

        names = ", ".join(i["name"] for i in formatted[:10])
        return tool_response(
            summary=f"Found {len(formatted)} log indexes: {names}.",
            data=formatted,
        )
