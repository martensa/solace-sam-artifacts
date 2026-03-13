"""
Solace Agent Mesh Tools fuer den Preisvergleich-Agenten.

Tools fuer das LLM:
  1. search_product_prices      - Preise fuer EAN oder Produktname suchen
  2. find_cheaper_alternatives  - Guenstigere Alternativprodukte finden
  3. compare_suppliers          - Anbietervergleich fuer ein Produkt
  4. batch_search_prices        - Batch-Preissuche fuer Leistungsverzeichnisse
  5. compare_with_contract_price - Marktpreis vs. Vertragspreis
  6. export_comparison_report   - Ergebnisse als CSV-Artefakt exportieren
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import re
import statistics
from typing import Optional

from pydantic import BaseModel, Field
from solace_agent_mesh.agent.tools import ToolResult
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool, DynamicToolProvider

from .models import (
    BatchSearchItem,
    BatchSearchResultItem,
    DataSource,
    PriceComparisonResult,
    PriceInsights,
    ProductOffer,
    ProductResult,
    SearchType,
)
from .scrapers.geizhals import GeizhalsScraper
from .scrapers.google_shopping import GoogleShoppingScraper
from .scrapers.idealo import IdealoScraper
from .utils import detect_search_type, format_price, normalize_ean

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Konfigurationsmodell
# ---------------------------------------------------------------------------


class PriceAgentConfig(BaseModel):
    """Konfiguration fuer den Preisvergleich-Agenten."""

    serpapi_key: Optional[str] = Field(
        default=None,
        description="SerpAPI-Schluessel fuer Google Shopping (optional)",
    )
    enable_idealo: bool = Field(default=True, description="Idealo aktivieren")
    enable_geizhals: bool = Field(
        default=True, description="Geizhals aktivieren"
    )
    enable_google: bool = Field(
        default=True, description="Google Shopping aktivieren"
    )
    max_results_per_source: int = Field(
        default=5,
        description="Maximale Ergebnisse pro Quelle",
        ge=1,
        le=20,
    )
    request_timeout: int = Field(
        default=15, description="HTTP-Timeout in Sekunden"
    )
    scraper_proxy: Optional[str] = Field(
        default=None, description="HTTP-Proxy-URL fuer Scraper (optional)"
    )
    cache_ttl: int = Field(
        default=300,
        description="Cache-TTL in Sekunden fuer Suchergebnisse (Standard: 300)",
        ge=0,
    )


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _build_scrapers(config: PriceAgentConfig):
    """Erstellt die konfigurierten Scraper-Instanzen."""
    scrapers = []
    kwargs = {
        "timeout": config.request_timeout,
        "proxy": config.scraper_proxy,
        "cache_ttl": config.cache_ttl,
    }
    if config.enable_idealo:
        scrapers.append(IdealoScraper(**kwargs))
    if config.enable_geizhals:
        scrapers.append(GeizhalsScraper(**kwargs))
    if config.enable_google:
        scrapers.append(
            GoogleShoppingScraper(
                serpapi_key=config.serpapi_key
                or os.environ.get("SERPAPI_KEY"),
                **kwargs,
            )
        )
    return scrapers


async def _close_scrapers(scrapers) -> None:
    """Schliesst alle Scraper-Clients sicher."""
    await asyncio.gather(*[s.close() for s in scrapers], return_exceptions=True)


def _compute_insights(products: list[ProductResult]) -> Optional[PriceInsights]:
    """Berechnet statistische Preis-Insights ueber alle Angebote."""
    all_prices: list[float] = []
    merchants: set[str] = set()
    sources: set[str] = set()

    for product in products:
        for offer in product.offers:
            all_prices.append(offer.total_price)
            merchants.add(offer.merchant_name.lower())
            sources.add(offer.source.value)

    if len(all_prices) < 2:
        return None

    sorted_prices = sorted(all_prices)
    median = statistics.median(sorted_prices)
    avg = statistics.mean(sorted_prices)
    spread = sorted_prices[-1] - sorted_prices[0]
    spread_pct = (spread / median * 100) if median > 0 else 0.0

    return PriceInsights(
        min_price=sorted_prices[0],
        max_price=sorted_prices[-1],
        median_price=round(median, 2),
        avg_price=round(avg, 2),
        price_spread=round(spread, 2),
        price_spread_percent=round(spread_pct, 1),
        num_offers=len(all_prices),
        num_merchants=len(merchants),
        sources_with_results=sorted(sources),
    )


def _format_insights(insights: PriceInsights) -> str:
    """Formatiert Preis-Insights als Markdown."""
    return (
        f"\n### Preis-Insights\n"
        f"| Kennzahl | Wert |\n"
        f"|----------|------|\n"
        f"| Guenstigster Preis | {format_price(insights.min_price)} |\n"
        f"| Teuerster Preis | {format_price(insights.max_price)} |\n"
        f"| Median | {format_price(insights.median_price)} |\n"
        f"| Durchschnitt | {format_price(insights.avg_price)} |\n"
        f"| Preisspanne | {format_price(insights.price_spread)} "
        f"({insights.price_spread_percent:.1f}%) |\n"
        f"| Angebote | {insights.num_offers} von {insights.num_merchants} Haendlern |\n"
        f"| Quellen | {', '.join(insights.sources_with_results)} |\n"
    )


def _format_comparison_result(result: PriceComparisonResult, quantity: int = 1) -> str:
    """Formatiert das Ergebnis als lesbaren Text fuer das LLM."""
    lines: list[str] = []

    lines.append(f"## Preisvergleich fuer: {result.query}")
    qty_info = f" | Menge: {quantity}" if quantity > 1 else ""
    lines.append(
        f"Suchart: {result.search_type.value.upper()} | "
        f"Quellen: {', '.join(s.value for s in result.sources_queried)} | "
        f"Angebote gefunden: {result.total_offers_found}{qty_info}"
    )
    lines.append("")

    if not result.products:
        lines.append("Keine Produkte gefunden.")
    else:
        for i, product in enumerate(result.products, 1):
            lines.append(f"### Produkt {i}: {product.name}")
            if product.brand:
                lines.append(f"Marke: {product.brand}")
            if product.ean:
                lines.append(f"EAN: {product.ean}")
            if product.cheapest_price is not None:
                price_info = format_price(product.cheapest_price)
                if quantity > 1:
                    total = product.cheapest_price * quantity
                    price_info += f" (x{quantity} = {format_price(total)})"
                lines.append(
                    f"**Guenstigster Preis: {price_info} "
                    f"bei {product.cheapest_merchant}**"
                )
            if product.offers:
                lines.append("")
                if quantity > 1:
                    lines.append(
                        "| Haendler | Stueckpreis | Versand | Gesamt (Stueck) | "
                        "Gesamt (Menge) | Quelle |"
                    )
                    lines.append(
                        "|---------|-----------|---------|----------------|"
                        "----------------|--------|"
                    )
                else:
                    lines.append("| Haendler | Preis | Versand | Gesamt | Quelle |")
                    lines.append("|---------|-------|---------|--------|--------|")
                for offer in sorted(product.offers, key=lambda o: o.total_price):
                    ship = (
                        "Gratis"
                        if offer.shipping_cost == 0
                        else format_price(offer.shipping_cost)
                    )
                    if quantity > 1:
                        qty_total = offer.total_price * quantity
                        lines.append(
                            f"| {offer.merchant_name} | {format_price(offer.price)} | "
                            f"{ship} | {format_price(offer.total_price)} | "
                            f"{format_price(qty_total)} | {offer.source.value} |"
                        )
                    else:
                        lines.append(
                            f"| {offer.merchant_name} | {format_price(offer.price)} | "
                            f"{ship} | {format_price(offer.total_price)} | "
                            f"{offer.source.value} |"
                        )
            lines.append("")

    # Preis-Insights
    if result.insights:
        lines.append(_format_insights(result.insights))

    if result.alternatives:
        lines.append("---")
        lines.append("## Guenstigere Alternativen")
        lines.append("")
        for alt in result.alternatives:
            savings_info = ""
            if alt.savings and alt.savings_percent:
                savings_info = (
                    f" (Ersparnis: {format_price(alt.savings)} / "
                    f"{alt.savings_percent:.1f}%)"
                )
            lines.append(
                f"- **{alt.name}** - {format_price(alt.cheapest_price)} "
                f"bei {alt.cheapest_merchant}{savings_info}"
            )
            lines.append(f"  Quelle: {alt.source_url}")

    if result.error_messages:
        lines.append("")
        lines.append("*Hinweise:*")
        for msg in result.error_messages:
            lines.append(f"- {msg}")

    return "\n".join(lines)


def _dedup_key(name: str) -> str:
    """Erzeugt einen Deduplizierungs-Schluessel aus einem Produktnamen."""
    return name.strip().lower()


def _result_to_json(result: PriceComparisonResult) -> dict:
    """Konvertiert das Ergebnis in ein JSON-serialisierbares dict fuer Inter-Agent-Kommunikation."""
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Gemeinsame Scraper-Logik
# ---------------------------------------------------------------------------


async def _run_search(
    scrapers,
    query: str,
    stype: SearchType,
    max_results: int,
) -> tuple[list[tuple[list[ProductResult], Optional[str]]], list]:
    """Fuehrt die Suche ueber alle Scraper aus. Gibt (outputs, errors) zurueck."""

    async def run_scraper(scraper):
        try:
            if stype == SearchType.EAN:
                return await scraper.search_by_ean_cached(query, max_results), None
            else:
                return await scraper.search_by_name_cached(query, max_results), None
        except Exception as e:
            logger.error("Scraper %s fehlgeschlagen: %s", scraper.SOURCE.value, e)
            return [], f"{scraper.SOURCE.value}: {type(e).__name__}: {e}"

    outputs = await asyncio.gather(*[run_scraper(s) for s in scrapers])
    return outputs, []


def _merge_products(
    scraper_outputs: list[tuple[list[ProductResult], Optional[str]]],
) -> tuple[list[ProductResult], int]:
    """Dedupliziert und merged Produkte aus mehreren Scrapern."""
    seen: dict[str, int] = {}
    products: list[ProductResult] = []

    for products_list, _error in scraper_outputs:
        for product in products_list:
            key = _dedup_key(product.name)
            if key in seen:
                existing = products[seen[key]]
                existing.offers.extend(product.offers)
                if not existing.ean and product.ean:
                    existing.ean = product.ean
                if not existing.brand and product.brand:
                    existing.brand = product.brand
                if not existing.image_url and product.image_url:
                    existing.image_url = product.image_url
                if existing.offers:
                    cheapest = min(existing.offers, key=lambda o: o.total_price)
                    existing.cheapest_price = cheapest.total_price
                    existing.cheapest_merchant = cheapest.merchant_name
            else:
                seen[key] = len(products)
                products.append(product)

    total_offers = sum(len(p.offers) for p in products)
    products.sort(
        key=lambda p: p.cheapest_price if p.cheapest_price is not None else float("inf")
    )
    return products, total_offers


# ---------------------------------------------------------------------------
# DynamicToolProvider
# ---------------------------------------------------------------------------


class PriceComparisonToolProvider(DynamicToolProvider):
    """
    Tool-Provider fuer den Preisvergleich-Agenten.
    Registriert alle Tools beim SAM-Framework.
    """

    config_model = PriceAgentConfig

    def create_tools(
        self, tool_config: Optional[PriceAgentConfig] = None
    ) -> list[DynamicTool]:
        # All tools are registered via @register_tool decorators.
        # The framework calls _create_tools_from_decorators automatically
        # in get_all_tools_for_framework, so return empty here to avoid
        # duplicate registration.
        return []


# ---------------------------------------------------------------------------
# Tool 1: search_product_prices
# ---------------------------------------------------------------------------


@PriceComparisonToolProvider.register_tool
async def search_product_prices(
    query: str,
    search_type: Optional[str] = None,
    max_results: int = 5,
    quantity: int = 1,
    tool_config: Optional[PriceAgentConfig] = None,
    **kwargs,
) -> ToolResult:
    """
    Search current prices for a product across multiple price comparison
    sites (Idealo, Geizhals, Google Shopping).

    Args:
        query: EAN (e.g. '4006381333931') or product name (e.g. 'Bosch Akkuschrauber GSR 18V').
        search_type: 'ean' or 'name'. Auto-detected if not specified.
        max_results: Maximum number of products per source (default: 5).
        quantity: Required quantity for total price calculation (default: 1).

    Returns:
        Full price comparison with merchants, prices, links, and price insights.
    """
    if not query or not query.strip():
        return ToolResult.error("Keine Suchanfrage angegeben.")

    config = tool_config or PriceAgentConfig(
        serpapi_key=os.environ.get("SERPAPI_KEY")
    )
    max_results = min(max(1, max_results), config.max_results_per_source)
    quantity = max(1, quantity)

    detected_type = detect_search_type(query)
    if search_type and search_type.lower() in ("ean", "name"):
        stype = SearchType(search_type.lower())
    else:
        stype = SearchType(detected_type)

    if stype == SearchType.EAN:
        query = normalize_ean(query)

    scrapers = _build_scrapers(config)
    result = PriceComparisonResult(
        query=query,
        search_type=stype,
        sources_queried=[s.SOURCE for s in scrapers],
    )

    try:
        scraper_outputs, _ = await _run_search(scrapers, query, stype, max_results)

        for _products, error_msg in scraper_outputs:
            if error_msg:
                result.error_messages.append(error_msg)

        result.products, result.total_offers_found = _merge_products(scraper_outputs)
        result.insights = _compute_insights(result.products)
    finally:
        await _close_scrapers(scrapers)

    return ToolResult.ok(
        message=f"Preisvergleich fuer '{query}' abgeschlossen. "
        f"{result.total_offers_found} Angebote von {len(scrapers)} Quellen gefunden.",
        data={
            "result": _format_comparison_result(result, quantity),
            "structured": _result_to_json(result),
            "quantity": quantity,
        },
    )


# ---------------------------------------------------------------------------
# Tool 2: find_cheaper_alternatives
# ---------------------------------------------------------------------------


@PriceComparisonToolProvider.register_tool
async def find_cheaper_alternatives(
    product_name: str,
    current_price: float,
    category: Optional[str] = None,
    max_alternatives: int = 5,
    quantity: int = 1,
    tool_config: Optional[PriceAgentConfig] = None,
    **kwargs,
) -> ToolResult:
    """
    Find cheaper alternative products for a given reference product.

    Args:
        product_name: Name of the reference product (e.g. 'Kaercher K5 Hochdruckreiniger').
        current_price: Current unit price of the reference product in EUR.
        category: Product category to narrow search (e.g. 'Reinigungsgeraete'), optional.
        max_alternatives: Maximum number of alternatives (default: 5).
        quantity: Required quantity for total price calculation (default: 1).

    Returns:
        List of cheaper alternatives with prices, merchants, and savings.
    """
    if not product_name or not product_name.strip():
        return ToolResult.error("Kein Produktname angegeben.")
    if current_price <= 0:
        return ToolResult.error("Aktueller Preis muss groesser als 0 sein.")

    config = tool_config or PriceAgentConfig(
        serpapi_key=os.environ.get("SERPAPI_KEY")
    )
    max_alternatives = min(max(1, max_alternatives), 20)
    quantity = max(1, quantity)

    scrapers = _build_scrapers(config)

    try:
        async def run_alternatives(scraper):
            try:
                return await scraper.find_alternatives(
                    product_name, current_price, category, max_alternatives
                )
            except Exception as e:
                logger.error(
                    "Alternativen-Suche mit %s fehlgeschlagen: %s",
                    scraper.SOURCE.value, e
                )
                return []

        all_alternatives_lists = await asyncio.gather(
            *[run_alternatives(s) for s in scrapers]
        )

        all_alternatives = [
            alt for sublist in all_alternatives_lists for alt in sublist
        ]
        all_alternatives.sort(key=lambda a: a.cheapest_price)

        seen: set[str] = set()
        unique_alternatives = []
        for alt in all_alternatives:
            key = _dedup_key(alt.name)
            if key not in seen:
                seen.add(key)
                unique_alternatives.append(alt)

        unique_alternatives = unique_alternatives[:max_alternatives]
    finally:
        await _close_scrapers(scrapers)

    if not unique_alternatives:
        return ToolResult.ok(
            message=(
                f"Keine guenstigeren Alternativen fuer '{product_name}' "
                f"(Referenzpreis: {format_price(current_price)}) gefunden."
            ),
            data={
                "alternatives": [],
                "structured": {"alternatives": [], "reference_price": current_price},
            },
        )

    lines = [
        f"## Guenstigere Alternativen zu '{product_name}'",
        f"Referenzpreis: {format_price(current_price)}",
        f"Kategorie: {category or 'Nicht angegeben'}",
    ]
    if quantity > 1:
        lines.append(f"Menge: {quantity} | Referenz-Gesamtpreis: {format_price(current_price * quantity)}")
    lines.extend([
        "",
        "| # | Produkt | Marke | Stueckpreis | Haendler | Ersparnis/Stueck | Quelle |"
        if quantity > 1 else
        "| # | Produkt | Marke | Preis | Haendler | Ersparnis | Quelle |",
        "|---|---------|-------|-------|---------|-----------|--------|",
    ])
    for i, alt in enumerate(unique_alternatives, 1):
        savings_info = ""
        if alt.savings and alt.savings_percent:
            savings_info = f"{format_price(alt.savings)} ({alt.savings_percent:.1f}%)"
        lines.append(
            f"| {i} | {alt.name} | {alt.brand or '-'} | "
            f"{format_price(alt.cheapest_price)} | {alt.cheapest_merchant} | "
            f"{savings_info} | [{alt.source.value}]({alt.source_url}) |"
        )

    if quantity > 1:
        lines.append("")
        lines.append("### Gesamtersparnis bei gewaehlter Menge")
        for alt in unique_alternatives:
            if alt.savings:
                total_savings = alt.savings * quantity
                lines.append(
                    f"- {alt.name}: **{format_price(total_savings)}** Ersparnis "
                    f"bei {quantity} Stueck"
                )

    structured = {
        "reference_product": product_name,
        "reference_price": current_price,
        "quantity": quantity,
        "alternatives": [alt.model_dump(mode="json") for alt in unique_alternatives],
    }

    return ToolResult.ok(
        message=(
            f"{len(unique_alternatives)} guenstigere Alternative(n) fuer "
            f"'{product_name}' gefunden."
        ),
        data={
            "result": "\n".join(lines),
            "structured": structured,
        },
    )


# ---------------------------------------------------------------------------
# Tool 3: compare_suppliers
# ---------------------------------------------------------------------------


@PriceComparisonToolProvider.register_tool
async def compare_suppliers(
    query: str,
    search_type: Optional[str] = None,
    quantity: int = 1,
    tool_config: Optional[PriceAgentConfig] = None,
    **kwargs,
) -> ToolResult:
    """
    Create a compact supplier overview for a product, sorted by total
    price (including shipping).

    Args:
        query: EAN or product name.
        search_type: 'ean' or 'name' (auto-detected if not specified).
        quantity: Required quantity for total price calculation (default: 1).

    Returns:
        Supplier comparison table with prices, shipping, delivery time, and price insights.
    """
    if not query or not query.strip():
        return ToolResult.error("Keine Suchanfrage angegeben.")

    config = tool_config or PriceAgentConfig(
        serpapi_key=os.environ.get("SERPAPI_KEY")
    )
    quantity = max(1, quantity)

    detected_type = detect_search_type(query)
    if search_type and search_type.lower() in ("ean", "name"):
        stype = SearchType(search_type.lower())
    else:
        stype = SearchType(detected_type)

    if stype == SearchType.EAN:
        query = normalize_ean(query)

    scrapers = _build_scrapers(config)

    try:
        scraper_outputs, _ = await _run_search(scrapers, query, stype, max_results=3)
    finally:
        await _close_scrapers(scrapers)

    all_offers: list[ProductOffer] = []
    product_name = query
    for products_list, _error in scraper_outputs:
        for product in products_list:
            if product.name and product.name != query:
                product_name = product.name
            all_offers.extend(product.offers)

    if not all_offers:
        return ToolResult.ok(
            message=f"Keine Angebote fuer '{query}' gefunden.",
            data={
                "result": "Keine Angebote gefunden.",
                "structured": {"query": query, "offers": []},
            },
        )

    # Deduplizieren nach Haendler (guenstigstes Angebot pro Haendler)
    best_by_merchant: dict[str, ProductOffer] = {}
    for offer in all_offers:
        key = offer.merchant_name.lower()
        if key not in best_by_merchant or offer.total_price < best_by_merchant[key].total_price:
            best_by_merchant[key] = offer

    sorted_offers = sorted(best_by_merchant.values(), key=lambda o: o.total_price)

    # Insights berechnen
    prices = [o.total_price for o in sorted_offers]
    insights = None
    if len(prices) >= 2:
        median = statistics.median(prices)
        avg = statistics.mean(prices)
        spread = prices[-1] - prices[0]
        insights = PriceInsights(
            min_price=prices[0],
            max_price=prices[-1],
            median_price=round(median, 2),
            avg_price=round(avg, 2),
            price_spread=round(spread, 2),
            price_spread_percent=round(spread / median * 100, 1) if median > 0 else 0.0,
            num_offers=len(all_offers),
            num_merchants=len(sorted_offers),
            sources_with_results=sorted({o.source.value for o in sorted_offers}),
        )

    lines = [
        f"## Anbietervergleich: {product_name}",
        f"Suchanfrage: `{query}`",
    ]
    if quantity > 1:
        lines.append(f"Menge: {quantity}")
    lines.append("")

    if quantity > 1:
        lines.append(
            "| Rang | Haendler | Stueckpreis | Versand | Gesamt (Stueck) | "
            "Gesamt (Menge) | Lieferzeit | Quelle | Link |"
        )
        lines.append(
            "|------|---------|-----------|---------|----------------|"
            "----------------|------------|--------|------|"
        )
    else:
        lines.append(
            "| Rang | Haendler | Preis | Versand | Gesamt | Lieferzeit | Quelle | Link |"
        )
        lines.append(
            "|------|---------|-------|---------|--------|------------|--------|------|"
        )

    for rank, offer in enumerate(sorted_offers, 1):
        ship = "Gratis" if offer.shipping_cost == 0 else format_price(offer.shipping_cost)
        delivery = offer.delivery_time or "k.A."
        link = f"[Zum Angebot]({offer.product_url})" if offer.product_url else "-"
        if quantity > 1:
            qty_total = offer.total_price * quantity
            lines.append(
                f"| {rank} | {offer.merchant_name} | {format_price(offer.price)} | "
                f"{ship} | {format_price(offer.total_price)} | "
                f"**{format_price(qty_total)}** | "
                f"{delivery} | {offer.source.value} | {link} |"
            )
        else:
            lines.append(
                f"| {rank} | {offer.merchant_name} | {format_price(offer.price)} | "
                f"{ship} | **{format_price(offer.total_price)}** | "
                f"{delivery} | {offer.source.value} | {link} |"
            )

    best = sorted_offers[0]
    lines.append("")
    if quantity > 1:
        lines.append(
            f"**Empfehlung:** {best.merchant_name} mit "
            f"{format_price(best.total_price)} Stueckpreis "
            f"({format_price(best.total_price * quantity)} fuer {quantity} Stueck)"
        )
    else:
        lines.append(
            f"**Empfehlung:** {best.merchant_name} mit "
            f"{format_price(best.total_price)} Gesamtpreis (inkl. Versand)"
        )

    if insights:
        lines.append(_format_insights(insights))

    structured = {
        "product_name": product_name,
        "query": query,
        "quantity": quantity,
        "offers": [o.model_dump(mode="json") for o in sorted_offers],
        "insights": insights.model_dump(mode="json") if insights else None,
    }

    return ToolResult.ok(
        message=(
            f"Anbietervergleich fuer '{product_name}': "
            f"{len(sorted_offers)} Haendler verglichen."
        ),
        data={
            "result": "\n".join(lines),
            "structured": structured,
        },
    )


# ---------------------------------------------------------------------------
# Tool 4: batch_search_prices
# ---------------------------------------------------------------------------


def _parse_batch_input(raw: str) -> list[BatchSearchItem]:
    """Parst die Batch-Eingabe in einzelne Suchauftraege."""
    items: list[BatchSearchItem] = []

    # Zuerst nach Zeilenumbruechen splitten, dann nach Komma (falls einzeilig)
    lines = raw.strip().splitlines()
    if len(lines) == 1 and "," in lines[0] and "|" not in lines[0]:
        lines = [part.strip() for part in lines[0].split(",")]

    for line in lines:
        line = line.strip()
        if not line:
            continue

        label = None
        if "|" in line:
            parts = line.split("|", 1)
            line = parts[0].strip()
            label = parts[1].strip()

        # "10 x Produkt" oder "10x Produkt" Format
        qty_match = re.match(r"^(\d+)\s*[xX]\s+(.+)$", line)
        if qty_match:
            quantity = int(qty_match.group(1))
            query = qty_match.group(2).strip()
        else:
            quantity = 1
            query = line

        if query:
            items.append(BatchSearchItem(query=query, quantity=quantity, label=label))

    return items


@PriceComparisonToolProvider.register_tool
async def batch_search_prices(
    items: str,
    tool_config: Optional[PriceAgentConfig] = None,
    **kwargs,
) -> ToolResult:
    """
    Batch price search for multiple products simultaneously - ideal for
    tender documents (Leistungsverzeichnisse) and RFQs.

    Accepts a comma- or line-separated list of search queries.
    Each line can optionally include quantity and position label:
      Format: "Qty x Product/EAN [| Label]" or simply "Product/EAN"
      Examples:
        "10 x 4006381333931 | Pos. 1.1"
        "Bosch GSR 18V-28"
        "5 x Hilti TE 30-A36, 3 x Fischer FIS V 360"

    Args:
        items: Comma- or line-separated list of products/EANs with optional quantity and label.

    Returns:
        Summary price table of all searched products with total cost.
    """
    if not items or not items.strip():
        return ToolResult.error("Keine Produkte angegeben.")

    config = tool_config or PriceAgentConfig(
        serpapi_key=os.environ.get("SERPAPI_KEY")
    )

    parsed_items = _parse_batch_input(items)
    if not parsed_items:
        return ToolResult.error("Keine gueltigen Produkte in der Eingabe gefunden.")

    scrapers = _build_scrapers(config)
    results: list[BatchSearchResultItem] = []

    try:
        async def search_single(item: BatchSearchItem) -> BatchSearchResultItem:
            stype = SearchType(detect_search_type(item.query))
            query = normalize_ean(item.query) if stype == SearchType.EAN else item.query

            try:
                async def run_scraper(scraper):
                    try:
                        if stype == SearchType.EAN:
                            return await scraper.search_by_ean_cached(query, 3), None
                        else:
                            return await scraper.search_by_name_cached(query, 3), None
                    except Exception as e:
                        return [], str(e)

                outputs = await asyncio.gather(*[run_scraper(s) for s in scrapers])
                products, total_offers = _merge_products(outputs)

                if not products or not products[0].offers:
                    return BatchSearchResultItem(
                        query=item.query,
                        label=item.label,
                        quantity=item.quantity,
                        error="Keine Angebote gefunden",
                    )

                best_product = products[0]
                unit_price = best_product.cheapest_price
                insights = _compute_insights(products)

                return BatchSearchResultItem(
                    query=item.query,
                    label=item.label,
                    quantity=item.quantity,
                    product_name=best_product.name,
                    unit_price=unit_price,
                    total_price=unit_price * item.quantity if unit_price else None,
                    merchant=best_product.cheapest_merchant,
                    num_offers=total_offers,
                    insights=insights,
                )

            except Exception as e:
                logger.error("Batch-Suche fuer '%s' fehlgeschlagen: %s", item.query, e)
                return BatchSearchResultItem(
                    query=item.query,
                    label=item.label,
                    quantity=item.quantity,
                    error=f"{type(e).__name__}: {e}",
                )

        # Limit concurrency to avoid overwhelming scrapers
        sem = asyncio.Semaphore(5)

        async def _limited(item):
            async with sem:
                return await search_single(item)

        results = list(await asyncio.gather(*[_limited(item) for item in parsed_items]))
    finally:
        await _close_scrapers(scrapers)

    # Ergebnis formatieren
    lines = [
        f"## Batch-Preisvergleich - {len(results)} Positionen",
        "",
        "| Pos. | Produkt | Menge | Stueckpreis | Gesamtpreis | Haendler | Angebote | Status |",
        "|------|---------|-------|-----------|-------------|---------|----------|--------|",
    ]

    grand_total = 0.0
    success_count = 0

    for i, r in enumerate(results, 1):
        pos = r.label or f"#{i}"
        if r.error:
            lines.append(
                f"| {pos} | {r.query} | {r.quantity} | - | - | - | - | {r.error} |"
            )
        else:
            success_count += 1
            unit = format_price(r.unit_price) if r.unit_price else "-"
            total = format_price(r.total_price) if r.total_price else "-"
            if r.total_price:
                grand_total += r.total_price
            name = r.product_name or r.query
            if len(name) > 40:
                name = name[:37] + "..."
            lines.append(
                f"| {pos} | {name} | {r.quantity} | {unit} | {total} | "
                f"{r.merchant or '-'} | {r.num_offers} | OK |"
            )

    lines.append("")
    lines.append(f"**Gesamtkosten (guenstigste Angebote): {format_price(grand_total)}**")
    lines.append(f"Erfolgreich: {success_count}/{len(results)} Positionen")

    structured = {
        "items": [r.model_dump(mode="json") for r in results],
        "grand_total": round(grand_total, 2),
        "success_count": success_count,
        "total_count": len(results),
    }

    return ToolResult.ok(
        message=(
            f"Batch-Preisvergleich abgeschlossen: {success_count}/{len(results)} "
            f"Positionen gefunden. Gesamtkosten: {format_price(grand_total)}"
        ),
        data={
            "result": "\n".join(lines),
            "structured": structured,
        },
    )


# ---------------------------------------------------------------------------
# Tool 5: compare_with_contract_price
# ---------------------------------------------------------------------------


@PriceComparisonToolProvider.register_tool
async def compare_with_contract_price(
    query: str,
    contract_price: float,
    contract_supplier: Optional[str] = None,
    contract_reference: Optional[str] = None,
    quantity: int = 1,
    tool_config: Optional[PriceAgentConfig] = None,
    **kwargs,
) -> ToolResult:
    """
    Compare the current market price of a product with an existing contract
    price. Useful for checking whether contract terms are still competitive
    or renegotiation is needed.

    Can be combined with the Contract DB Agent, which provides contract
    prices from the contract database.

    Args:
        query: EAN or product name.
        contract_price: Current contract price (unit price in EUR).
        contract_supplier: Name of the contract supplier (optional).
        contract_reference: Contract number or reference (optional).
        quantity: Required quantity for total price calculation (default: 1).

    Returns:
        Contract price vs. market price comparison with savings potential and recommendation.
    """
    if not query or not query.strip():
        return ToolResult.error("Keine Suchanfrage angegeben.")
    if contract_price <= 0:
        return ToolResult.error("Vertragspreis muss groesser als 0 sein.")

    config = tool_config or PriceAgentConfig(
        serpapi_key=os.environ.get("SERPAPI_KEY")
    )
    quantity = max(1, quantity)

    detected_type = detect_search_type(query)
    stype = SearchType(detected_type)
    if stype == SearchType.EAN:
        query = normalize_ean(query)

    scrapers = _build_scrapers(config)

    try:
        scraper_outputs, _ = await _run_search(scrapers, query, stype, max_results=5)
        products, total_offers = _merge_products(scraper_outputs)
        insights = _compute_insights(products)
    finally:
        await _close_scrapers(scrapers)

    # Bestes Marktangebot ermitteln
    best_market_price = None
    best_market_merchant = None
    best_market_url = None

    for product in products:
        for offer in product.offers:
            if best_market_price is None or offer.total_price < best_market_price:
                best_market_price = offer.total_price
                best_market_merchant = offer.merchant_name
                best_market_url = offer.product_url

    lines = [
        f"## Vertragspreis-Vergleich: {query}",
    ]
    if contract_reference:
        lines.append(f"Vertragsreferenz: `{contract_reference}`")
    if contract_supplier:
        lines.append(f"Vertragslieferant: {contract_supplier}")
    lines.append("")

    lines.append("| Kennzahl | Vertrag | Markt (guenstigster) | Differenz |")
    lines.append("|----------|---------|---------------------|-----------|")

    if best_market_price is not None:
        diff = contract_price - best_market_price
        diff_pct = (diff / contract_price * 100) if contract_price > 0 else 0.0

        lines.append(
            f"| Stueckpreis | {format_price(contract_price)} | "
            f"{format_price(best_market_price)} | "
            f"{format_price(abs(diff))} ({abs(diff_pct):.1f}%) |"
        )

        if quantity > 1:
            contract_total = contract_price * quantity
            market_total = best_market_price * quantity
            diff_total = contract_total - market_total
            lines.append(
                f"| Gesamt ({quantity} Stueck) | {format_price(contract_total)} | "
                f"{format_price(market_total)} | "
                f"{format_price(abs(diff_total))} |"
            )

        lines.append("")

        if diff > 0:
            lines.append(
                f"**Bewertung: Vertragspreis liegt {format_price(diff)} "
                f"({diff_pct:.1f}%) UEBER dem besten Marktpreis.**"
            )
            if diff_pct > 15:
                lines.append(
                    "**Empfehlung: Dringender Nachverhandlungsbedarf.** "
                    f"Bester Marktpreis bei {best_market_merchant}."
                )
            elif diff_pct > 5:
                lines.append(
                    "**Empfehlung: Nachverhandlung empfohlen.** "
                    f"Bester Marktpreis bei {best_market_merchant}."
                )
            else:
                lines.append(
                    "**Empfehlung: Vertragspreis ist annaehernd marktgerecht.** "
                    "Nachverhandlung optional."
                )
        elif diff < 0:
            lines.append(
                f"**Bewertung: Vertragspreis liegt {format_price(abs(diff))} "
                f"({abs(diff_pct):.1f}%) UNTER dem besten Marktpreis.**"
            )
            lines.append(
                "**Empfehlung: Vertrag bietet gute Konditionen.** "
                "Kein Handlungsbedarf."
            )
        else:
            lines.append("**Bewertung: Vertragspreis entspricht dem Marktpreis.**")

        if best_market_url:
            lines.append(f"\nBestes Marktangebot: [{best_market_merchant}]({best_market_url})")
    else:
        lines.append(
            f"| Stueckpreis | {format_price(contract_price)} | "
            f"Keine Marktdaten | - |"
        )
        lines.append("")
        lines.append("**Keine Marktpreise gefunden.** Vertragsbewertung nicht moeglich.")

    if insights:
        lines.append(_format_insights(insights))

    structured = {
        "query": query,
        "contract_price": contract_price,
        "contract_supplier": contract_supplier,
        "contract_reference": contract_reference,
        "quantity": quantity,
        "best_market_price": best_market_price,
        "best_market_merchant": best_market_merchant,
        "price_difference": round(contract_price - best_market_price, 2) if best_market_price else None,
        "price_difference_percent": round(
            (contract_price - best_market_price) / contract_price * 100, 1
        ) if best_market_price and contract_price > 0 else None,
        "insights": insights.model_dump(mode="json") if insights else None,
        "total_offers_found": total_offers,
    }

    return ToolResult.ok(
        message=(
            f"Vertragspreis-Vergleich fuer '{query}': "
            f"Vertrag {format_price(contract_price)} vs. "
            f"Markt {format_price(best_market_price) if best_market_price else 'keine Daten'}."
        ),
        data={
            "result": "\n".join(lines),
            "structured": structured,
        },
    )


# ---------------------------------------------------------------------------
# Tool 6: export_comparison_report
# ---------------------------------------------------------------------------


@PriceComparisonToolProvider.register_tool
async def export_comparison_report(
    query: str,
    search_type: Optional[str] = None,
    quantity: int = 1,
    tool_config: Optional[PriceAgentConfig] = None,
    **kwargs,
) -> ToolResult:
    """
    Export a price comparison as a structured CSV report. Ideal for award
    memos (Vergabevermerke), offer comparisons, and procurement documentation.

    Args:
        query: EAN or product name.
        search_type: 'ean' or 'name' (auto-detected if not specified).
        quantity: Required quantity (default: 1).

    Returns:
        CSV report as text content with all offers and price insights.
    """
    if not query or not query.strip():
        return ToolResult.error("Keine Suchanfrage angegeben.")

    config = tool_config or PriceAgentConfig(
        serpapi_key=os.environ.get("SERPAPI_KEY")
    )
    quantity = max(1, quantity)

    detected_type = detect_search_type(query)
    if search_type and search_type.lower() in ("ean", "name"):
        stype = SearchType(search_type.lower())
    else:
        stype = SearchType(detected_type)

    if stype == SearchType.EAN:
        query = normalize_ean(query)

    scrapers = _build_scrapers(config)

    try:
        scraper_outputs, _ = await _run_search(scrapers, query, stype, max_results=5)
        products, total_offers = _merge_products(scraper_outputs)
        insights = _compute_insights(products)
    finally:
        await _close_scrapers(scrapers)

    # CSV erstellen
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # Header-Metadaten
    writer.writerow(["Preisvergleich-Report"])
    writer.writerow(["Suchanfrage", query])
    writer.writerow(["Suchart", stype.value.upper()])
    writer.writerow(["Menge", str(quantity)])
    writer.writerow(["Angebote gefunden", str(total_offers)])
    writer.writerow([])

    if insights:
        writer.writerow(["Preis-Insights"])
        writer.writerow(["Guenstigster Preis", f"{insights.min_price:.2f}"])
        writer.writerow(["Teuerster Preis", f"{insights.max_price:.2f}"])
        writer.writerow(["Median", f"{insights.median_price:.2f}"])
        writer.writerow(["Durchschnitt", f"{insights.avg_price:.2f}"])
        writer.writerow(["Preisspanne", f"{insights.price_spread:.2f}"])
        writer.writerow(["Preisspanne %", f"{insights.price_spread_percent:.1f}%"])
        writer.writerow(["Haendler", str(insights.num_merchants)])
        writer.writerow([])

    # Angebotsliste
    writer.writerow([
        "Produkt", "Marke", "EAN", "Haendler", "Preis (EUR)",
        "Versand (EUR)", "Gesamtpreis (EUR)", "Gesamtpreis x Menge (EUR)",
        "Lieferzeit", "Quelle", "URL",
    ])

    for product in products:
        for offer in sorted(product.offers, key=lambda o: o.total_price):
            writer.writerow([
                product.name,
                product.brand or "",
                product.ean or "",
                offer.merchant_name,
                f"{offer.price:.2f}",
                f"{offer.shipping_cost:.2f}",
                f"{offer.total_price:.2f}",
                f"{offer.total_price * quantity:.2f}",
                offer.delivery_time or "",
                offer.source.value,
                offer.product_url,
            ])

    csv_content = output.getvalue()
    output.close()

    return ToolResult.ok(
        message=(
            f"CSV-Report fuer '{query}' erstellt: "
            f"{total_offers} Angebote in {len(products)} Produkten."
        ),
        data={
            "result": f"CSV-Report erstellt mit {total_offers} Angeboten.\n\n```csv\n{csv_content}```",
            "csv_content": csv_content,
            "filename": f"preisvergleich_{query.replace(' ', '_')[:30]}.csv",
            "structured": {
                "query": query,
                "total_offers": total_offers,
                "products_count": len(products),
                "insights": insights.model_dump(mode="json") if insights else None,
            },
        },
    )
