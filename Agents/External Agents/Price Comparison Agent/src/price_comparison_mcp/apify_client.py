"""Async HTTP client for the Apify Google Shopping Scraper actor.

Uses the run-sync-get-dataset-items endpoint so one HTTP call both
starts the actor run and returns its results. Apify actors typically
take longer than direct search APIs, so the default timeout is 30s.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from .searxng_client import SearchResult

logger = logging.getLogger("price-comparison-mcp.apify")

APIFY_ENDPOINT = (
    "https://api.apify.com/v2/acts/apify~google-shopping-scraper/"
    "run-sync-get-dataset-items"
)


def _parse_price(price_str: str) -> Optional[float]:
    """Parse a price string supporting German and English locales.

    Handles: "123.45", "EUR 123,45", "123,45 EUR", "1.234,56"
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


class ApifyGoogleShoppingClient:
    """Async HTTP client for the Apify Google Shopping Scraper actor.

    Runs can take 10-30s, so the default timeout is longer than the
    other clients. If api_token is empty, search() returns [].
    """

    def __init__(self, api_token: str, timeout: float = 30.0) -> None:
        self._api_token = api_token or ""
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def available(self) -> bool:
        return bool(self._api_token)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def search(
        self, query: str, max_results: int = 10
    ) -> list[SearchResult]:
        """Run the Apify Google Shopping actor for the given query.

        Returns a list of SearchResult objects. Never raises -- returns
        [] on any error or when the API token is not configured.
        """
        if not self._api_token:
            logger.info("Apify client not configured; skipping")
            return []

        body = {
            "queries": [query],
            "maxItems": min(max_results, 20),
            "locationUqid": "DE",
            "language": "de",
        }
        params = {"token": self._api_token}

        try:
            client = await self._get_client()
            response = await client.post(
                APIFY_ENDPOINT, params=params, json=body
            )
            response.raise_for_status()
            items = response.json()
        except httpx.TimeoutException:
            logger.warning("Apify request timed out for query: %s", query[:80])
            return []
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Apify HTTP %d for query: %s",
                e.response.status_code,
                query[:80],
            )
            return []
        except Exception as e:
            logger.warning("Apify request failed: %s", e)
            return []

        if not isinstance(items, list):
            logger.warning("Apify returned non-list payload: %s", type(items))
            return []

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for item in items[:max_results]:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if not url or url in seen_urls:
                continue
            title = item.get("title", "")
            merchant = item.get("source", "")
            price_raw = item.get("price", "")
            if isinstance(price_raw, (int, float)):
                price: Optional[float] = float(price_raw)
            else:
                price = _parse_price(price_raw)
            snippet = merchant
            seen_urls.add(url)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    inline_price=price,
                    source_engine="apify",
                    domain=_extract_domain(url),
                )
            )

        logger.info(
            "Apify returned %d results for '%s' (%d with inline prices)",
            len(results),
            query[:60],
            sum(1 for r in results if r.inline_price is not None),
        )
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
