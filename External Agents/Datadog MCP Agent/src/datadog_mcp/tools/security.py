"""Datadog Security Monitoring tools  - OBSERVE + RESPOND stages."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_security_signal, count_by, counts_str, truncate,
)


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register security monitoring tools on the MCP server."""

    @mcp.tool()
    async def search_security_signals(
        query: str = "*",
        from_time: str = "now-24h",
        to_time: str = "now",
        limit: int = 25,
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Search Datadog security monitoring signals (threats, anomalies, compliance).

        Args:
            query: Security signal search query (e.g. "status:high", "source:cloudtrail",
                   "rule.name:*brute*force*", "@severity:critical").
            from_time: Start  - relative ("now-24h") or ISO 8601.
            to_time: End  - relative ("now") or ISO 8601.
            limit: Max results (default 25, max 1000).
            sort_order: "asc" or "desc" (default "desc").

        Returns:
            Security signals with title, severity, status, source, and rule name.
        """
        body: dict[str, Any] = {
            "filter": {"query": query, "from": from_time, "to": to_time},
            "sort": {"order": sort_order},
            "page": {"limit": min(limit, 1000)},
        }

        try:
            data = await dd.post("/api/v2/security_monitoring/signals/search", body=body)
        except Exception as e:
            return error_response(f"Failed to search security signals: {e}")

        signals = data.get("data", [])
        formatted = [format_security_signal(s) for s in signals]
        sev_str = counts_str(count_by(formatted, "severity"))

        return tool_response(
            summary=f"Found {len(formatted)} security signals. Severity: {sev_str}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def get_security_signal(signal_id: str) -> dict[str, Any]:
        """Get detailed information about a specific security signal.

        Args:
            signal_id: The security signal ID.

        Returns:
            Full signal details including rule, attributes, and related entities.
        """
        try:
            data = await dd.get(f"/api/v2/security_monitoring/signals/{signal_id}")
        except Exception as e:
            return error_response(f"Failed to get security signal {signal_id}: {e}")

        signal = data.get("data", data)
        formatted = format_security_signal(signal)
        attrs = signal.get("attributes", {})
        formatted["message"] = truncate(attrs.get("message"), 1000)
        formatted["attributes_detail"] = attrs.get("attributes", {})

        return tool_response(
            summary=f"Security signal: '{formatted['title']}'  - severity={formatted['severity']}, status={formatted['status']}.",
            data=formatted,
        )

    @mcp.tool()
    async def triage_security_signal(
        signal_id: str,
        state: str,
        archive_reason: str = "",
    ) -> dict[str, Any]:
        """Update the triage state of a security signal.

        Args:
            signal_id: The security signal ID.
            state: New state: "open", "under_review", "archived".
            archive_reason: Reason if archiving: "none", "false_positive",
                            "testing_or_maintenance", "investigated_case_opened", "other".

        Returns:
            Updated signal state.
        """
        body: dict[str, Any] = {
            "data": {
                "attributes": {"state": state},
            }
        }
        if archive_reason:
            body["data"]["attributes"]["archive_reason"] = archive_reason

        try:
            data = await dd.patch(
                f"/api/v2/security_monitoring/signals/{signal_id}/state",
                body=body,
            )
        except Exception as e:
            return error_response(f"Failed to triage signal {signal_id}: {e}")

        return tool_response(
            summary=f"Updated security signal {signal_id} to state='{state}'.",
            data=data.get("data", data),
        )
