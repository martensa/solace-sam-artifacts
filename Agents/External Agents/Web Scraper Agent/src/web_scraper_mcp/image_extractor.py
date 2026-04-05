"""Intelligent product image extraction from web pages."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .config import IGNORED_IMAGE_DOMAINS, PRODUCT_IMAGE_SELECTORS

logger = logging.getLogger("web-scraper-mcp.image-extractor")


async def extract_product_image_url(page: Page, custom_selector: str | None = None) -> str | None:
    """Extract the best product image URL from a page.

    Strategy (priority order):
    1. Custom CSS selector (if provided)
    2. Open Graph image meta tag (og:image)
    3. Schema.org Product image (JSON-LD)
    4. Common product CSS selectors
    5. Largest visible image on the page (>= 400x400 preferred)
    """
    base_url = page.url

    # 1. Custom selector
    if custom_selector:
        url = await _url_from_selector(page, custom_selector, base_url)
        if url:
            logger.info("Found image via custom selector: %s", url[:120])
            return url

    # 2. Open Graph
    url = await _og_image(page, base_url)
    if url:
        logger.info("Found image via og:image: %s", url[:120])
        return url

    # 3. Schema.org JSON-LD
    url = await _schema_org_image(page, base_url)
    if url:
        logger.info("Found image via Schema.org JSON-LD: %s", url[:120])
        return url

    # 4. Product CSS selectors
    for selector in PRODUCT_IMAGE_SELECTORS:
        url = await _url_from_selector(page, selector, base_url)
        if url:
            logger.info("Found image via product selector '%s': %s", selector, url[:120])
            return url

    # 5. Largest visible image
    url = await _largest_visible_image(page, base_url)
    if url:
        logger.info("Found image via largest-visible heuristic: %s", url[:120])
        return url

    logger.warning("No product image found on %s", base_url)
    return None


async def _url_from_selector(page: Page, selector: str, base_url: str) -> str | None:
    """Get image src from the first matching element."""
    try:
        el = await page.query_selector(selector)
        if el is None:
            return None
        # Try multiple source attributes (src, data-src, srcset first entry)
        for attr in ("src", "data-src", "data-lazy-src", "data-original"):
            val = await el.get_attribute(attr)
            if val and _is_valid_image_url(val):
                return urljoin(base_url, val)
        # Try srcset (pick first entry)
        srcset = await el.get_attribute("srcset")
        if srcset:
            first = srcset.split(",")[0].strip().split(" ")[0]
            if _is_valid_image_url(first):
                return urljoin(base_url, first)
    except PlaywrightError:
        pass
    return None


async def _og_image(page: Page, base_url: str) -> str | None:
    """Extract og:image meta tag."""
    try:
        el = await page.query_selector('meta[property="og:image"]')
        if el:
            content = await el.get_attribute("content")
            if content and _is_valid_image_url(content):
                return urljoin(base_url, content)
    except PlaywrightError:
        pass
    return None


async def _schema_org_image(page: Page, base_url: str) -> str | None:
    """Extract image from Schema.org Product JSON-LD."""
    try:
        scripts = await page.query_selector_all('script[type="application/ld+json"]')
        for script in scripts:
            text = await script.inner_text()
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue

            # Handle both single object and @graph arrays
            items: list = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and "@graph" in data:
                items = data["@graph"]

            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    item_type = " ".join(item_type)
                if "Product" not in item_type:
                    continue

                img = item.get("image")
                if isinstance(img, str) and _is_valid_image_url(img):
                    return urljoin(base_url, img)
                if isinstance(img, list) and img:
                    first = img[0]
                    if isinstance(first, str) and _is_valid_image_url(first):
                        return urljoin(base_url, first)
                    if isinstance(first, dict):
                        url = first.get("url") or first.get("contentUrl")
                        if url and _is_valid_image_url(url):
                            return urljoin(base_url, url)
    except (PlaywrightError, json.JSONDecodeError):
        pass
    return None


async def _largest_visible_image(page: Page, base_url: str) -> str | None:
    """Find the largest visible image on the page."""
    try:
        images = await page.evaluate("""
            () => {
                const imgs = Array.from(document.querySelectorAll('img'));
                return imgs
                    .filter(img => {
                        const rect = img.getBoundingClientRect();
                        const visible = rect.width > 0 && rect.height > 0;
                        const bigEnough = (img.naturalWidth >= 100 && img.naturalHeight >= 100)
                            || (rect.width >= 100 && rect.height >= 100);
                        return visible && bigEnough;
                    })
                    .map(img => ({
                        src: img.src || img.dataset.src || img.dataset.lazySrc || '',
                        naturalWidth: img.naturalWidth,
                        naturalHeight: img.naturalHeight,
                        area: img.naturalWidth * img.naturalHeight,
                    }))
                    .filter(img => img.src && !img.src.startsWith('data:'))
                    .sort((a, b) => b.area - a.area);
            }
        """)

        # First pass: prefer images >= 400x400
        for img in images:
            src = img.get("src", "")
            if not src or not _is_valid_image_url(src):
                continue
            if img.get("naturalWidth", 0) >= 400 and img.get("naturalHeight", 0) >= 400:
                return urljoin(base_url, src)

        # Second pass: largest available image
        for img in images:
            src = img.get("src", "")
            if src and _is_valid_image_url(src):
                return urljoin(base_url, src)

    except PlaywrightError:
        pass
    return None


def _is_valid_image_url(url: str) -> bool:
    """Check that a URL looks like a real image and not a tracker/placeholder."""
    if not url:
        return False

    if url.startswith("data:"):
        return False

    # Skip tracking pixels
    lower = url.lower()
    for domain in IGNORED_IMAGE_DOMAINS:
        if domain in lower:
            return False

    # Skip SVG placeholders
    if lower.endswith(".svg") or "placeholder" in lower or "blank.gif" in lower:
        return False

    # Skip 1x1 pixel indicators
    if re.search(r'/1x1[./]', url) or re.search(r'[?&]w=1[&$]', url):
        return False

    return True
