"""Datadog Containers tools  - OBSERVE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_container, format_container_image,
    count_by, counts_str,
)
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register container tools on the MCP server."""

    @mcp.tool()
    async def list_containers(
        filter_query: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List running containers monitored by Datadog.

        Args:
            filter_query: Filter string (e.g. "image_name:nginx", "short_image:redis").
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Containers with name, image, state, host, and tags.
        """
        params: dict[str, Any] = {"page[size]": page_size, "page[cursor]": ""}
        if filter_query:
            params["filter[tags]"] = filter_query

        try:
            data = await dd.get("/api/v2/containers", **params)
        except Exception as e:
            return error_response(f"Failed to list containers: {e}")

        containers = data.get("data", [])
        formatted = [format_container(c) for c in containers]
        state_str = counts_str(count_by(formatted, "state"))

        return tool_response(
            summary=f"Found {len(formatted)} containers. States: {state_str}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def list_container_images(
        filter_query: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List container images deployed across the infrastructure.

        Args:
            filter_query: Filter string (e.g. "name:nginx", "tag:latest").
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Container images with name, tags, OS, and vulnerability counts.
        """
        params: dict[str, Any] = {"page[size]": page_size}
        if filter_query:
            params["filter[tags]"] = filter_query

        try:
            data = await dd.get("/api/v2/container_images", **params)
        except Exception as e:
            return error_response(f"Failed to list container images: {e}")

        images = data.get("data", [])
        formatted = [format_container_image(ci) for ci in images]

        return tool_response(
            summary=f"Found {len(formatted)} container images.",
            data=formatted, total_count=len(formatted),
        )
