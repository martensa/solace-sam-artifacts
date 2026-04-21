"""CSV export tool for price comparison results."""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

from ..errors import ErrorCode, tool_error

logger = logging.getLogger("price-comparison-mcp.export")


async def handle_export_report(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Export price comparison results as CSV text."""
    results_json = arguments.get("results_json", "")
    include_insights = arguments.get("include_insights", True)
    response_mode = arguments.get("response_mode", "full")

    if not results_json:
        return tool_error(ErrorCode.INVALID_PARAMETER, "results_json must not be empty")

    try:
        data = json.loads(results_json)
    except json.JSONDecodeError as e:
        return tool_error(ErrorCode.INVALID_PARAMETER, f"Invalid JSON: {e}")

    # Handle both single search and batch results
    if "items" in data:
        # Batch result
        return _export_batch(data, include_insights, response_mode)
    elif "offers" in data:
        # Single search result
        return _export_single(data, include_insights, response_mode)
    else:
        return tool_error(ErrorCode.INVALID_PARAMETER, "JSON must contain 'offers' or 'items' key")


def _export_single(data: dict, include_insights: bool, response_mode: str) -> dict[str, Any]:
    """Export a single search result as CSV."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # Header
    writer.writerow([
        "Produkt", "Haendler", "Preis (EUR)", "Versand (EUR)",
        "Gesamt (EUR)", "Quelle", "URL",
    ])

    query = data.get("query", "")
    offers = data.get("offers", [])

    for offer in offers:
        writer.writerow([
            query,
            offer.get("merchant", ""),
            f"{offer.get('price', 0):.2f}".replace(".", ","),
            f"{offer.get('shipping_cost', 0):.2f}".replace(".", ","),
            f"{offer.get('total_price', 0):.2f}".replace(".", ","),
            offer.get("source", ""),
            offer.get("url", ""),
        ])

    # Insights
    if include_insights and data.get("insights"):
        insights = data["insights"]
        writer.writerow([])
        writer.writerow(["Statistik", "Wert"])
        writer.writerow(["Min. Preis", f"{insights.get('min_price', 0):.2f}".replace(".", ",")])
        writer.writerow(["Max. Preis", f"{insights.get('max_price', 0):.2f}".replace(".", ",")])
        writer.writerow(["Median", f"{insights.get('median_price', 0):.2f}".replace(".", ",")])
        writer.writerow(["Angebote", str(insights.get("num_offers", 0))])
        writer.writerow(["Haendler", str(insights.get("num_merchants", 0))])

    csv_text = output.getvalue()
    rows = len(offers)

    if response_mode == "summary":
        return {"content": [{"type": "text", "text": f"CSV report: {rows} offers for {query}"}]}

    return {"content": [{"type": "text", "text": csv_text}]}


def _export_batch(data: dict, include_insights: bool, response_mode: str) -> dict[str, Any]:
    """Export batch search results as CSV."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    writer.writerow([
        "Position", "Artikel", "Menge", "Status",
        "Guenstigster Preis (EUR)", "Haendler", "URL",
    ])

    items = data.get("items", [])
    total_rows = 0

    for item in items:
        label = item.get("label", "")
        query = item.get("query", "")
        quantity = item.get("quantity", 1)
        status = item.get("status", "")
        cheapest = item.get("cheapest")

        if cheapest:
            writer.writerow([
                label,
                query,
                str(quantity),
                status,
                f"{cheapest.get('total_price', 0):.2f}".replace(".", ","),
                cheapest.get("merchant", ""),
                cheapest.get("url", ""),
            ])
        else:
            writer.writerow([label, query, str(quantity), status, "", "", ""])
        total_rows += 1

    csv_text = output.getvalue()

    if response_mode == "summary":
        return {"content": [{"type": "text", "text": f"CSV batch report: {total_rows} items"}]}

    return {"content": [{"type": "text", "text": csv_text}]}
