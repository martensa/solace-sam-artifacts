"""Datadog SLO tools  - ALERT stage."""

from __future__ import annotations

import time
from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_slo, count_by, counts_str,
)
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register SLO tools on the MCP server."""

    @mcp.tool()
    async def list_slos(
        query: str = "",
        tags: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List Datadog Service Level Objectives.

        Args:
            query: Search query to filter SLOs by name.
            tags: Comma-separated tags (e.g. "env:prod,service:api").
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            SLOs with thresholds, status, and configuration.
        """
        params: dict[str, Any] = {"limit": 1000}
        if query: params["query"] = query
        if tags: params["tags_query"] = tags

        try:
            data = await dd.get("/api/v1/slo", **params)
        except Exception as e:
            return error_response(f"Failed to list SLOs: {e}")

        slos = data.get("data", [])
        formatted = [format_slo(s) for s in slos]
        page_items, total, has_more = paginate_list(formatted, page, page_size)
        type_str = counts_str(count_by(formatted, "type"))

        return tool_response(
            summary=f"Found {total} SLOs. Types: {type_str}.",
            data=page_items, total_count=total, page=page, has_more=has_more,
        )

    @mcp.tool()
    async def get_slo(slo_id: str) -> dict[str, Any]:
        """Get detailed configuration and current status of a specific SLO.

        Args:
            slo_id: The SLO ID string.

        Returns:
            SLO definition including thresholds, type, description, and current status.
        """
        try:
            data = await dd.get(f"/api/v1/slo/{slo_id}")
        except Exception as e:
            return error_response(f"Failed to get SLO {slo_id}: {e}")

        slo = data.get("data", data)
        formatted = format_slo(slo)
        formatted["monitor_ids"] = slo.get("monitor_ids", [])
        formatted["groups"] = slo.get("groups", [])
        formatted["query"] = slo.get("query", {})

        return tool_response(
            summary=f"SLO '{formatted['name']}' ({formatted['type']}): {len(formatted.get('thresholds', []))} thresholds configured.",
            data=formatted,
        )

    @mcp.tool()
    async def get_slo_history(
        slo_id: str,
        from_seconds_ago: int = 2592000,
        to_seconds_ago: int = 0,
    ) -> dict[str, Any]:
        """Get SLO status history, error budget remaining, and uptime data.

        Args:
            slo_id: The SLO ID string.
            from_seconds_ago: Start as seconds ago (default: 2592000 = 30 days).
            to_seconds_ago: End as seconds ago (default: 0 = now).

        Returns:
            SLO history with SLI value, error budget remaining, and uptime series.
        """
        now = int(time.time())
        try:
            data = await dd.get(
                f"/api/v1/slo/{slo_id}/history",
                from_ts=str(now - from_seconds_ago),
                to_ts=str(now - to_seconds_ago),
            )
        except Exception as e:
            return error_response(f"Failed to get SLO history for {slo_id}: {e}")

        history = data.get("data", {})
        overall = history.get("overall", {})
        sli_value = overall.get("sli_value")
        error_budget = overall.get("error_budget_remaining")

        parts = [f"SLO {slo_id}"]
        if sli_value is not None: parts.append(f"SLI: {sli_value:.4f}%")
        if error_budget is not None: parts.append(f"error budget remaining: {error_budget:.2f}%")

        return tool_response(
            summary=", ".join(parts) + ".",
            data={"slo_id": slo_id, "overall": overall, "thresholds": history.get("thresholds", {}), "series": history.get("series", {})},
        )

    @mcp.tool()
    async def create_slo(
        name: str,
        slo_type: str,
        description: str = "",
        tags: list[str] | None = None,
        target_threshold: float = 99.9,
        timeframe: str = "30d",
        monitor_ids: list[int] | None = None,
        numerator: str = "",
        denominator: str = "",
    ) -> dict[str, Any]:
        """Create a new Service Level Objective.

        Args:
            name: SLO name (e.g. "API Availability", "Payment Latency P99").
            slo_type: "metric" (metric-based) or "monitor" (monitor-based).
            description: SLO description.
            tags: Tags (e.g. ["env:prod", "team:platform"]).
            target_threshold: Target percentage (default 99.9).
            timeframe: "7d", "30d", or "90d" (default "30d").
            monitor_ids: List of monitor IDs (required for slo_type="monitor").
            numerator: Good events metric query (required for slo_type="metric").
            denominator: Total events metric query (required for slo_type="metric").

        Returns:
            Created SLO details.
        """
        body: dict[str, Any] = {
            "name": name,
            "type": slo_type,
            "description": description,
            "tags": tags or [],
            "thresholds": [{"target": target_threshold, "timeframe": timeframe}],
        }
        if slo_type == "monitor" and monitor_ids:
            body["monitor_ids"] = monitor_ids
        elif slo_type == "metric" and numerator and denominator:
            body["query"] = {"numerator": numerator, "denominator": denominator}

        try:
            data = await dd.post("/api/v1/slo", body=body)
        except Exception as e:
            return error_response(f"Failed to create SLO: {e}")

        raw = data.get("data", data)
        slo = raw[0] if isinstance(raw, list) and raw else (raw if raw else data)
        return tool_response(
            summary=f"Created SLO '{name}' ({slo_type}) with target {target_threshold}% over {timeframe}.",
            data=format_slo(slo),
        )
