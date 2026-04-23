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


def _de(value: float) -> str:
    """Format a float as German decimal string."""
    return f"{value:.2f}".replace(".", ",")


def _export_single(data: dict, include_insights: bool, response_mode: str) -> dict[str, Any]:
    """Export a single search result as CSV."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # Header -- includes all B2B context columns a procurement user
    # would want when importing into Excel or an ERP system.
    writer.writerow([
        "Produkt", "Haendler", "Preis (EUR)", "Versand (EUR)",
        "Gesamt (EUR)", "MwSt", "Verfuegbarkeit", "Login noetig",
        "Staffelpreis", "Staffeln", "MOQ", "Match", "Quelle",
        "Ausreisser", "Grund", "URL",
    ])

    query = data.get("query", "")
    offers = data.get("offers", [])

    def _vat_de(v: str) -> str:
        return {"net": "netto", "gross": "brutto"}.get(v, "unbekannt")

    def _avail_de(v: str) -> str:
        return {
            "in_stock": "auf Lager",
            "out_of_stock": "nicht verfuegbar",
            "on_request": "auf Anfrage",
            "backorder": "Lieferzeit",
        }.get(v, "")

    def _tiers_str(tiers: Any) -> str:
        if not tiers or not isinstance(tiers, list):
            return ""
        return " | ".join(
            f"ab {t.get('min_qty')} Stk.: {_de(t.get('price', 0) or 0)} EUR"
            for t in tiers if isinstance(t, dict)
        )

    for offer in offers:
        is_outlier = bool(offer.get("is_outlier", False))
        reason = offer.get("outlier_reason") or ""
        moq = offer.get("min_order_quantity")
        writer.writerow([
            query,
            offer.get("merchant", ""),
            _de(offer.get("price", 0) or 0),
            _de(offer.get("shipping_cost", 0) or 0),
            _de(offer.get("total_price", 0) or 0),
            _vat_de(offer.get("vat_status", "")),
            _avail_de(offer.get("availability", "")),
            "ja" if offer.get("login_required") else "nein",
            "ja" if offer.get("has_tier_pricing") else "nein",
            _tiers_str(offer.get("tier_pricing")),
            str(moq) if moq else "",
            offer.get("match_confidence", ""),
            offer.get("source", ""),
            "ja" if is_outlier else "nein",
            reason,
            offer.get("url", ""),
        ])

    # Append login-gated merchants (no price, just the context note)
    for gated in data.get("login_gated_merchants", []):
        writer.writerow([
            query,
            gated.get("merchant", ""),
            "", "", "",
            _vat_de(gated.get("vat_status", "")),
            "",
            "ja",
            "ja" if gated.get("has_tier_pricing") else "nein",
            "",
            "",
            "",
            "login-gate",
            "nein",
            gated.get("note", ""),
            gated.get("url", ""),
        ])

    # Insights
    if include_insights and data.get("insights"):
        insights = data["insights"]
        filtered = insights.get("filtered")

        writer.writerow([])
        writer.writerow(["Statistik (alle Angebote)", "Wert"])
        writer.writerow(["Min. Preis", _de(insights.get("min_price", 0))])
        writer.writerow(["Max. Preis", _de(insights.get("max_price", 0))])
        writer.writerow(["Median", _de(insights.get("median_price", 0))])
        if insights.get("avg_price") is not None:
            writer.writerow(["Durchschnitt", _de(insights.get("avg_price", 0))])
        writer.writerow(["Angebote", str(insights.get("num_offers", 0))])
        writer.writerow(["Haendler", str(insights.get("num_merchants", 0))])

        # Filtered (non-outlier) statistics, when available -- these are
        # usually the more meaningful numbers to act on.
        if filtered:
            writer.writerow([])
            writer.writerow(["Statistik (ohne Ausreisser)", "Wert"])
            writer.writerow(["Min. Preis", _de(filtered.get("min_price", 0))])
            writer.writerow(["Max. Preis", _de(filtered.get("max_price", 0))])
            writer.writerow(["Median", _de(filtered.get("median_price", 0))])
            writer.writerow(["Durchschnitt", _de(filtered.get("avg_price", 0))])
            writer.writerow(["Angebote", str(filtered.get("num_offers", 0))])
            writer.writerow(["Ausreisser entfernt", str(filtered.get("outliers_excluded", 0))])

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
