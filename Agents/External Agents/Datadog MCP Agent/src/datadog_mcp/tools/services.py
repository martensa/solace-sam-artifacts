"""Datadog Service Catalog tools  - ANALYZE stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response, format_service
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register service catalog tools on the MCP server."""

    @mcp.tool()
    async def list_services(
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List services from the Datadog Service Catalog.

        Args:
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Services with name, team, description, and contacts.
        """
        try:
            data = await dd.get(
                "/api/v2/services/definitions",
                **{"page[size]": page_size, "page[number]": page},
            )
        except Exception as e:
            return error_response(f"Failed to list services: {e}")

        services = data.get("data", [])
        formatted = [format_service(s) for s in services]
        total = data.get("meta", {}).get("count", len(formatted))
        names = ", ".join(s.get("name") or "?" for s in formatted[:10])

        return tool_response(
            summary=f"Found {total} services: {names}{'...' if total > 10 else ''}.",
            data=formatted, total_count=total, page=page, has_more=len(services) == page_size,
        )

    @mcp.tool()
    async def get_service_definition(service_name: str) -> dict[str, Any]:
        """Get full definition of a service from the Service Catalog.

        Args:
            service_name: Service name to look up.

        Returns:
            Service definition: team, contacts, links, documentation, integrations.
        """
        try:
            data = await dd.get(f"/api/v2/services/definitions/{service_name}")
        except Exception as e:
            return error_response(f"Failed to get service '{service_name}': {e}")

        service = data.get("data", data)
        formatted = format_service(service)

        return tool_response(
            summary=f"Service '{service_name}': team={formatted.get('team')}, {len(formatted.get('contacts', []))} contacts, {len(formatted.get('links', []))} links.",
            data=formatted,
        )

    @mcp.tool()
    async def get_service_dependencies(
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Get service dependency relationships from the Software Catalog.

        Args:
            page: Page number (default 1).
            page_size: Results per page (default 100).

        Returns:
            Service-to-service dependency relationships.
        """
        try:
            data = await dd.get(
                "/api/v2/catalog/relation",
                **{"page[limit]": page_size, "page[offset]": (page - 1) * page_size},
            )
        except Exception as e:
            return error_response(f"Failed to get service dependencies: {e}")

        relations = data.get("data", [])
        formatted = []
        for r in relations:
            attrs = r.get("attributes", {})
            formatted.append({
                "id": r.get("id"),
                "type": attrs.get("type"),
                "source": attrs.get("source", {}).get("name"),
                "target": attrs.get("target", {}).get("name"),
            })

        return tool_response(
            summary=f"Found {len(formatted)} service dependency relationships.",
            data=formatted, total_count=len(formatted), page=page, has_more=len(relations) == page_size,
        )
