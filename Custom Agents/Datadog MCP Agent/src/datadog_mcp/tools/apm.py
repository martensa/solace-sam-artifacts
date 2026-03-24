"""Datadog APM & Traces tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, build_aggregate_body


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register APM/trace tools on the MCP server."""

    @mcp.tool()
    async def search_spans(
        query: str = "*",
        from_time: str = "now-1h",
        to_time: str = "now",
        limit: int = 25,
        sort: str = "timestamp",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Search distributed trace spans in Datadog APM.

        Args:
            query: Span search query (e.g. "service:api resource_name:GET_/users",
                   "@http.status_code:>=500", "env:prod @duration:>1s").
            from_time: Start  - relative ("now-1h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            limit: Max spans (default 25, max 1000).
            sort: Sort field (default "timestamp").
            sort_order: "asc" or "desc" (default "desc").

        Returns:
            Spans with service, resource, duration, status, and trace ID.
        """
        body: dict[str, Any] = {
            "filter": {"query": query, "from": from_time, "to": to_time},
            "sort": {"field": sort, "order": sort_order},
            "page": {"limit": min(limit, 1000)},
        }

        try:
            data = await dd.post("/api/v2/spans/events/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search spans: {e}")

        spans = data.get("data", [])
        formatted = []
        for s in spans:
            attrs = s.get("attributes", {})
            span_attrs = attrs.get("attributes", {})
            formatted.append({
                "id": s.get("id"),
                "trace_id": span_attrs.get("trace_id"),
                "span_id": span_attrs.get("span_id"),
                "service": attrs.get("service"),
                "resource_name": span_attrs.get("resource_name"),
                "operation_name": span_attrs.get("operation_name"),
                "duration_ns": span_attrs.get("duration"),
                "status": span_attrs.get("status"),
                "host": attrs.get("host"),
                "timestamp": attrs.get("timestamp"),
                "error": span_attrs.get("error_type") or span_attrs.get("error_message"),
            })

        return tool_response(
            summary=f"Found {len(formatted)} spans matching '{query}'.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def aggregate_spans(
        query: str = "*",
        from_time: str = "now-1h",
        to_time: str = "now",
        group_by: list[str] | None = None,
        compute_metric: str = "count",
        compute_type: str = "total",
    ) -> dict[str, Any]:
        """Aggregate trace/span analytics (latency percentiles, error rates, throughput).

        Args:
            query: Span search query (same syntax as search_spans).
            from_time: Start  - relative ("now-1h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            group_by: Fields to group by (e.g. ["service", "resource_name", "@http.status_code"]).
            compute_metric: Metric: "count", "@duration", or any span attribute.
            compute_type: Aggregation: "total", "avg", "min", "max", "pc75", "pc90", "pc95", "pc99".

        Returns:
            Aggregated span analytics with buckets.
        """
        body = build_aggregate_body(query, from_time, to_time, group_by, compute_metric, compute_type)

        try:
            data = await dd.post("/api/v2/spans/analytics/aggregate", body=body)
        except Exception as e:
            return error_response(f"Failed to aggregate spans: {e}")

        buckets = data.get("data", {}).get("buckets", [])
        return tool_response(
            summary=f"Span aggregation for '{query}': {len(buckets)} buckets. Metric: {compute_type}({compute_metric}).",
            data={"buckets": buckets[:50]}, total_count=len(buckets),
        )
