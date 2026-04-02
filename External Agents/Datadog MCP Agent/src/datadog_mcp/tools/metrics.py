"""Datadog Metrics tools  - OBSERVE stage."""

from __future__ import annotations

import time
from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register metrics tools on the MCP server."""

    @mcp.tool()
    async def query_metrics(
        query: str,
        from_seconds_ago: int = 3600,
        to_seconds_ago: int = 0,
    ) -> dict[str, Any]:
        """Query timeseries metric data from Datadog.

        Args:
            query: Datadog metrics query (e.g. "avg:system.cpu.user{host:web-01}",
                   "sum:trace.servlet.request.hits{service:api}.as_count()").
                   Supports avg, sum, min, max, count aggregations.
                   Tag filters in curly braces: {env:prod,service:api}.
            from_seconds_ago: Start of time window as seconds ago from now (default: 3600 = 1h).
            to_seconds_ago: End of time window as seconds ago from now (default: 0 = now).

        Returns:
            Timeseries data with pointlists per matching series.
        """
        now = int(time.time())
        try:
            data = await dd.get(
                "/api/v1/query",
                query=query,
                **{"from": str(now - from_seconds_ago), "to": str(now - to_seconds_ago)},
            )
        except Exception as e:
            return error_response(f"Failed to query metrics: {e}")

        series = data.get("series", [])
        results = []
        for s in series:
            points = s.get("pointlist", [])
            valid = [p[1] for p in points if p[1] is not None]
            results.append({
                "metric": s.get("metric"),
                "scope": s.get("scope"),
                "display_name": s.get("display_name"),
                "unit": s["unit"][0].get("name") if s.get("unit") and len(s["unit"]) > 0 else None,
                "point_count": len(points),
                "points_sample": points[:20],
                "avg_value": sum(valid) / max(len(valid), 1),
            })

        if not results:
            return tool_response(summary=f"No data for query '{query}' in the specified time range.", data=[])

        names = ", ".join(r["metric"] or "unknown" for r in results[:5])
        return tool_response(
            summary=f"Found {len(results)} series for '{query}': {names}. Up to 20 points per series.",
            data=results,
        )

    @mcp.tool()
    async def list_metrics(
        query: str = "",
    ) -> dict[str, Any]:
        """Search for available Datadog metrics by name prefix.

        Args:
            query: Metric name prefix (e.g. "system.cpu", "trace.", "aws.ec2").

        Returns:
            List of matching metric names.
        """
        try:
            data = await dd.get("/api/v1/search", q=f"metrics:{query}")
        except Exception as e:
            return error_response(f"Failed to search metrics: {e}")

        metrics = data.get("results", {}).get("metrics", [])
        return tool_response(
            summary=f"Found {len(metrics)} metrics matching '{query}'.",
            data=metrics[:100],
            total_count=len(metrics),
        )

    @mcp.tool()
    async def get_metric_metadata(
        metric_name: str,
    ) -> dict[str, Any]:
        """Get metadata for a specific metric (type, unit, description).

        Args:
            metric_name: Full metric name (e.g. "system.cpu.user").

        Returns:
            Metric metadata including type, unit, description, integration.
        """
        try:
            data = await dd.get(f"/api/v1/metrics/{metric_name}")
        except Exception as e:
            return error_response(f"Failed to get metadata for '{metric_name}': {e}")

        return tool_response(
            summary=f"Metric '{metric_name}': type={data.get('type')}, unit={data.get('unit')}, description={data.get('description', 'N/A')}.",
            data=data,
        )

    @mcp.tool()
    async def list_metric_tags(
        metric_name: str,
    ) -> dict[str, Any]:
        """Get tag configurations for a specific metric.

        Args:
            metric_name: Full metric name (e.g. "system.cpu.user").

        Returns:
            Tags actively queried and their configurations for this metric.
        """
        try:
            data = await dd.get(f"/api/v2/metrics/{metric_name}/tags")
        except Exception as e:
            return error_response(f"Failed to get tags for metric '{metric_name}': {e}")

        tag_data = data.get("data", {})
        attrs = tag_data.get("attributes", {})
        tags = attrs.get("tags", [])
        return tool_response(
            summary=f"Metric '{metric_name}' has {len(tags)} active tag keys: {', '.join(tags[:10])}.",
            data=attrs,
        )

    @mcp.tool()
    async def submit_custom_metrics(
        series: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Submit custom metric data points to Datadog.

        Args:
            series: List of metric series to submit. Each entry:
                {
                    "metric": "custom.my_metric",
                    "type": 0,  # 0=unspecified, 1=count, 2=rate, 3=gauge
                    "points": [{"timestamp": <unix_ts>, "value": <float>}],
                    "tags": ["env:prod", "service:api"],
                    "resources": [{"name": "host-01", "type": "host"}]
                }

        Returns:
            Confirmation of submission.
        """
        body = {"series": series}
        try:
            await dd.post("/api/v2/series", body=body)
        except Exception as e:
            return error_response(f"Failed to submit metrics: {e}")

        names = ", ".join(s.get("metric", "?") for s in series[:5])
        return tool_response(
            summary=f"Submitted {len(series)} metric series: {names}.",
        )
