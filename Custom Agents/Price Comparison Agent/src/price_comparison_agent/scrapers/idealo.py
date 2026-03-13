"""Idealo.de Scraper fuer Preisvergleiche."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from typing import List, Optional

from bs4 import BeautifulSoup

from ..models import DataSource, ProductOffer, ProductResult
from ..utils import normalize_ean
from .base import BaseScraper

logger = logging.getLogger(__name__)

IDEALO_BASE = "https://www.idealo.de"
IDEALO_SEARCH = f"{IDEALO_BASE}/preisvergleich/MainSearchProductCategory.html"
IDEALO_API_SEARCH = f"{IDEALO_BASE}/offerlist/api/offers"


class IdealoScraper(BaseScraper):
    """Scraper fuer idealo.de."""

    SOURCE = DataSource.IDEALO
    BASE_URL = IDEALO_BASE

    async def search_by_ean(
        self, ean: str, max_results: int = 5
    ) -> List[ProductResult]:
        cleaned_ean = normalize_ean(ean)
        return await self._search(cleaned_ean, max_results)

    async def search_by_name(
        self, name: str, max_results: int = 5
    ) -> List[ProductResult]:
        return await self._search(name, max_results)

    async def _search(self, query: str, max_results: int) -> List[ProductResult]:
        """Suche auf Idealo und parse Ergebnisse."""
        url = f"{IDEALO_SEARCH}?q={urllib.parse.quote_plus(query)}"
        results: List[ProductResult] = []

        try:
            response = await self._get(url)
            soup = BeautifulSoup(response.text, "html.parser")

            # Produktkarten aus der Suchergebnisseite extrahieren
            product_cards = soup.select("div.sr-resultList__item, article.offerList-item")
            if not product_cards:
                product_cards = soup.select("[class*='result-item'], [class*='offerList']")

            # Karten parsen und Detail-URLs sammeln
            parsed: list[tuple[ProductResult, str]] = []
            for card in product_cards[:max_results]:
                product = self._parse_search_card(card, query)
                if product:
                    detail_url = ""
                    if product.offers and product.offers[0].source_url:
                        detail_url = product.offers[0].source_url
                    parsed.append((product, detail_url))

            # Detail-Seiten parallel abrufen
            async def _enrich(product: ProductResult, detail_url: str) -> ProductResult:
                if detail_url:
                    detailed = await self._fetch_product_detail(detail_url, product)
                    if detailed:
                        return detailed
                return product

            enriched = await asyncio.gather(
                *[_enrich(p, u) for p, u in parsed]
            )
            results.extend(enriched)

            # Falls keine Karten per CSS gefunden, versuche JSON-LD
            if not results:
                results = self._parse_json_ld(soup, query)

        except Exception as e:
            logger.error("Idealo Suche fehlgeschlagen fuer '%s': %s", query, e)

        return results

    def _parse_search_card(
        self, card, query: str
    ) -> Optional[ProductResult]:
        """Parst eine einzelne Produktkarte aus den Suchergebnissen."""
        try:
            # Produktname
            name_el = card.select_one(
                "span.sr-resultList__title, h2.offerList-item__title, "
                "[class*='title'], [class*='name']"
            )
            name = name_el.get_text(strip=True) if name_el else query

            # Preis
            price_el = card.select_one(
                "span.price__value, [class*='price']"
            )
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = self._parse_price(price_text)

            # URL zur Idealo-Produktseite
            link_el = card.select_one("a[href*='/preisvergleich/']")
            if not link_el:
                link_el = card.select_one("a")
            source_url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                source_url = href if href.startswith("http") else f"{IDEALO_BASE}{href}"

            if price is None and not source_url:
                return None

            offer = ProductOffer(
                merchant_name="idealo.de",
                price=price or 0.0,
                shipping_cost=0.0,
                total_price=price or 0.0,
                product_url=source_url,
                source_url=source_url,
                source=DataSource.IDEALO,
            )

            return ProductResult(name=name, offers=[offer] if price else [])

        except Exception as e:
            logger.debug("Fehler beim Parsen einer Idealo-Karte: %s", e)
            return None

    async def _fetch_product_detail(
        self, product_url: str, base_product: ProductResult
    ) -> Optional[ProductResult]:
        """Laedt die Detailseite eines Produkts und extrahiert alle Angebote."""
        try:
            response = await self._get(product_url)
            soup = BeautifulSoup(response.text, "html.parser")

            # Versuche Angebote aus der Preisliste zu extrahieren
            offers = self._parse_offer_list(soup, product_url)

            # EAN aus Produktdetails
            ean = self._extract_ean(soup)

            # Produktname verfeinern
            name_el = soup.select_one(
                "h1.oopStage-title, h1[class*='title'], h1"
            )
            name = name_el.get_text(strip=True) if name_el else base_product.name

            # Marke
            brand_el = soup.select_one("[class*='brand'], [itemprop='brand']")
            brand = brand_el.get_text(strip=True) if brand_el else None

            # Bild
            img_el = soup.select_one(
                "img.oopStage-image, img[class*='product'], img[itemprop='image']"
            )
            image_url = None
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src")

            return ProductResult(
                name=name,
                ean=ean,
                brand=brand,
                image_url=image_url,
                offers=offers if offers else base_product.offers,
            )

        except Exception as e:
            logger.debug("Fehler beim Laden der Idealo-Produktdetails: %s", e)
            return None

    def _parse_offer_list(
        self, soup: BeautifulSoup, product_url: str
    ) -> List[ProductOffer]:
        """Extrahiert die Haendler-Angebote aus einer Idealo-Produktseite."""
        offers: List[ProductOffer] = []

        # Idealo verwendet oft strukturierte Daten oder dedizierte Klassen
        offer_rows = soup.select(
            "div.oopShopList-item, div.shopList-item, "
            "[class*='shopList'] [class*='item']"
        )

        for row in offer_rows:
            try:
                # Haendlername
                merchant_el = row.select_one(
                    "[class*='shopName'], [class*='merchant'], span.shop-name"
                )
                merchant = merchant_el.get_text(strip=True) if merchant_el else "Unbekannt"

                # Preis
                price_el = row.select_one("[class*='price']")
                price = self._parse_price(
                    price_el.get_text(strip=True) if price_el else ""
                )
                if price is None:
                    continue

                # Versand
                shipping_el = row.select_one(
                    "[class*='shipping'], [class*='versand']"
                )
                shipping_text = shipping_el.get_text(strip=True) if shipping_el else ""
                shipping = self._parse_shipping(shipping_text)

                # Shop-URL
                link_el = row.select_one("a[href]")
                product_link = ""
                if link_el:
                    href = link_el["href"]
                    product_link = href if href.startswith("http") else f"{IDEALO_BASE}{href}"

                offers.append(
                    ProductOffer(
                        merchant_name=merchant,
                        price=price,
                        shipping_cost=shipping,
                        total_price=price + shipping,
                        product_url=product_link or product_url,
                        source_url=product_url,
                        source=DataSource.IDEALO,
                    )
                )
            except Exception as e:
                logger.debug("Fehler beim Parsen eines Idealo-Angebots: %s", e)

        return sorted(offers, key=lambda o: o.total_price)

    def _parse_json_ld(
        self, soup: BeautifulSoup, query: str
    ) -> List[ProductResult]:
        """Versucht Produktdaten aus JSON-LD Markup zu extrahieren."""
        results: List[ProductResult] = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    data = data[0] if data else {}
                if data.get("@type") in ("Product", "ItemList"):
                    name = data.get("name", query)
                    offers_data = data.get("offers", {})
                    if isinstance(offers_data, dict):
                        offers_data = [offers_data]
                    parsed_offers = []
                    for o in offers_data:
                        price = float(o.get("price", 0))
                        if price <= 0:
                            continue
                        parsed_offers.append(
                            ProductOffer(
                                merchant_name=o.get("seller", {}).get("name", "Unbekannt"),
                                price=price,
                                shipping_cost=0.0,
                                total_price=price,
                                product_url=o.get("url", ""),
                                source_url=o.get("url", ""),
                                source=DataSource.IDEALO,
                            )
                        )
                    if parsed_offers:
                        results.append(ProductResult(name=name, offers=parsed_offers))
            except Exception:
                pass
        return results

    def _extract_ean(self, soup: BeautifulSoup) -> Optional[str]:
        """Extrahiert die EAN aus der Produktseite."""
        # Suche in Meta-Tags
        for meta in soup.find_all("meta"):
            content = meta.get("content", "")
            if re.match(r"^\d{8}$|^\d{12,14}$", content.strip()):
                return content.strip()

        # Suche in Text
        text = soup.get_text()
        match = re.search(r"EAN[:\s]+(\d{8}|\d{12,14})", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None
