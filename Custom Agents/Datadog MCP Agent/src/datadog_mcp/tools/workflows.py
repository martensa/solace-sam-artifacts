"""Datadog Workflow Automation tools  - RESPOND stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, format_workflow


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register workflow automation tools on the MCP server."""

    @mcp.tool()
    async def list_workflows(
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List Datadog workflow automations.

        Args:
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Workflows with name, description, state, and timestamps.
        """
        params: dict[str, Any] = {
            "page[size]": page_size,
            "page[number]": page,
        }

        try:
            data = await dd.get("/api/v2/workflows", **params)
        except Exception as e:
            return error_response(f"Failed to list workflows: {e}")

        workflows = data.get("data", [])
        formatted = [format_workflow(w) for w in workflows]
        names = ", ".join(w.get("name") or "?" for w in formatted[:10])

        return tool_response(
            summary=f"Found {len(formatted)} workflows: {names}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def trigger_workflow(
        workflow_id: str,
        input_parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger a workflow automation to execute.

        Args:
            workflow_id: The workflow ID to execute.
            input_parameters: Optional input parameters for the workflow
                (e.g. {"host": "web-01", "action": "restart"}).

        Returns:
            Workflow execution instance details.
        """
        body: dict[str, Any] = {}
        if input_parameters:
            body["meta"] = {"payload": input_parameters}

        try:
            data = await dd.post(f"/api/v2/workflows/{workflow_id}/instances", body=body)
        except Exception as e:
            return error_response(f"Failed to trigger workflow {workflow_id}: {e}")

        instance = data.get("data", data)
        attrs = instance.get("attributes", {})
        return tool_response(
            summary=f"Triggered workflow {workflow_id}. Instance ID: {instance.get('id')}, status: {attrs.get('status', 'started')}.",
            data={
                "instance_id": instance.get("id"),
                "workflow_id": workflow_id,
                "status": attrs.get("status"),
                "created_at": attrs.get("created_at"),
            },
        )
