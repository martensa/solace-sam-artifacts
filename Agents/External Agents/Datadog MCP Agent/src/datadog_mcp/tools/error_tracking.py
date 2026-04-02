"""Datadog Error Tracking tools  - ANALYZE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_error_tracking_issue, count_by, counts_str,
)


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register error tracking tools on the MCP server."""

    @mcp.tool()
    async def search_error_tracking_issues(
        query: str = "*",
        from_time: str = "now-24h",
        to_time: str = "now",
        limit: int = 25,
        sort: str = "last_seen",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Search error tracking issues across services.

        Args:
            query: Search query (e.g. "service:api", "status:unresolved",
                   "level:error", "env:prod").
            from_time: Start  - relative ("now-24h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            limit: Max results (default 25).
            sort: Sort field: "last_seen", "first_seen", "count" (default "last_seen").
            sort_order: "asc" or "desc" (default "desc").

        Returns:
            Error tracking issues with title, status, count, service, and time info.
        """
        body: dict[str, Any] = {
            "filter": {"query": query, "from": from_time, "to": to_time},
            "sort": {"field": sort, "order": sort_order},
            "page": {"limit": min(limit, 100)},
        }

        try:
            data = await dd.post("/api/v2/error-tracking/issues/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search error tracking issues: {e}")

        issues = data.get("data", [])
        formatted = [format_error_tracking_issue(i) for i in issues]
        status_str = counts_str(count_by(formatted, "status"))

        return tool_response(
            summary=f"Found {len(formatted)} error tracking issues. Status: {status_str}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def update_error_tracking_issue(
        issue_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Update the status of an error tracking issue.

        Args:
            issue_id: The error tracking issue ID.
            status: New status: "open", "resolved", "ignored".

        Returns:
            Updated issue confirmation.
        """
        body = {"data": {"attributes": {"status": status}, "type": "error_tracking_issue"}}

        try:
            data = await dd.patch(f"/api/v2/error-tracking/issues/{issue_id}/state", body=body)
        except Exception as e:
            return error_response(f"Failed to update error tracking issue {issue_id}: {e}")

        return tool_response(
            summary=f"Updated error tracking issue {issue_id} to status='{status}'.",
            data=data.get("data", data),
        )
