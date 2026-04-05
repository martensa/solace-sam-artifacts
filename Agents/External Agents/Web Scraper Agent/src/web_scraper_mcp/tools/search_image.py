"""Tool: search_and_download_image -- searches for a product image and downloads it.

Uses CORS-free download via BrowserManager.download_resource().
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import random
from typing import Any
from urllib.parse import quote_plus, urlparse

from PIL import Image, UnidentifiedImageError
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser_manager import BrowserManager, simulate_human_mouse
from ..errors import ErrorCode, classify_playwright_error, tool_error
from ..image_extractor import extract_product_image_url
from ..response import build_response
from ..validation import validate_url
from .download_image import _process_image

logger = logging.getLogger("web-scraper-mcp.tools.search")

MAX_RESULTS_TO_TRY = 5
PARALLEL_BATCH_SIZE = 3

NEXT_STEP = "Product image found and downloaded. Available for embedding, display, or further analysis."


async def _google_image_search(page, query: str, preferred_sources: list[str] | None) -> list[str]:
    """Perform a Google Images search and return original image URLs."""
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=isch&hl=en"

    try:
        await page.goto(search_url, wait_until="networkidle", timeout=30000)
    except PlaywrightTimeout:
        logger.info("Google Images networkidle timeout, proceeding with available content")

    # Human-like behavior on search page
    await simulate_human_mouse(page)
    await asyncio.sleep(random.uniform(1.5, 3.0))

    # Extract original image URLs from Google Images
    results = await page.evaluate("""
        () => {
            const results = [];

            // Method 1: imgres links contain original URL
            const links = document.querySelectorAll('a[href*="/imgres"]');
            for (const link of links) {
                const href = link.href;
                const match = href.match(/imgurl=([^&]+)/);
                if (match) {
                    try {
                        results.push(decodeURIComponent(match[1]));
                    } catch {}
                }
            }

            // Method 2: data attributes on image thumbnails
            if (results.length === 0) {
                const imgs = document.querySelectorAll('img[data-src]');
                for (const img of imgs) {
                    const src = img.getAttribute('data-src') || img.src;
                    if (src && src.startsWith('http') && !src.includes('google')
                        && !src.includes('gstatic')) {
                        results.push(src);
                    }
                }
            }

            return results.slice(0, 20);
        }
    """)

    if not results:
        return []

    # Prioritize preferred sources if specified
    if preferred_sources:
        preferred = []
        others = []
        for url in results:
            domain = urlparse(url).netloc.lower()
            if any(src.lower() in domain for src in preferred_sources):
                preferred.append(url)
            else:
                others.append(url)
        return preferred + others

    return results


async def _bing_image_search(page, query: str) -> list[str]:
    """Fallback: search Bing Images."""
    bing_url = f"https://www.bing.com/images/search?q={quote_plus(query)}&form=HDRSC3"

    try:
        await page.goto(bing_url, wait_until="networkidle", timeout=30000)
    except PlaywrightTimeout:
        logger.info("Bing Images networkidle timeout, proceeding")

    await asyncio.sleep(random.uniform(1.5, 2.5))

    return await page.evaluate("""
        () => {
            const results = [];
            // Bing stores original URLs in data-m JSON attribute
            const items = document.querySelectorAll('a.iusc');
            for (const item of items) {
                try {
                    const m = JSON.parse(item.getAttribute('m') || '{}');
                    if (m.murl) results.push(m.murl);
                } catch {}
            }
            // Fallback: thumbnail images
            if (results.length === 0) {
                const imgs = document.querySelectorAll('.mimg');
                for (const img of imgs) {
                    const src = img.src || img.dataset.src;
                    if (src && src.startsWith('http')) {
                        results.push(src);
                    }
                }
            }
            return results.slice(0, 10);
        }
    """)


async def _try_download_from_url(
    url: str,
    browser_mgr: BrowserManager,
    output_format: str,
    response_mode: str,
    max_width: int = 1200,
) -> dict | None:
    """Try to download an image from a URL using CORS-free method."""
    page = None
    try:
        page = await browser_mgr.get_page(url)
        page.set_default_timeout(15000)

        # Navigate to page to establish session
        try:
            await page.goto(url, wait_until="load", timeout=20000)
        except PlaywrightTimeout:
            pass  # proceed with what we have

        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Try to find image URL on the page (if it's a product page)
        image_url = await extract_product_image_url(page)
        if not image_url:
            image_url = url  # Try the URL directly

        # Download via CORS-free method
        result = await browser_mgr.download_resource(page, image_url)
        if not result or not result.data:
            return None

        # Validate it's a real image of reasonable size
        try:
            img = Image.open(io.BytesIO(result.data))
            if img.width < 80 or img.height < 80:
                return None
        except (UnidentifiedImageError, OSError):
            return None

        processed, mime = _process_image(result.data, output_format, max_width)
        encoded = base64.b64encode(processed).decode("utf-8")
        img = Image.open(io.BytesIO(processed))

        metadata = (
            f"| Field | Value |\n"
            f"|-------|-------|\n"
            f"| Source URL | {image_url} |\n"
            f"| Format | {output_format.upper()} |\n"
            f"| Dimensions | {img.width}x{img.height} |\n"
            f"| Size | {len(processed):,} bytes |\n"
        )

        return build_response(
            response_mode=response_mode,
            metadata=metadata,
            next_step=NEXT_STEP,
            payload_items=[{"type": "image", "data": encoded, "mimeType": mime}],
        )

    except (PlaywrightError, PlaywrightTimeout, OSError) as e:
        logger.debug("Failed to download from %s: %s", url[:80], e)
        return None
    finally:
        if page:
            try:
                await page.close()
            except PlaywrightError:
                pass


async def handle_search_image(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
) -> dict:
    """Search for a product image and download the best match."""
    query = arguments["search_query"]
    preferred_sources = arguments.get("preferred_sources")
    output_format = arguments.get("output_format", "png")
    response_mode = arguments.get("response_mode", "full")

    page = None
    try:
        # Step 1: Search Google Images
        page = await browser_mgr.get_page("https://www.google.com")
        page.set_default_timeout(30000)

        image_urls = await _google_image_search(page, query, preferred_sources)
        await page.close()
        page = None

        # Fallback: try Bing if Google yields nothing
        if not image_urls:
            logger.info("Google Images returned no results, trying Bing")
            page = await browser_mgr.get_page("https://www.bing.com")
            image_urls = await _bing_image_search(page, query)
            await page.close()
            page = None

        if not image_urls:
            return tool_error(
                ErrorCode.NO_SEARCH_RESULTS,
                f"No image results found for '{query}'",
            )

        # Filter SSRF-blocked URLs upfront
        safe_urls = []
        for i, url in enumerate(image_urls[:MAX_RESULTS_TO_TRY]):
            ssrf_err = validate_url(url)
            if ssrf_err:
                logger.info("Skipping result %d (SSRF blocked): %s", i + 1, url[:80])
                continue
            safe_urls.append(url)

        logger.info("Found %d candidate URLs for '%s', trying %d safe URLs",
                     len(image_urls), query, len(safe_urls))

        # Step 2: Try downloading in parallel batches for speed
        for batch_start in range(0, len(safe_urls), PARALLEL_BATCH_SIZE):
            batch = safe_urls[batch_start:batch_start + PARALLEL_BATCH_SIZE]
            logger.info("Downloading batch %d-%d of %d",
                        batch_start + 1, batch_start + len(batch), len(safe_urls))

            tasks = [
                _try_download_from_url(url, browser_mgr, output_format, response_mode)
                for url in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, dict) and not result.get("isError"):
                    return result

        return tool_error(
            ErrorCode.DOWNLOAD_FAILED,
            f"Found {len(image_urls)} results for '{query}' but could not download any "
            "valid image. Try providing a direct product page URL with download_product_image.",
        )

    except (PlaywrightError, PlaywrightTimeout) as e:
        logger.exception("Search failed for '%s'", query)
        error_code = classify_playwright_error(str(e))
        return tool_error(error_code, f"Search failed for '{query}': {e}")
    finally:
        if page:
            try:
                await page.close()
            except PlaywrightError:
                pass
