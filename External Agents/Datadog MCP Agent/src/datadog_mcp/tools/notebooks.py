"""Datadog Notebooks tools  - ANALYZE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, format_notebook, truncate
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register notebook tools on the MCP server."""

    @mcp.tool()
    async def list_notebooks(
        query: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List Datadog notebooks (operational runbooks, investigation docs).

        Args:
            query: Search notebooks by name.
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Notebooks with name, author, status, and cell count.
        """
        params: dict[str, Any] = {"count": 1000}
        if query:
            params["query"] = query

        try:
            data = await dd.get("/api/v1/notebooks", **params)
        except Exception as e:
            return error_response(f"Failed to list notebooks: {e}")

        notebooks = data.get("data", [])
        formatted = [format_notebook(n.get("attributes", n)) for n in notebooks]
        page_items, total, has_more = paginate_list(formatted, page, page_size)

        return tool_response(
            summary=f"Found {total} notebooks" + (f" matching '{query}'" if query else "") + ".",
            data=page_items, total_count=total, page=page, has_more=has_more,
        )

    @mcp.tool()
    async def get_notebook(notebook_id: int) -> dict[str, Any]:
        """Get a notebook's content including all cells (runbook/investigation doc).

        Args:
            notebook_id: The numeric notebook ID.

        Returns:
            Notebook content with cells (markdown, timeseries, queries, etc.).
        """
        try:
            data = await dd.get(f"/api/v1/notebooks/{notebook_id}")
        except Exception as e:
            return error_response(f"Failed to get notebook {notebook_id}: {e}")

        nb = data.get("data", {})
        attrs = nb.get("attributes", {})
        cells = attrs.get("cells", [])

        cell_summary = []
        for c in cells[:30]:
            cell_attrs = c.get("attributes", {})
            cell_def = cell_attrs.get("definition", {})
            cell_summary.append({
                "id": c.get("id"),
                "type": cell_def.get("type"),
                "text": truncate(cell_def.get("text"), 200) if cell_def.get("type") == "markdown" else None,
            })

        result = format_notebook(attrs)
        result["cells"] = cell_summary
        result["time"] = attrs.get("time", {})

        return tool_response(
            summary=f"Notebook '{result.get('name')}': {len(cells)} cells.",
            data=result,
        )
