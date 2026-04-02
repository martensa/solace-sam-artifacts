"""Composite SRE tools  - ANALYZE stage.

These tools combine multiple Datadog API calls to provide high-level
operational insights aligned with SRE best practices.
"""

from __future__ import annotations

import time
from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register composite SRE tools on the MCP server."""

    @mcp.tool()
    async def get_service_health(
        service: str,
        env: str = "prod",
        from_seconds_ago: int = 900,
    ) -> dict[str, Any]:
        """Get the Four Golden Signals for a service (Latency, Traffic, Errors, Saturation).

        This is a composite tool that queries multiple trace metrics to build a
        complete health picture aligned with Google SRE best practices.

        Args:
            service: Service name as it appears in Datadog APM (e.g. "web-api", "payment-service").
            env: Environment tag (default "prod").
            from_seconds_ago: Time window in seconds (default: 900 = 15 minutes).

        Returns:
            Four Golden Signals:
            - Latency: p50, p95, p99 response times
            - Traffic: requests per second
            - Errors: error rate percentage
            - Saturation: (derived from request volume vs baseline)
        """
        now = int(time.time())
        from_ts = str(now - from_seconds_ago)
        to_ts = str(now)
        scope = f"{{service:{service},env:{env}}}"

        queries = {
            "latency_p50": f"p50:trace.http.request.duration{scope}",
            "latency_p95": f"p95:trace.http.request.duration{scope}",
            "latency_p99": f"p99:trace.http.request.duration{scope}",
            "traffic_hits": f"sum:trace.http.request.hits{scope}.as_count()",
            "traffic_errors": f"sum:trace.http.request.errors{scope}.as_count()",
        }

        results: dict[str, Any] = {}
        errors: list[str] = []

        for key, query in queries.items():
            try:
                data = await dd.get(
                    "/api/v1/query",
                    query=query,
                    **{"from": from_ts, "to": to_ts},
                )
                series = data.get("series", [])
                if series:
                    points = series[0].get("pointlist", [])
                    valid = [p[1] for p in points if p[1] is not None]
                    results[key] = sum(valid) / max(len(valid), 1) if valid else None
                else:
                    results[key] = None
            except Exception as e:
                errors.append(f"{key}: {e}")
                results[key] = None

        # Calculate derived metrics
        total_hits = results.get("traffic_hits") or 0
        total_errors = results.get("traffic_errors") or 0
        error_rate = (total_errors / total_hits * 100) if total_hits > 0 else 0.0
        rps = total_hits / from_seconds_ago if total_hits > 0 else 0.0

        health = {
            "service": service,
            "env": env,
            "time_window_seconds": from_seconds_ago,
            "latency": {
                "p50_ms": round(results["latency_p50"] / 1e6, 2) if results.get("latency_p50") else None,
                "p95_ms": round(results["latency_p95"] / 1e6, 2) if results.get("latency_p95") else None,
                "p99_ms": round(results["latency_p99"] / 1e6, 2) if results.get("latency_p99") else None,
            },
            "traffic": {
                "requests_per_second": round(rps, 2),
                "total_requests": int(total_hits),
            },
            "errors": {
                "error_rate_percent": round(error_rate, 2),
                "total_errors": int(total_errors),
            },
            "saturation": {
                "note": "Saturation metrics are infrastructure-specific. Use query_metrics with system.cpu.user, system.mem.used, etc. for the hosts running this service.",
            },
        }

        if errors:
            health["query_errors"] = errors

        # Build summary
        lat_str = f"p50={health['latency']['p50_ms']}ms" if health['latency']['p50_ms'] else "no latency data"
        return tool_response(
            summary=(
                f"Service '{service}' ({env}): "
                f"Latency {lat_str}, "
                f"Traffic {health['traffic']['requests_per_second']} req/s, "
                f"Error rate {health['errors']['error_rate_percent']}%."
            ),
            data=health,
        )
