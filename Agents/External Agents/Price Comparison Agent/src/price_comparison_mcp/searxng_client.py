"""Async HTTP client for SearXNG meta-search engine."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger("price-comparison-mcp.searxng")

# Regex patterns for inline price extraction from search snippets
# German locale: 1.234,56 EUR or 123,45 Euro or 99,99EUR
_PRICE_PATTERN = re.compile(
    r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*(?:EUR|Euro)",
    re.IGNORECASE,
)

# Also match simple formats: 123.45 EUR (English locale in some results)
_PRICE_PATTERN_EN = re.compile(
    r"(\d{1,6}\.\d{2})\s*(?:EUR|Euro)",
    re.IGNORECASE,
)


class SearchResult:
    """A single search result with optional inline price."""

    __slots__ = ("title", "url", "snippet", "inline_price", "source_engine", "domain")

    def __init__(
        self,
        title: str,
        url: str,
        snippet: str,
        inline_price: Optional[float] = None,
        source_engine: str = "",
        domain: str = "",
    ) -> None:
        self.title = title
        self.url = url
        self.snippet = snippet
        self.inline_price = inline_price
        self.source_engine = source_engine
        self.domain = domain

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet[:200] if self.snippet else "",
            "inline_price": self.inline_price,
            "source_engine": self.source_engine,
            "domain": self.domain,
        }


def _extract_inline_price(text: str) -> Optional[float]:
    """Extract the first price from a text string (German or English locale)."""
    # Try German format first (more common for our use case)
    match = _PRICE_PATTERN.search(text)
    if match:
        price_str = match.group(1).replace(".", "").replace(",", ".")
        try:
            return float(price_str)
        except ValueError:
            pass

    # Try English format
    match = _PRICE_PATTERN_EN.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


class SearXNGClient:
    """Async HTTP client for SearXNG JSON API."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 30,
        append_price_term: bool = True,
    ) -> list[SearchResult]:
        """Search SearXNG and return results with inline prices extracted.

        Args:
            query: Search query (product name or EAN).
            max_results: Maximum results to return.
            append_price_term: Whether to append "Preis" to the query for
                better price discovery.

        Returns:
            List of SearchResult objects. Never raises -- returns empty list on error.
        """
        search_query = f"{query} Preis kaufen" if append_price_term else query
        url = f"{self._base_url}/search"
        params = {
            "q": search_query,
            "format": "json",
            "language": "de-DE",
        }

        try:
            client = await self._get_client()
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            logger.warning("SearXNG request timed out for query: %s", query[:80])
            return []
        except httpx.HTTPStatusError as e:
            logger.warning("SearXNG HTTP %d for query: %s", e.response.status_code, query[:80])
            return []
        except Exception as e:
            logger.warning("SearXNG request failed: %s", e)
            return []

        raw_results = data.get("results", [])
        results: list[SearchResult] = []

        for item in raw_results[:max_results]:
            title = item.get("title", "")
            result_url = item.get("url", "")
            snippet = item.get("content", "")
            engine = item.get("engine", "")

            if not result_url:
                continue

            # Extract inline price from title + snippet
            combined_text = f"{title} {snippet}"
            inline_price = _extract_inline_price(combined_text)
            domain = _extract_domain(result_url)

            results.append(SearchResult(
                title=title,
                url=result_url,
                snippet=snippet,
                inline_price=inline_price,
                source_engine=engine,
                domain=domain,
            ))

        logger.info(
            "SearXNG returned %d results for '%s' (%d with inline prices)",
            len(results),
            query[:60],
            sum(1 for r in results if r.inline_price is not None),
        )
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
