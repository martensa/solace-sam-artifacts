"""Google Shopping Scraper - nutzt SerpAPI falls konfiguriert, sonst direkt."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..models import DataSource, ProductOffer, ProductResult
from .base import BaseScraper

logger = logging.getLogger(__name__)

SERPAPI_BASE = "https://serpapi.com/search"
GOOGLE_SHOPPING_BASE = "https://www.google.de/search"


class GoogleShoppingScraper(BaseScraper):
    """
    Scraper fuer Google Shopping.

    Bevorzugt SerpAPI (strukturierte JSON-Antworten) wenn ``serpapi_key``
    gesetzt ist. Ohne Key wird ein einfacher HTML-Scraper verwendet,
    der deutlich weniger zuverlaessig ist.
    """

    SOURCE = DataSource.GOOGLE_SHOPPING
    BASE_URL = GOOGLE_SHOPPING_BASE

    def __init__(self, serpapi_key: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.serpapi_key = serpapi_key

    async def search_by_ean(
        self, ean: str, max_results: int = 5
    ) -> List[ProductResult]:
        return await self.search_by_name(ean, max_results)

    async def search_by_name(
        self, name: str, max_results: int = 5
    ) -> List[ProductResult]:
        if self.serpapi_key:
            return await self._search_serpapi(name, max_results)
        return await self._search_html(name, max_results)

    # ------------------------------------------------------------------
    # SerpAPI-Implementierung (bevorzugt)
    # ------------------------------------------------------------------

    async def _search_serpapi(
        self, query: str, max_results: int
    ) -> List[ProductResult]:
        """Suche ueber SerpAPI - liefert strukturierte Daten."""
        params = {
            "engine": "google_shopping",
            "q": query,
            "gl": "de",
            "hl": "de",
            "api_key": self.serpapi_key,
            "num": str(max_results * 2),
        }
        url = f"{SERPAPI_BASE}?{urllib.parse.urlencode(params)}"

        try:
            response = await self._get(url)
            data = response.json()
            return self._parse_serpapi_response(data, max_results)
        except Exception as e:
            logger.error("SerpAPI Anfrage fehlgeschlagen fuer '%s': %s", query, e)
            return []

    def _parse_serpapi_response(
        self, data: Dict[str, Any], max_results: int
    ) -> List[ProductResult]:
        """Parst die SerpAPI JSON-Antwort."""
        results: List[ProductResult] = []
        shopping_results = data.get("shopping_results", [])

        for item in shopping_results[:max_results]:
            try:
                price_str = item.get("price", "0")
                price = self._parse_price(price_str)
                if price is None:
                    continue

                offer = ProductOffer(
                    merchant_name=item.get("source", "Google Shopping"),
                    price=price,
                    shipping_cost=0.0,
                    total_price=price,
                    product_url=item.get("link", ""),
                    source_url=item.get("link", ""),
                    source=DataSource.GOOGLE_SHOPPING,
                    rating=item.get("rating"),
                    rating_count=item.get("reviews"),
                )

                product = ProductResult(
                    name=item.get("title", "Unbekannt"),
                    brand=item.get("brand"),
                    image_url=item.get("thumbnail"),
                    offers=[offer],
                )
                results.append(product)
            except Exception as e:
                logger.debug("SerpAPI: Fehler beim Parsen eines Eintrags: %s", e)

        return results

    # ------------------------------------------------------------------
    # HTML-Scraping als Fallback
    # ------------------------------------------------------------------

    async def _search_html(
        self, query: str, max_results: int
    ) -> List[ProductResult]:
        """
        Fallback: Direkte Google-Shopping-Suche per HTML-Scraping.
        Weniger zuverlaessig, da Google das Layout haeufig aendert.
        """
        params = {
            "q": query,
            "tbm": "shop",
            "hl": "de",
            "gl": "de",
            "num": "20",
        }
        url = f"{GOOGLE_SHOPPING_BASE}?{urllib.parse.urlencode(params)}"

        try:
            response = await self._get(url)
            soup = BeautifulSoup(response.text, "html.parser")
            return self._parse_google_html(soup, max_results)
        except Exception as e:
            logger.error(
                "Google Shopping HTML-Scraping fehlgeschlagen fuer '%s': %s", query, e
            )
            return []

    def _parse_google_html(
        self, soup: BeautifulSoup, max_results: int
    ) -> List[ProductResult]:
        """Parst Google Shopping HTML-Suchergebnisse."""
        results: List[ProductResult] = []

        # Google Shopping Cards - Selektoren koennen veralten
        cards = soup.select(
            "div.sh-dgr__content, div[class*='sh-pr__product'], "
            "div.g-blk, div[jsname='Sx9Kwc']"
        )

        for card in cards[:max_results]:
            try:
                name_el = card.select_one(
                    "h3, [class*='title'], [aria-label]"
                )
                name = name_el.get_text(strip=True) if name_el else ""
                if not name:
                    continue

                price_el = card.select_one(
                    "[class*='price'], span[aria-label*='EUR']"
                )
                price_text = ""
                if price_el:
                    price_text = price_el.get("aria-label", "") or price_el.get_text(strip=True)
                price = self._parse_price(price_text)
                if price is None:
                    continue

                merchant_el = card.select_one(
                    "[class*='merchant'], [class*='seller'], span.E5ocAb"
                )
                merchant = (
                    merchant_el.get_text(strip=True)
                    if merchant_el
                    else "Google Shopping"
                )

                link_el = card.select_one("a[href]")
                product_url = link_el["href"] if link_el else ""
                if product_url.startswith("/"):
                    product_url = f"https://www.google.de{product_url}"

                offer = ProductOffer(
                    merchant_name=merchant,
                    price=price,
                    shipping_cost=0.0,
                    total_price=price,
                    product_url=product_url,
                    source_url=product_url,
                    source=DataSource.GOOGLE_SHOPPING,
                )
                results.append(ProductResult(name=name, offers=[offer]))

            except Exception as e:
                logger.debug("Google HTML: Fehler beim Parsen einer Karte: %s", e)

        return results
