"""Abstrakte Basisklasse fuer alle Preisvergleich-Scraper."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import List, Optional

import httpx

from ..models import AlternativeProduct, DataSource, ProductOffer, ProductResult
from ..utils import COMMON_HEADERS, calculate_savings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Einfacher TTL-Cache fuer Suchergebnisse
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_TTL = 300  # 5 Minuten


class _ResultCache:
    """In-Memory-Cache mit TTL fuer Scraper-Ergebnisse."""

    def __init__(self, ttl: int = _DEFAULT_CACHE_TTL) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[float, list]] = {}

    def get(self, key: str) -> Optional[list]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: list) -> None:
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]


class BaseScraper(ABC):
    """Abstrakte Basisklasse fuer Preisvergleich-Scraper."""

    SOURCE: DataSource
    BASE_URL: str

    def __init__(
        self,
        timeout: int = 15,
        max_retries: int = 2,
        proxy: Optional[str] = None,
        cache_ttl: int = _DEFAULT_CACHE_TTL,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
        self._proxy = proxy
        self._cache = _ResultCache(ttl=cache_ttl)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            kwargs: dict = {
                "headers": COMMON_HEADERS,
                "timeout": self.timeout,
                "follow_redirects": True,
            }
            if self._proxy:
                kwargs["proxy"] = self._proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _get(self, url: str, headers: Optional[dict] = None, **kwargs) -> httpx.Response:
        """HTTP GET mit Retry-Logik."""
        client = await self._get_client()
        if headers:
            kwargs["headers"] = headers
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 429, 503):
                    wait = 2 ** attempt
                    logger.warning(
                        "%s: Rate limit/Service unavailable, warte %ds (Versuch %d/%d)",
                        self.SOURCE.value, wait, attempt + 1, self.max_retries + 1
                    )
                    await asyncio.sleep(wait)
                    last_error = e
                else:
                    raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                wait = 2 ** attempt
                logger.warning(
                    "%s: Verbindungsfehler, warte %ds (Versuch %d/%d): %s",
                    self.SOURCE.value, wait, attempt + 1, self.max_retries + 1, e
                )
                await asyncio.sleep(wait)
                last_error = e

        raise RuntimeError(
            f"{self.SOURCE.value}: Alle {self.max_retries + 1} Versuche fehlgeschlagen. "
            f"Letzter Fehler: {last_error}"
        )

    # ------------------------------------------------------------------
    # Gemeinsame Parser (ehemals in jedem Scraper dupliziert)
    # ------------------------------------------------------------------

    def _parse_price(self, text: str) -> Optional[float]:
        """Parst einen Preisstring zu einem float."""
        if not text:
            return None
        text = text.replace("EUR", "").strip()
        # Format: 1.234,56
        match = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", text)
        if match:
            return float(match.group(1).replace(".", "").replace(",", "."))
        # Format: 1234.56 oder 1234,56
        match = re.search(r"(\d+)[.,](\d{2})\b", text)
        if match:
            return float(f"{match.group(1)}.{match.group(2)}")
        # Nur ganze Zahl
        match = re.search(r"(\d+)", text)
        if match:
            return float(match.group(1))
        return None

    def _parse_shipping(self, text: str) -> float:
        """Parst Versandkostentext zu float, 0.0 bei 'Gratis'."""
        text_lower = text.lower()
        if any(w in text_lower for w in ("gratis", "kostenlos", "frei", "0,00", "0.00")):
            return 0.0
        price = self._parse_price(text)
        return price if price is not None else 0.0

    # ------------------------------------------------------------------
    # Cached search wrappers
    # ------------------------------------------------------------------

    def _cache_key(self, method: str, query: str, max_results: int) -> str:
        return f"{self.SOURCE.value}:{method}:{query.lower().strip()}:{max_results}"

    async def search_by_ean_cached(
        self, ean: str, max_results: int = 5
    ) -> List[ProductResult]:
        """search_by_ean mit Cache."""
        key = self._cache_key("ean", ean, max_results)
        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("%s: Cache-Hit fuer EAN '%s'", self.SOURCE.value, ean)
            return cached
        result = await self.search_by_ean(ean, max_results)
        self._cache.set(key, result)
        return result

    async def search_by_name_cached(
        self, name: str, max_results: int = 5
    ) -> List[ProductResult]:
        """search_by_name mit Cache."""
        key = self._cache_key("name", name, max_results)
        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("%s: Cache-Hit fuer Name '%s'", self.SOURCE.value, name)
            return cached
        result = await self.search_by_name(name, max_results)
        self._cache.set(key, result)
        return result

    # ------------------------------------------------------------------
    # Abstrakte Methoden
    # ------------------------------------------------------------------

    @abstractmethod
    async def search_by_ean(
        self, ean: str, max_results: int = 5
    ) -> List[ProductResult]:
        """Sucht Produkte anhand der EAN."""

    @abstractmethod
    async def search_by_name(
        self, name: str, max_results: int = 5
    ) -> List[ProductResult]:
        """Sucht Produkte anhand des Namens."""

    # ------------------------------------------------------------------
    # Gemeinsame find_alternatives-Logik
    # ------------------------------------------------------------------

    async def find_alternatives(
        self,
        product_name: str,
        reference_price: float,
        category: Optional[str] = None,
        max_results: int = 5,
    ) -> List[AlternativeProduct]:
        """Sucht guenstigere Alternativen."""
        query = f"{category} {product_name}" if category else product_name
        search_results = await self.search_by_name_cached(query, max_results=max_results * 2)

        alternatives: List[AlternativeProduct] = []
        for product in search_results:
            if not product.offers:
                continue
            cheapest = min(product.offers, key=lambda o: o.total_price)
            if cheapest.total_price < reference_price * 0.95:
                savings, savings_pct = calculate_savings(
                    reference_price, cheapest.total_price
                )
                alternatives.append(
                    AlternativeProduct(
                        name=product.name,
                        brand=product.brand,
                        cheapest_price=cheapest.total_price,
                        cheapest_merchant=cheapest.merchant_name,
                        product_url=cheapest.product_url,
                        source_url=cheapest.source_url,
                        source=self.SOURCE,
                        savings=savings,
                        savings_percent=savings_pct,
                    )
                )

        return sorted(alternatives, key=lambda a: a.cheapest_price)[:max_results]
