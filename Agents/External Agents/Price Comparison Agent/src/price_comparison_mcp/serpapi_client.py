"""Async HTTP client for SerpAPI Google Shopping (optional)."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger("price-comparison-mcp.serpapi")

SERPAPI_ENDPOINT = "https://serpapi.com/search"


class SerpAPIResult:
    """A single Google Shopping result from SerpAPI."""

    __slots__ = ("title", "price", "merchant", "url", "thumbnail", "source")

    def __init__(
        self,
        title: str,
        price: Optional[float],
        merchant: str,
        url: str,
        thumbnail: str = "",
        source: str = "serpapi",
    ) -> None:
        self.title = title
        self.price = price
        self.merchant = merchant
        self.url = url
        self.thumbnail = thumbnail
        self.source = source

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "price": self.price,
            "merchant": self.merchant,
            "url": self.url,
            "source": self.source,
        }


def _parse_serpapi_price(price_str: str) -> Optional[float]:
    """Parse a price string from SerpAPI (e.g., '$189.99' or '189,99 EUR')."""
    if not price_str:
        return None
    import re
    # Remove currency symbols and whitespace
    cleaned = re.sub(r"[^0-9.,]", "", price_str)
    if not cleaned:
        return None

    # German format: 1.234,56
    if "," in cleaned and "." in cleaned:
        if cleaned.index(",") > cleaned.index("."):
            # German: dots are thousands, comma is decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        # else English: commas are thousands
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Could be German decimal or English thousands
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            # Likely German decimal: 123,45
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


class SerpAPIClient:
    """Async HTTP client for SerpAPI Google Shopping.

    Only instantiated if a valid API key is provided.
    """

    def __init__(self, api_key: str, timeout: float = 5.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def search(self, query: str, max_results: int = 10) -> list[SerpAPIResult]:
        """Search Google Shopping via SerpAPI.

        Returns:
            List of SerpAPIResult objects. Never raises -- returns empty list on error.
        """
        if not self._api_key:
            return []

        params = {
            "engine": "google_shopping",
            "q": query,
            "gl": "de",
            "hl": "de",
            "api_key": self._api_key,
            "num": str(min(max_results, 20)),
        }

        try:
            client = await self._get_client()
            response = await client.get(SERPAPI_ENDPOINT, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            logger.warning("SerpAPI request timed out for query: %s", query[:80])
            return []
        except httpx.HTTPStatusError as e:
            logger.warning("SerpAPI HTTP %d for query: %s", e.response.status_code, query[:80])
            return []
        except Exception as e:
            logger.warning("SerpAPI request failed: %s", e)
            return []

        shopping_results = data.get("shopping_results", [])
        results: list[SerpAPIResult] = []

        for item in shopping_results[:max_results]:
            title = item.get("title", "")
            price_str = item.get("extracted_price") or item.get("price", "")
            merchant = item.get("source", "")
            url = item.get("link", "")
            thumbnail = item.get("thumbnail", "")

            # Parse price
            if isinstance(price_str, (int, float)):
                price = float(price_str)
            elif isinstance(price_str, str):
                price = _parse_serpapi_price(price_str)
            else:
                price = None

            if url:
                results.append(SerpAPIResult(
                    title=title,
                    price=price,
                    merchant=merchant,
                    url=url,
                    thumbnail=thumbnail,
                ))

        logger.info(
            "SerpAPI returned %d shopping results for '%s'",
            len(results),
            query[:60],
        )
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
