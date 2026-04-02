"""Datadog Incidents tools  - RESPOND stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_incident, count_by, counts_str,
)


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register incident tools on the MCP server."""

    @mcp.tool()
    async def list_incidents(
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List Datadog incidents.

        Args:
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Incidents with status, severity, and timeline info.
        """
        try:
            data = await dd.get(
                "/api/v2/incidents",
                **{"page[size]": page_size, "page[offset]": (page - 1) * page_size},
            )
        except Exception as e:
            return error_response(f"Failed to list incidents: {e}")

        incidents = data.get("data", [])
        formatted = [format_incident(i) for i in incidents]
        sev_str = counts_str(count_by(formatted, "severity"))
        total = data.get("meta", {}).get("pagination", {}).get("total", len(formatted))

        return tool_response(
            summary=f"Found {total} incidents. Severity: {sev_str}.",
            data=formatted, total_count=total, page=page, has_more=len(incidents) == page_size,
        )

    @mcp.tool()
    async def get_incident(incident_id: str) -> dict[str, Any]:
        """Get detailed information about a specific incident.

        Args:
            incident_id: The incident ID string.

        Returns:
            Full incident with timeline, severity, commander, and customer impact.
        """
        try:
            data = await dd.get(f"/api/v2/incidents/{incident_id}")
        except Exception as e:
            return error_response(f"Failed to get incident {incident_id}: {e}")

        incident = data.get("data", data)
        formatted = format_incident(incident)
        attrs = incident.get("attributes", {})
        formatted["timeline"] = attrs.get("timeline", {})
        formatted["notification_handles"] = attrs.get("notification_handles", [])
        formatted["fields"] = attrs.get("fields", {})

        return tool_response(
            summary=f"Incident '{formatted['title']}': status={formatted['status']}, severity={formatted['severity']}.",
            data=formatted,
        )

    @mcp.tool()
    async def create_incident(
        title: str,
        severity: str = "UNKNOWN",
        customer_impacted: bool = False,
        customer_impact_scope: str = "",
        notification_handles: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new Datadog incident.

        Args:
            title: Incident title describing the issue.
            severity: Severity level: "SEV-1", "SEV-2", "SEV-3", "SEV-4", "SEV-5", or "UNKNOWN".
            customer_impacted: Whether customers are affected.
            customer_impact_scope: Description of customer impact.
            notification_handles: List of notification handles (e.g. ["@slack-ops", "@pagerduty"]).

        Returns:
            Created incident details.
        """
        attrs: dict[str, Any] = {
            "title": title,
            "severity": severity,
            "customer_impacted": customer_impacted,
        }
        if customer_impact_scope:
            attrs["customer_impact_scope"] = customer_impact_scope
        if notification_handles:
            attrs["notification_handles"] = [{"display_name": h, "handle": h} for h in notification_handles]

        body = {"data": {"type": "incidents", "attributes": attrs}}

        try:
            data = await dd.post("/api/v2/incidents", body=body)
        except Exception as e:
            return error_response(f"Failed to create incident: {e}")

        incident = data.get("data", data)
        return tool_response(
            summary=f"Created incident '{title}' (ID: {incident.get('id')}) with severity {severity}.",
            data=format_incident(incident),
        )

    @mcp.tool()
    async def update_incident(
        incident_id: str,
        title: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        customer_impact_scope: str | None = None,
        customer_impacted: bool | None = None,
    ) -> dict[str, Any]:
        """Update a Datadog incident's status, severity, or other fields.

        Args:
            incident_id: The incident ID string.
            title: New title (optional).
            status: New status: "active", "stable", "resolved" (optional).
            severity: New severity: "SEV-1" through "SEV-5" or "UNKNOWN" (optional).
            customer_impact_scope: Impact description (optional).
            customer_impacted: Whether customers are impacted (optional).

        Returns:
            Updated incident details.
        """
        attrs: dict[str, Any] = {}
        if title is not None: attrs["title"] = title
        if status is not None: attrs["state"] = status
        if severity is not None: attrs["severity"] = severity
        if customer_impact_scope is not None: attrs["customer_impact_scope"] = customer_impact_scope
        if customer_impacted is not None: attrs["customer_impacted"] = customer_impacted
        if not attrs:
            return error_response("No fields provided to update.")

        body = {"data": {"id": incident_id, "type": "incidents", "attributes": attrs}}

        try:
            data = await dd.patch(f"/api/v2/incidents/{incident_id}", body=body)
        except Exception as e:
            return error_response(f"Failed to update incident {incident_id}: {e}")

        incident = data.get("data", data)
        return tool_response(
            summary=f"Updated incident {incident_id}.",
            data=format_incident(incident),
        )
