"""Datadog CI/CD Visibility tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, build_aggregate_body


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register CI/CD visibility tools on the MCP server."""

    @mcp.tool()
    async def search_ci_pipeline_events(
        query: str = "*",
        from_time: str = "now-24h",
        to_time: str = "now",
        limit: int = 25,
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Search CI/CD pipeline execution events.

        Args:
            query: Pipeline search query (e.g. "@ci.pipeline.name:deploy",
                   "@ci.status:error", "@ci.provider.name:github").
            from_time: Start  - relative ("now-24h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            limit: Max results (default 25, max 1000).
            sort_order: "asc" or "desc" (default "desc").

        Returns:
            Pipeline events with name, status, duration, and git info.
        """
        body: dict[str, Any] = {
            "filter": {"query": query, "from": from_time, "to": to_time},
            "sort": {"order": sort_order},
            "page": {"limit": min(limit, 1000)},
        }

        try:
            data = await dd.post("/api/v2/ci/pipelines/events/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search CI pipeline events: {e}")

        events = data.get("data", [])
        formatted = []
        for e in events:
            attrs = e.get("attributes", {})
            ci = attrs.get("ci", {})
            formatted.append({
                "id": e.get("id"),
                "pipeline_name": ci.get("pipeline", {}).get("name"),
                "status": ci.get("status"),
                "level": ci.get("level"),
                "provider": ci.get("provider", {}).get("name"),
                "duration_ms": ci.get("pipeline", {}).get("duration"),
                "git_branch": ci.get("git", {}).get("branch"),
                "git_commit_sha": ci.get("git", {}).get("commit", {}).get("sha"),
                "timestamp": attrs.get("timestamp"),
            })

        return tool_response(
            summary=f"Found {len(formatted)} CI pipeline events matching '{query}'.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def aggregate_ci_pipelines(
        query: str = "*",
        from_time: str = "now-7d",
        to_time: str = "now",
        group_by: list[str] | None = None,
        compute_metric: str = "count",
        compute_type: str = "total",
    ) -> dict[str, Any]:
        """Aggregate CI/CD pipeline analytics (success rates, durations, failure counts).

        Args:
            query: Pipeline search query.
            from_time: Start  - relative ("now-7d") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            group_by: Fields to group by (e.g. ["@ci.pipeline.name", "@ci.status"]).
            compute_metric: Metric: "count", "@duration", or any numeric attribute.
            compute_type: Aggregation: "total", "avg", "min", "max", "cardinality".

        Returns:
            Aggregated pipeline statistics.
        """
        body = build_aggregate_body(query, from_time, to_time, group_by, compute_metric, compute_type)

        try:
            data = await dd.post("/api/v2/ci/pipelines/analytics/aggregate", body=body)
        except Exception as e:
            return error_response(f"Failed to aggregate CI pipelines: {e}")

        buckets = data.get("data", {}).get("buckets", [])
        return tool_response(
            summary=f"CI pipeline aggregation for '{query}': {len(buckets)} buckets.",
            data={"buckets": buckets[:50]}, total_count=len(buckets),
        )
