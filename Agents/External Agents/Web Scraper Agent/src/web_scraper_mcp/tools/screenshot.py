"""Tool: screenshot_webpage -- takes a screenshot of a page or specific element."""

from __future__ import annotations

import asyncio
import base64
import logging
import random
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser_manager import BrowserManager, simulate_human_scroll
from ..errors import ErrorCode, classify_playwright_error, tool_error
from ..response import build_response

logger = logging.getLogger("web-scraper-mcp.tools.screenshot")

MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]

NEXT_STEP = "Screenshot captured. Available for visual analysis, embedding, or display."


async def handle_screenshot(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
) -> dict:
    """Take a screenshot of a webpage or a specific element."""
    url = arguments["url"]
    element_selector = arguments.get("element_selector")
    viewport_width = arguments.get("viewport_width", 1920)
    viewport_height = arguments.get("viewport_height", 1080)
    full_page = arguments.get("full_page", False)
    response_mode = arguments.get("response_mode", "full")

    last_error = None
    for attempt in range(MAX_RETRIES):
        page = None
        try:
            page = await browser_mgr.get_page(url)
            page.set_default_timeout(browser_mgr.config.default_timeout_ms)

            # Set viewport
            await page.set_viewport_size({"width": viewport_width, "height": viewport_height})

            # Navigate with networkidle fallback
            try:
                await page.goto(url, wait_until="networkidle", timeout=browser_mgr.config.navigation_timeout_ms)
            except PlaywrightTimeout:
                logger.info("networkidle timeout for screenshot, proceeding with available content")

            # Human-like behavior
            if browser_mgr.config.stealth_enabled:
                await simulate_human_scroll(page)
            await asyncio.sleep(browser_mgr.config.random_delay())

            # Take screenshot
            if element_selector:
                try:
                    el = await page.wait_for_selector(element_selector, timeout=10000)
                except PlaywrightTimeout:
                    return tool_error(
                        ErrorCode.ELEMENT_NOT_FOUND,
                        f"Element '{element_selector}' not found on {url}",
                    )
                screenshot_bytes = await el.screenshot(type="png")
            else:
                screenshot_bytes = await page.screenshot(type="png", full_page=full_page)

            encoded = base64.b64encode(screenshot_bytes).decode("utf-8")

            capture_type = "element" if element_selector else ("full page" if full_page else "viewport")
            metadata = (
                f"| Field | Value |\n"
                f"|-------|-------|\n"
                f"| URL | {url} |\n"
                f"| Capture | {capture_type} |\n"
                f"| Viewport | {viewport_width}x{viewport_height} |\n"
                f"| Size | {len(screenshot_bytes):,} bytes |\n"
            )
            if element_selector:
                metadata = metadata.rstrip("\n") + f"\n| Selector | `{element_selector}` |\n"

            return build_response(
                response_mode=response_mode,
                metadata=metadata,
                next_step=NEXT_STEP,
                payload_items=[{"type": "image", "data": encoded, "mimeType": "image/png"}],
            )

        except (PlaywrightError, PlaywrightTimeout, OSError) as e:
            last_error = str(e)
            logger.warning("Screenshot attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, last_error)
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
    msg = f"Failed to screenshot {url} after {MAX_RETRIES} attempts. Last error: {last_error}"
    return tool_error(error_code, msg)
