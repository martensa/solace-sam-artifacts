"""Datadog Downtimes tools  - RESPOND stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_downtime, count_by, counts_str,
)


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register downtime tools on the MCP server."""

    @mcp.tool()
    async def list_downtimes(
        current_only: bool = True,
    ) -> dict[str, Any]:
        """List scheduled Datadog downtimes (maintenance windows).

        Args:
            current_only: If true, only active/scheduled downtimes. If false, include cancelled.

        Returns:
            Downtimes with schedule, scope, and status.
        """
        try:
            data = await dd.get("/api/v2/downtime", current_only=str(current_only).lower())
        except Exception as e:
            return error_response(f"Failed to list downtimes: {e}")

        downtimes = data.get("data", [])
        formatted = [format_downtime(d) for d in downtimes]
        status_str = counts_str(count_by(formatted, "status"))

        return tool_response(
            summary=f"Found {len(formatted)} downtimes. Status: {status_str}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def create_downtime(
        monitor_identifier_type: str,
        monitor_identifier: str | int,
        scope: str,
        message: str = "",
        start: str | None = None,
        end: str | None = None,
        display_name: str = "",
    ) -> dict[str, Any]:
        """Schedule a new downtime (maintenance window).

        Args:
            monitor_identifier_type: "monitor_id" (single monitor) or "monitor_tags" (tag-based).
            monitor_identifier: Monitor ID (int) or comma-separated tags string.
            scope: Downtime scope (e.g. "env:staging", "host:web-01").
            message: Reason for the downtime.
            start: ISO 8601 start time (default: now). E.g. "2024-03-15T10:00:00Z".
            end: ISO 8601 end time (required for one-time downtimes).
            display_name: Human-readable name.

        Returns:
            Created downtime details.
        """
        try:
            monitor_id_obj: dict[str, Any] = {"type": monitor_identifier_type}
            if monitor_identifier_type == "monitor_id":
                monitor_id_obj["monitor_id"] = int(monitor_identifier)
            else:
                monitor_id_obj["monitor_tags"] = [t.strip() for t in str(monitor_identifier).split(",")]
        except (ValueError, TypeError) as e:
            return error_response(f"Invalid monitor_identifier '{monitor_identifier}': {e}")

        schedule: dict[str, Any] = {}
        if start: schedule["start"] = start
        if end: schedule["end"] = end

        body: dict[str, Any] = {
            "data": {
                "type": "downtime",
                "attributes": {
                    "monitor_identifier": monitor_id_obj,
                    "scope": scope, "message": message,
                    **({"schedule": schedule} if schedule else {}),
                },
            }
        }
        if display_name:
            body["data"]["attributes"]["display_name"] = display_name

        try:
            data = await dd.post("/api/v2/downtime", body=body)
        except Exception as e:
            return error_response(f"Failed to create downtime: {e}")

        dt = data.get("data", data)
        return tool_response(
            summary=f"Created downtime '{display_name or 'unnamed'}' for scope '{scope}'.",
            data=format_downtime(dt),
        )

    @mcp.tool()
    async def cancel_downtime(downtime_id: str) -> dict[str, Any]:
        """Cancel a scheduled downtime.

        Args:
            downtime_id: The downtime ID to cancel.

        Returns:
            Cancellation confirmation.
        """
        try:
            await dd.delete(f"/api/v2/downtime/{downtime_id}")
        except Exception as e:
            return error_response(f"Failed to cancel downtime {downtime_id}: {e}")

        return tool_response(summary=f"Cancelled downtime {downtime_id}.")
