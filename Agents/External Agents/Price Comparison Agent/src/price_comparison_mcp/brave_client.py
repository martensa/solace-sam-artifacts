"""Async HTTP client for Brave Search API (free tier 2000/month).

Returns SearchResult objects compatible with SearXNG results so the
pipeline can consume them uniformly.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from .searxng_client import SearchResult

logger = logging.getLogger("price-comparison-mcp.brave")

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _parse_price(price_str: str) -> Optional[float]:
    """Parse a price string supporting German and English locales.

    Handles: "123.45", "EUR 123,45", "123,45 EUR", "123,45 EUR",
    "1.234,56" (German thousands), "1,234.56" (English thousands).
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


def _extract_inline_price_from_text(text: str) -> Optional[float]:
    """Best-effort inline price extraction from description text."""
    if not text:
        return None
    match = re.search(
        r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d{1,6}\.\d{2})\s*(?:EUR|Euro|\u20ac)",
        text,
        re.IGNORECASE,
    )
    if match:
        return _parse_price(match.group(1))
    return None


class BraveSearchClient:
    """Async HTTP client for the Brave Search API.

    Free tier offers ~2000 queries/month with a single subscription key.
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

    async def _request(
        self,
        query: str,
        max_results: int,
        result_filter: Optional[str] = None,
    ) -> dict:
        """Perform a single Brave API request. Never raises."""
        params = {
            "q": query,
            "country": "DE",
            "search_lang": "de",
            "count": str(min(max_results, 20)),
        }
        if result_filter:
            params["result_filter"] = result_filter

        headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
        }

        try:
            client = await self._get_client()
            response = await client.get(
                BRAVE_ENDPOINT, params=params, headers=headers
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            logger.warning("Brave request timed out for query: %s", query[:80])
            return {}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Brave HTTP %d for query: %s", e.response.status_code, query[:80]
            )
            return {}
        except Exception as e:
            logger.warning("Brave request failed: %s", e)
            return {}

    async def search(
        self, query: str, max_results: int = 10
    ) -> list[SearchResult]:
        """Search Brave Web for the given query.

        Returns a list of SearchResult objects. Never raises -- returns
        [] on any error or when the API key is not configured.

        Brave's /v1/web/search supports `result_filter` with values
        web / discussions / faq / infobox / news / videos / locations
        / summarizer / query -- there is NO `products` filter on the
        public API (despite earlier code that tried to use one and
        burned 5 of every 9 calls on HTTP 422). We instead rely on
        Brave's web results, which already surface shop pages with
        inline prices in the description for shopping-intent queries.
        Inline-price extraction over the title + description lifts
        those into our SearchResult.inline_price slot.
        """
        if not self._api_key:
            logger.info("Brave client not configured; skipping")
            return []

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        # Web search -- single API call covers the full free-tier budget
        # without burning quota on a 422-returning products endpoint.
        web_data = await self._request(query, max_results)
        web_results = (web_data.get("web") or {}).get("results") or []
        for item in web_results[:max_results]:
            url = item.get("url") or ""
            if not url or url in seen_urls:
                continue
            title = item.get("title", "")
            description = item.get("description", "")
            price = _extract_inline_price_from_text(
                f"{title} {description}"
            )
            seen_urls.add(url)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=description,
                    inline_price=price,
                    source_engine="brave",
                    domain=_extract_domain(url),
                )
            )

        logger.info(
            "Brave returned %d results for '%s' (%d with inline prices)",
            len(results),
            query[:60],
            sum(1 for r in results if r.inline_price is not None),
        )
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
