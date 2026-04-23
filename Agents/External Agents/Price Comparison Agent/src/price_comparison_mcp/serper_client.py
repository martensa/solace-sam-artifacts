"""Async HTTP client for Serper.dev Google Search proxy.

Serper.dev provides 2500 free one-time queries plus paid plans.
We prefer the /shopping endpoint for price-rich results and fall back
to /search for general web results when shopping is empty.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from .searxng_client import SearchResult

logger = logging.getLogger("price-comparison-mcp.serper")

SERPER_SHOPPING_ENDPOINT = "https://google.serper.dev/shopping"
SERPER_SEARCH_ENDPOINT = "https://google.serper.dev/search"


def _parse_price(price_str: str) -> Optional[float]:
    """Parse a price string supporting German and English locales.

    Handles: "EUR 123,45", "123,45 EUR", "123.45", "1.234,56"
    (German thousands), "1,234.56" (English thousands).
    Returns None on parse failure.
    """
    if not price_str:
        return None
    cleaned = re.sub(r"[^0-9.,]", "", str(price_str))
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.index(",") > cleaned.index("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_domain(url: str) -> str:
    """Extract domain from URL, stripping the www. prefix."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


class SerperClient:
    """Async HTTP client for Serper.dev Google Search proxy.

    If api_key is empty, search() returns [] immediately.
    """

    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self._api_key = api_key or ""
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

    async def _post(self, endpoint: str, query: str, max_results: int) -> dict:
        """Perform a single Serper POST request. Never raises."""
        body = {
            "q": query,
            "gl": "de",
            "hl": "de",
            "num": min(max_results, 20),
        }
        headers = {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }
        try:
            client = await self._get_client()
            response = await client.post(endpoint, json=body, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            logger.warning("Serper request timed out for query: %s", query[:80])
            return {}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Serper HTTP %d for query: %s",
                e.response.status_code,
                query[:80],
            )
            return {}
        except Exception as e:
            logger.warning("Serper request failed: %s", e)
            return {}

    async def search(
        self, query: str, max_results: int = 10
    ) -> list[SearchResult]:
        """Search Serper shopping (primary) and web (fallback).

        Returns a list of SearchResult objects. Never raises -- returns
        [] on any error or when the API key is not configured.
        """
        if not self._api_key:
            logger.info("Serper client not configured; skipping")
            return []

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        # Primary: shopping results with explicit prices.
        shopping_data = await self._post(
            SERPER_SHOPPING_ENDPOINT, query, max_results
        )
        shopping = shopping_data.get("shopping") or []
        for item in shopping[:max_results]:
            url = item.get("link") or ""
            if not url or url in seen_urls:
                continue
            title = item.get("title", "")
            merchant = item.get("source", "")
            price_str = item.get("price", "")
            price = _parse_price(price_str)
            snippet = merchant
            seen_urls.add(url)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    inline_price=price,
                    source_engine="serper",
                    domain=_extract_domain(url),
                )
            )

        # Fallback: general web results if shopping was empty.
        if not results:
            web_data = await self._post(
                SERPER_SEARCH_ENDPOINT, query, max_results
            )
            organic = web_data.get("organic") or []
            for item in organic[:max_results]:
                url = item.get("link") or ""
                if not url or url in seen_urls:
                    continue
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                seen_urls.add(url)
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        inline_price=None,
                        source_engine="serper",
                        domain=_extract_domain(url),
                    )
                )

        logger.info(
            "Serper returned %d results for '%s' (%d with inline prices)",
            len(results),
            query[:60],
            sum(1 for r in results if r.inline_price is not None),
        )
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
