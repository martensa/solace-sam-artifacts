"""Tool: download_product_image -- extracts and downloads the main product image.

Uses the BrowserManager's CORS-free download_resource() method which operates
at the API/CDP level rather than in-page fetch(), bypassing CORS, CSP, and
mixed-content restrictions.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import random
from typing import Any

from PIL import Image, UnidentifiedImageError
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser_manager import BrowserManager, simulate_human_mouse
from ..errors import ErrorCode, classify_playwright_error, tool_error
from ..image_extractor import extract_product_image_url
from ..response import build_response

logger = logging.getLogger("web-scraper-mcp.tools.download")

MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]

FORMAT_MAP = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
MIME_MAP = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

NEXT_STEP = "Product image downloaded. Available for embedding, display, or further analysis."


def _process_image(raw_bytes: bytes, output_format: str, max_width: int, quality: int = 90) -> tuple[bytes, str]:
    """Resize, convert, and strip metadata. Returns (bytes, mime_type).

    Raises UnidentifiedImageError if input is not a valid image.
    """
    pil_format = FORMAT_MAP.get(output_format.lower(), "PNG")
    mime = MIME_MAP.get(pil_format, "image/png")

    img = Image.open(io.BytesIO(raw_bytes))

    # Convert palette/RGBA to RGB for JPEG
    if pil_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
        img = background

    # Resize if wider than max_width, preserving aspect ratio
    if img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)

    # Save without EXIF metadata
    buf = io.BytesIO()
    save_kwargs: dict[str, Any] = {"format": pil_format}
    if pil_format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = quality
    img.save(buf, **save_kwargs)
    return buf.getvalue(), mime


async def handle_download_image(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
) -> dict:
    """Download the main product image from a page or direct URL."""
    url = arguments["url"]
    custom_selector = arguments.get("image_selector")
    output_format = arguments.get("output_format", "png")
    max_width = arguments.get("max_width", 1200)
    response_mode = arguments.get("response_mode", "full")

    last_error = None
    for attempt in range(MAX_RETRIES):
        page = None
        try:
            page = await browser_mgr.get_page(url)
            page.set_default_timeout(browser_mgr.config.default_timeout_ms)

            # Navigate to the page
            try:
                nav_timeout = browser_mgr.config.navigation_timeout_ms
                response = await page.goto(url, wait_until="networkidle", timeout=nav_timeout)
            except PlaywrightTimeout:
                logger.info("networkidle timeout, proceeding with available content")
                response = None

            # Check HTTP status
            if response and response.status >= 400:
                status = response.status
                # Might be a bot challenge -- wait and check
                if status == 403:
                    await asyncio.sleep(random.uniform(4, 7))
                    response = None  # proceed anyway, might have resolved
                else:
                    last_error = f"HTTP {status} for {url}"
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAYS[attempt] + random.uniform(0, 2))
                        continue
                    return {
                        "content": [{"type": "text", "text": last_error}],
                        "isError": True,
                    }

            # Human-like behavior before extraction
            if browser_mgr.config.stealth_enabled:
                await simulate_human_mouse(page)
            await asyncio.sleep(browser_mgr.config.random_delay())

            # Extract image URL from the page
            image_url = await extract_product_image_url(page, custom_selector)
            if not image_url:
                return tool_error(
                    ErrorCode.IMAGE_NOT_FOUND,
                    f"No product image found on {url}. "
                    "Try screenshot_webpage as fallback, or provide a CSS selector via image_selector.",
                )

            # Download via CORS-free method
            result = await browser_mgr.download_resource(page, image_url)
            if not result or not result.data:
                last_error = f"Failed to download image from {image_url}"
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt] + random.uniform(0, 2))
                    continue
                return tool_error(ErrorCode.DOWNLOAD_FAILED, last_error)

            # Process image (validate + resize + convert)
            try:
                processed, mime = _process_image(result.data, output_format, max_width)
            except (UnidentifiedImageError, OSError) as e:
                last_error = f"Downloaded data from {image_url} is not a valid image: {e}"
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                return tool_error(ErrorCode.INVALID_IMAGE_DATA, last_error)

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
            last_error = str(e)
            logger.warning("Attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, last_error)
            if attempt < MAX_RETRIES - 1:
                jitter = random.uniform(0, 2)
                await asyncio.sleep(RETRY_DELAYS[attempt] + jitter)
                continue
            break
        finally:
            if page:
                try:
                    await page.close()
                except PlaywrightError:
                    pass

    error_code = classify_playwright_error(last_error or "")
    msg = f"Failed to download image from {url} after {MAX_RETRIES} attempts. Last error: {last_error}"
    return tool_error(error_code, msg)
