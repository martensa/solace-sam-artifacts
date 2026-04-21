"""Main search pipeline: SearXNG + optional SerpAPI + Playwright detail fetch."""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
import time
from typing import Any, Optional
from urllib.parse import urlparse

from ..browser_manager import BrowserManager, simulate_human_mouse, simulate_human_scroll
from ..cache import TTLCache
from ..config import PriceSearchConfig
from ..errors import ErrorCode, tool_error
from ..price_extractor import ExtractedOffer, extract_prices, parse_price
from ..searxng_client import SearXNGClient, SearchResult

logger = logging.getLogger("price-comparison-mcp.search")

# URL scoring for prioritization
_PRICE_SITE_SCORES: dict[str, int] = {
    "idealo.de": 100,
    "geizhals.de": 95,
    "geizhals.at": 95,
    "billiger.de": 90,
    "guenstiger.de": 85,
    "preis.de": 85,
    "preisvergleich.de": 80,
    "amazon.de": 70,
    "otto.de": 65,
    "mediamarkt.de": 60,
    "saturn.de": 60,
    "notebooksbilliger.de": 55,
    "alternate.de": 55,
    "conrad.de": 55,
    "reichelt.de": 55,
    "voelkner.de": 55,
    "elektro4000.de": 50,
    "ebay.de": 40,
}


def _score_url(url: str) -> int:
    """Score a URL for prioritization. Higher = more likely to have price data."""
    try:
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        return 0

    # Exact match
    score = _PRICE_SITE_SCORES.get(domain, 0)
    if score > 0:
        return score

    # Partial match (subdomains)
    for known_domain, known_score in _PRICE_SITE_SCORES.items():
        if domain.endswith(f".{known_domain}") or domain == known_domain:
            return known_score

    # Generic shop indicators in URL
    lower_url = url.lower()
    if any(w in lower_url for w in ("/product", "/produkt", "/shop", "/kaufen")):
        return 30

    return 10


def _deduplicate_urls(urls: list[str]) -> list[str]:
    """Remove duplicate URLs (normalize trailing slashes, www prefix)."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        normalized = url.rstrip("/")
        parsed = urlparse(normalized)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        key = f"{domain}{parsed.path}"
        if key not in seen:
            seen.add(key)
            result.append(url)
    return result


def _detect_search_type(query: str) -> str:
    """Detect if query is an EAN barcode or product name."""
    cleaned = re.sub(r"[\s\-]", "", query.strip())
    if cleaned.isdigit() and len(cleaned) in (8, 12, 13, 14):
        return "ean"
    return "name"


def _compute_insights(offers: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute price insights from a list of offer dicts."""
    prices = [o["total_price"] for o in offers if o.get("total_price")]
    if len(prices) < 2:
        return {}

    merchants = set(o.get("merchant", "").lower() for o in offers if o.get("merchant"))
    sorted_prices = sorted(prices)

    return {
        "min_price": sorted_prices[0],
        "max_price": sorted_prices[-1],
        "median_price": round(statistics.median(sorted_prices), 2),
        "avg_price": round(statistics.mean(sorted_prices), 2),
        "price_spread": round(sorted_prices[-1] - sorted_prices[0], 2),
        "num_offers": len(prices),
        "num_merchants": len(merchants),
    }


async def _fetch_detail_page(
    url: str,
    browser_mgr: BrowserManager,
    timeout_seconds: int,
) -> list[ExtractedOffer]:
    """Fetch a single URL via Playwright and extract prices."""
    page = None
    try:
        page = await browser_mgr.get_page(url)

        # Navigate with timeout
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_seconds * 1000,
        )

        if not response:
            logger.debug("No response for %s", url[:80])
            return []

        if response.status >= 400:
            logger.debug("HTTP %d for %s", response.status, url[:80])
            return []

        # Brief human simulation to trigger lazy-loaded content
        await simulate_human_mouse(page)
        await asyncio.sleep(0.5)
        await simulate_human_scroll(page)
        await asyncio.sleep(0.5)

        # Extract prices using layered strategy
        offers = await extract_prices(page, url)
        return offers

    except asyncio.TimeoutError:
        logger.debug("Timeout fetching %s", url[:80])
        return []
    except Exception as e:
        logger.debug("Error fetching %s: %s", url[:80], e)
        return []
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def handle_search_prices(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
    searxng_client: SearXNGClient,
    serpapi_client: Any,
    search_config: PriceSearchConfig,
    cache: TTLCache,
) -> dict[str, Any]:
    """Main search pipeline: discovery -> ranking -> detail fetch -> aggregate."""
    query = arguments.get("query", "").strip()
    max_results = min(arguments.get("max_results", 10), 20)
    fetch_details = arguments.get("fetch_details", True)
    response_mode = arguments.get("response_mode", "full")

    if not query or len(query) < 2:
        return tool_error(ErrorCode.INVALID_PARAMETER, "query must be at least 2 characters")

    # Check cache
    cache_key = f"search:{query.lower()}:{fetch_details}:{max_results}"
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        logger.info("Cache hit for query: %s", query[:60])
        return cached_result

    start_time = time.monotonic()
    search_type = _detect_search_type(query)
    sources_queried: list[str] = []
    all_offers: list[dict[str, Any]] = []
    timing: dict[str, int] = {}

    # ── Phase 1: Discovery (SearXNG + optional SerpAPI in parallel) ──────

    discovery_tasks = []

    # SearXNG search
    async def searxng_search() -> list[SearchResult]:
        t0 = time.monotonic()
        results = await searxng_client.search(query, max_results=30)
        timing["searxng_ms"] = int((time.monotonic() - t0) * 1000)
        return results

    discovery_tasks.append(searxng_search())

    # Optional SerpAPI search
    async def serpapi_search() -> list[Any]:
        if serpapi_client is None or not serpapi_client.available:
            return []
        t0 = time.monotonic()
        results = await serpapi_client.search(query, max_results=15)
        timing["serpapi_ms"] = int((time.monotonic() - t0) * 1000)
        return results

    discovery_tasks.append(serpapi_search())

    searxng_results, serpapi_results = await asyncio.gather(*discovery_tasks)

    sources_queried.append("searxng")
    if serpapi_results:
        sources_queried.append("serpapi")

    # Extract inline offers from SearXNG results
    for result in searxng_results:
        if result.inline_price is not None:
            all_offers.append({
                "merchant": result.domain or "unknown",
                "price": result.inline_price,
                "shipping_cost": 0.0,
                "total_price": result.inline_price,
                "currency": "EUR",
                "url": result.url,
                "source": "searxng",
                "availability": "",
            })

    # Extract offers from SerpAPI results
    for result in serpapi_results:
        if result.price is not None:
            all_offers.append({
                "merchant": result.merchant or "unknown",
                "price": result.price,
                "shipping_cost": 0.0,
                "total_price": result.price,
                "currency": "EUR",
                "url": result.url,
                "source": "serpapi",
                "availability": "",
            })

    # ── Phase 2: URL ranking + deduplication ─────────────────────────────

    if fetch_details:
        # Collect candidate URLs from SearXNG results
        candidate_urls = [r.url for r in searxng_results if r.url]
        # Also add SerpAPI URLs
        candidate_urls.extend(r.url for r in serpapi_results if r.url)

        # Deduplicate and score
        candidate_urls = _deduplicate_urls(candidate_urls)
        scored = [(url, _score_url(url)) for url in candidate_urls]
        scored.sort(key=lambda x: x[1], reverse=True)
        top_urls = [url for url, _ in scored[:search_config.max_detail_urls]]

        # ── Phase 3: Playwright detail fetch (parallel) ──────────────────

        if top_urls:
            elapsed = time.monotonic() - start_time
            remaining = max(5, search_config.total_timeout_seconds - elapsed)
            per_url_timeout = min(
                search_config.detail_timeout_seconds,
                int(remaining / min(len(top_urls), search_config.concurrent_fetches)),
            )

            t0 = time.monotonic()
            semaphore = asyncio.Semaphore(search_config.concurrent_fetches)

            async def fetch_with_semaphore(url: str) -> list[ExtractedOffer]:
                async with semaphore:
                    return await asyncio.wait_for(
                        _fetch_detail_page(url, browser_mgr, per_url_timeout),
                        timeout=per_url_timeout + 2,
                    )

            tasks = [fetch_with_semaphore(url) for url in top_urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for url, result in zip(top_urls, results):
                if isinstance(result, Exception):
                    logger.debug("Detail fetch failed for %s: %s", url[:60], result)
                    continue
                for offer in result:
                    all_offers.append({
                        **offer.to_dict(),
                        "source": "playwright",
                    })

            timing["detail_fetch_ms"] = int((time.monotonic() - t0) * 1000)

    # ── Phase 4: Aggregate + format ──────────────────────────────────────

    # Deduplicate by merchant+price
    seen: set[str] = set()
    unique_offers: list[dict[str, Any]] = []
    for offer in all_offers:
        key = f"{offer['merchant'].lower()}:{offer['price']:.2f}"
        if key not in seen:
            seen.add(key)
            unique_offers.append(offer)

    # Sort by total price
    unique_offers.sort(key=lambda o: o.get("total_price", float("inf")))

    # Limit results
    unique_offers = unique_offers[:max_results]

    # Compute insights
    insights = _compute_insights(unique_offers) if len(unique_offers) >= 2 else {}

    total_ms = int((time.monotonic() - start_time) * 1000)
    timing["total_ms"] = total_ms

    result_data = {
        "query": query,
        "search_type": search_type,
        "offers": unique_offers,
        "insights": insights,
        "sources_queried": sources_queried,
        "timing": timing,
    }

    # Build MCP response
    if not unique_offers:
        response_text = (
            f"No prices found for: {query}\n\n"
            f"Searched: {', '.join(sources_queried)}\n"
            f"Time: {total_ms}ms"
        )
        result = {"content": [{"type": "text", "text": response_text}]}
    elif response_mode == "summary":
        summary = (
            f"Found {len(unique_offers)} offers for: {query}\n"
            f"Price range: {unique_offers[0]['price']:.2f} - {unique_offers[-1]['price']:.2f} EUR\n"
            f"Sources: {', '.join(sources_queried)} | Time: {total_ms}ms"
        )
        result = {"content": [{"type": "text", "text": summary}]}
    else:
        import json
        result = {
            "content": [{
                "type": "text",
                "text": json.dumps(result_data, ensure_ascii=False, indent=2),
            }],
        }

    # Cache the result
    cache.set(cache_key, result)
    return result
