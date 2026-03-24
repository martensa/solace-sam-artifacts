"""Datadog Cost & Usage tools  - REVIEW stage."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import tool_response, error_response


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register cost and usage tools on the MCP server."""

    @mcp.tool()
    async def get_usage_summary(
        product_family: str = "",
        from_time: str = "",
        to_time: str = "",
    ) -> dict[str, Any]:
        """Get hourly usage data across Datadog products.

        Args:
            product_family: Filter by product: "infra_hosts", "logs", "apm_hosts",
                           "custom_metrics", "synthetics", "rum", "security", "ci_visibility".
                           Empty for all products.
            from_time: Start time in ISO 8601 format (e.g. "2024-03-01T00:00:00Z").
                       Default: 24 hours ago.
            to_time: End time in ISO 8601 format. Default: now.

        Returns:
            Hourly usage data per product family.
        """
        params: dict[str, Any] = {"filter[timestamp][start]": from_time} if from_time else {}
        if to_time:
            params["filter[timestamp][end]"] = to_time
        if product_family:
            params["filter[product_families]"] = product_family

        try:
            data = await dd.get("/api/v2/usage/hourly_usage", **params)
        except Exception as e:
            return error_response(f"Failed to get usage summary: {e}")

        usage = data.get("data", [])
        formatted = []
        for u in usage[:100]:
            attrs = u.get("attributes", {})
            measurements = attrs.get("measurements", [])
            formatted.append({
                "id": u.get("id"),
                "product_family": attrs.get("product_family"),
                "timestamp": attrs.get("timestamp"),
                "org_name": attrs.get("org_name"),
                "measurements": [{
                    "usage_type": m.get("usage_type"),
                    "value": m.get("value"),
                } for m in measurements],
            })

        families = set(f.get("product_family") for f in formatted if f.get("product_family"))
        return tool_response(
            summary=f"Usage data: {len(formatted)} records across {len(families)} product families: {', '.join(sorted(families))}.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def get_estimated_cost(
        view: str = "summary",
    ) -> dict[str, Any]:
        """Get estimated cost data for the Datadog organization.

        Args:
            view: Cost view type: "summary" (total) or "sub-org" (by sub-organization).

        Returns:
            Estimated cost breakdown by product.
        """
        try:
            data = await dd.get("/api/v2/usage/estimated_cost", view=view)
        except Exception as e:
            return error_response(f"Failed to get estimated cost: {e}")

        cost_data = data.get("data", [])
        formatted = []
        for c in cost_data:
            attrs = c.get("attributes", {})
            charges = attrs.get("charges", [])
            formatted.append({
                "id": c.get("id"),
                "org_name": attrs.get("org_name"),
                "date": attrs.get("date"),
                "total_cost": attrs.get("total_cost"),
                "charges": [{
                    "product_name": ch.get("product_name"),
                    "charge_type": ch.get("charge_type"),
                    "cost": ch.get("cost"),
                } for ch in charges],
            })

        return tool_response(
            summary=f"Estimated cost data: {len(formatted)} records.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def get_hourly_usage(
        product_family: str,
        from_time: str,
        to_time: str = "",
    ) -> dict[str, Any]:
        """Get detailed hourly usage breakdown for a specific product.

        Args:
            product_family: Product to query: "infra_hosts", "logs", "apm_hosts",
                           "custom_metrics", "synthetics", "rum", "security", "ci_visibility".
            from_time: Start in ISO 8601 (e.g. "2024-03-01T00:00:00Z"). Required.
            to_time: End in ISO 8601. Default: now.

        Returns:
            Detailed hourly usage with measurements per product.
        """
        params: dict[str, Any] = {
            "filter[timestamp][start]": from_time,
            "filter[product_families]": product_family,
        }
        if to_time:
            params["filter[timestamp][end]"] = to_time

        try:
            data = await dd.get("/api/v2/usage/hourly_usage", **params)
        except Exception as e:
            return error_response(f"Failed to get hourly usage for {product_family}: {e}")

        usage = data.get("data", [])
        formatted = []
        for u in usage[:200]:
            attrs = u.get("attributes", {})
            formatted.append({
                "timestamp": attrs.get("timestamp"),
                "product_family": attrs.get("product_family"),
                "measurements": attrs.get("measurements", []),
            })

        return tool_response(
            summary=f"Hourly usage for '{product_family}': {len(formatted)} data points.",
            data=formatted, total_count=len(formatted),
        )
