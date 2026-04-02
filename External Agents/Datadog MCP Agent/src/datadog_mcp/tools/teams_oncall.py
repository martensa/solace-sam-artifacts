"""Datadog Teams & On-Call tools  - RESPOND stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, format_team
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register teams and on-call tools on the MCP server."""

    @mcp.tool()
    async def list_teams(
        query: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List teams in the Datadog organization.

        Args:
            query: Search teams by name.
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Teams with name, handle, summary, and member count.
        """
        params: dict[str, Any] = {
            "page[size]": page_size,
            "page[number]": page,
        }
        if query:
            params["filter[keyword]"] = query

        try:
            data = await dd.get("/api/v2/team", **params)
        except Exception as e:
            return error_response(f"Failed to list teams: {e}")

        teams = data.get("data", [])
        formatted = [format_team(t) for t in teams]
        total = data.get("meta", {}).get("pagination", {}).get("total_count", len(formatted))
        names = ", ".join(t.get("name") or "?" for t in formatted[:10])

        return tool_response(
            summary=f"Found {total} teams: {names}.",
            data=formatted, total_count=total, page=page, has_more=len(teams) == page_size,
        )

    @mcp.tool()
    async def list_oncall_schedules(
        include: str = "",
    ) -> dict[str, Any]:
        """List on-call schedules configured in Datadog.

        Args:
            include: Comma-separated related resources to include (e.g. "users,teams").

        Returns:
            On-call schedules with name, timezone, layers, and current rotation.
        """
        params: dict[str, Any] = {}
        if include:
            params["include"] = include

        try:
            # The on-call API requires fetching individual schedules;
            # we use the teams API to discover on-call info
            data = await dd.get("/api/v2/on-call/schedules", **params)
        except Exception as e:
            return error_response(f"Failed to list on-call schedules: {e}")

        schedules = data.get("data", [])
        formatted = []
        for s in schedules:
            attrs = s.get("attributes", {})
            formatted.append({
                "id": s.get("id"),
                "name": attrs.get("name"),
                "timezone": attrs.get("timezone"),
                "layers": attrs.get("layers", []),
                "created_at": attrs.get("created_at"),
                "modified_at": attrs.get("modified_at"),
            })

        names = ", ".join(s.get("name") or "?" for s in formatted[:10])
        return tool_response(
            summary=f"Found {len(formatted)} on-call schedules: {names}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def get_current_oncall(
        schedule_id: str,
    ) -> dict[str, Any]:
        """Get who is currently on call for a specific schedule.

        Args:
            schedule_id: The on-call schedule ID.

        Returns:
            Current on-call rotation with user details and shift times.
        """
        try:
            data = await dd.get(f"/api/v2/on-call/schedules/{schedule_id}", include="users")
        except Exception as e:
            return error_response(f"Failed to get on-call for schedule {schedule_id}: {e}")

        schedule = data.get("data", {})
        attrs = schedule.get("attributes", {})
        included = data.get("included", [])

        users = []
        for inc in included:
            if inc.get("type") == "users":
                user_attrs = inc.get("attributes", {})
                users.append({
                    "id": inc.get("id"),
                    "name": user_attrs.get("name"),
                    "email": user_attrs.get("email"),
                })

        return tool_response(
            summary=f"On-call schedule '{attrs.get('name')}': {len(users)} user(s) currently on call.",
            data={
                "schedule_id": schedule_id,
                "schedule_name": attrs.get("name"),
                "timezone": attrs.get("timezone"),
                "current_oncall_users": users,
                "layers": attrs.get("layers", []),
            },
        )
