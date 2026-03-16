"""Geizhals.de Scraper fuer Preisvergleiche."""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from typing import List, Optional

from bs4 import BeautifulSoup

from ..models import DataSource, ProductOffer, ProductResult
from ..utils import normalize_ean
from .base import BaseScraper

logger = logging.getLogger(__name__)

GEIZHALS_BASE = "https://geizhals.de"
GEIZHALS_SEARCH = f"{GEIZHALS_BASE}/"


class GeizhalsScraper(BaseScraper):
    """Scraper fuer geizhals.de."""

    SOURCE = DataSource.GEIZHALS
    BASE_URL = GEIZHALS_BASE

    async def search_by_ean(
        self, ean: str, max_results: int = 5
    ) -> List[ProductResult]:
        cleaned = normalize_ean(ean)
        return await self._search(cleaned, max_results)

    async def search_by_name(
        self, name: str, max_results: int = 5
    ) -> List[ProductResult]:
        return await self._search(name, max_results)

    async def _search(self, query: str, max_results: int) -> List[ProductResult]:
        """Suche auf Geizhals."""
        url = (
            f"{GEIZHALS_SEARCH}?fs={urllib.parse.quote_plus(query)}"
            f"&in=&pg=1&sale=1&sort=p"
        )
        results: List[ProductResult] = []

        try:
            response = await self._get(
                url,
                headers={
                    "Referer": GEIZHALS_BASE + "/",
                },
            )
            soup = BeautifulSoup(response.text, "html.parser")

            # Produktliste
            product_items = soup.select(
                "article.listview__item, div.listview__item, "
                "[class*='product-list'] article"
            )

            # Karten parsen und Detail-URLs sammeln
            parsed: list[tuple[ProductResult, str]] = []
            for item in product_items[:max_results]:
                product = self._parse_product_item(item)
                if product:
                    detail_url = ""
                    detail_link = item.select_one("a[href*='?a=']")
                    if detail_link and detail_link.get("href"):
                        href = detail_link["href"]
                        detail_url = (
                            href if href.startswith("http") else f"{GEIZHALS_BASE}{href}"
                        )
                    parsed.append((product, detail_url))

            # Detail-Seiten parallel abrufen
            async def _enrich(product: ProductResult, detail_url: str) -> ProductResult:
                if detail_url:
                    detailed = await self._fetch_price_list(detail_url, product)
                    if detailed:
                        return detailed
                return product

            enriched = await asyncio.gather(
                *[_enrich(p, u) for p, u in parsed]
            )
            results.extend(enriched)

        except Exception as e:
            logger.error("Geizhals Suche fehlgeschlagen fuer '%s': %s", query, e)

        return results

    def _parse_product_item(self, item) -> Optional[ProductResult]:
        """Parst ein Listenelement von Geizhals."""
        try:
            # Name
            name_el = item.select_one(
                "span.listview__name, [class*='product-name'], h2, h3"
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                return None

            # Guenstigster Preis
            price_el = item.select_one(
                "span.price, [class*='best-price'], [class*='price__amount']"
            )
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = self._parse_price(price_text)

            # Link
            link_el = item.select_one("a[href]")
            source_url = ""
            if link_el:
                href = link_el["href"]
                source_url = href if href.startswith("http") else f"{GEIZHALS_BASE}{href}"

            # Haendler
            merchant_el = item.select_one("[class*='merchant'], [class*='shop']")
            merchant = merchant_el.get_text(strip=True) if merchant_el else "Geizhals-Haendler"

            if price is None:
                return ProductResult(name=name, offers=[])

            offer = ProductOffer(
                merchant_name=merchant,
                price=price,
                shipping_cost=0.0,
                total_price=price,
                product_url=source_url,
                source_url=source_url,
                source=DataSource.GEIZHALS,
            )
            return ProductResult(name=name, offers=[offer])

        except Exception as e:
            logger.debug("Geizhals: Fehler beim Parsen eines Produkts: %s", e)
            return None

    async def _fetch_price_list(
        self, detail_url: str, base_product: ProductResult
    ) -> Optional[ProductResult]:
        """Laedt die vollstaendige Preisliste von einer Geizhals-Detailseite."""
        try:
            response = await self._get(detail_url)
            soup = BeautifulSoup(response.text, "html.parser")

            offers: List[ProductOffer] = []

            # Preistabelle
            price_rows = soup.select(
                "tr.offer, tr[class*='offer'], "
                "div.offer-list__item, [class*='offer-row']"
            )

            for row in price_rows:
                try:
                    # Haendlername
                    merchant_el = row.select_one(
                        "[class*='merchant'], [class*='shop-name'], td.merchant"
                    )
                    merchant = (
                        merchant_el.get_text(strip=True) if merchant_el else "Unbekannt"
                    )

                    # Preis
                    price_el = row.select_one(
                        "td.price, [class*='price'], span.price"
                    )
                    price = self._parse_price(
                        price_el.get_text(strip=True) if price_el else ""
                    )
                    if price is None:
                        continue

                    # Versand
                    ship_el = row.select_one(
                        "[class*='shipping'], [class*='versand'], td.delivery"
                    )
                    shipping_text = ship_el.get_text(strip=True) if ship_el else ""
                    shipping = self._parse_shipping(shipping_text)

                    # Lieferzeit
                    delivery_el = row.select_one(
                        "[class*='delivery-time'], [class*='lieferzeit']"
                    )
                    delivery_time = (
                        delivery_el.get_text(strip=True) if delivery_el else None
                    )

                    # Haendler-Link
                    link_el = row.select_one("a[href]")
                    product_link = ""
                    if link_el:
                        href = link_el["href"]
                        product_link = (
                            href if href.startswith("http") else f"{GEIZHALS_BASE}{href}"
                        )

                    offers.append(
                        ProductOffer(
                            merchant_name=merchant,
                            price=price,
                            shipping_cost=shipping,
                            total_price=price + shipping,
                            product_url=product_link or detail_url,
                            source_url=detail_url,
                            source=DataSource.GEIZHALS,
                            delivery_time=delivery_time,
                        )
                    )
                except Exception as e:
                    logger.debug("Geizhals: Fehler beim Parsen eines Angebots: %s", e)

            if not offers:
                return None

            # Produktmetadaten
            name_el = soup.select_one("h1[class*='title'], h1")
            name = name_el.get_text(strip=True) if name_el else base_product.name

            brand_el = soup.select_one("[class*='brand'], [itemprop='brand']")
            brand = brand_el.get_text(strip=True) if brand_el else None

            ean = self._extract_ean(soup)

            return ProductResult(
                name=name,
                ean=ean,
                brand=brand,
                offers=sorted(offers, key=lambda o: o.total_price),
            )

        except Exception as e:
            logger.debug("Geizhals: Fehler beim Laden der Detailseite: %s", e)
            return None

    def _extract_ean(self, soup: BeautifulSoup) -> Optional[str]:
        text = soup.get_text()
        match = re.search(r"EAN[:\s]+(\d{8}|\d{12,14})", text, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"GTIN[:\s]+(\d{8}|\d{12,14})", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None
