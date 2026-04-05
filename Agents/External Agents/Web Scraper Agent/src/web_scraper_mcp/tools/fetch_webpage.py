"""Tool: fetch_protected_webpage -- fetches fully rendered HTML via headless browser."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Response
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser_manager import BrowserManager, simulate_human_mouse, simulate_human_scroll
from ..errors import ErrorCode, classify_playwright_error, tool_error
from ..response import build_response

logger = logging.getLogger("web-scraper-mcp.tools.fetch")

MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]

# Error messages that indicate a retryable transient failure
RETRYABLE_PATTERNS = [
    "net::ERR_CONNECTION_RESET",
    "net::ERR_CONNECTION_TIMED_OUT",
    "net::ERR_ABORTED",
    "Navigation failed because page crashed",
]

# Error messages that indicate we should not retry (human intervention needed)
NON_RETRYABLE_PATTERNS = ["captcha", "login required"]

# Maximum HTML size to return (characters)
MAX_HTML_LENGTH = 200_000

NEXT_STEP = (
    "HTML content retrieved. Can be parsed for links, data extraction, "
    "or further navigation by other agents."
)


def _is_retryable(error_msg: str) -> bool:
    lower = error_msg.lower()
    if any(p.lower() in lower for p in NON_RETRYABLE_PATTERNS):
        return False
    return any(p.lower() in lower for p in RETRYABLE_PATTERNS) or "timeout" in lower


async def _navigate_with_fallback(page, url: str, timeout_ms: int) -> Response | None:
    """Navigate to URL. Falls back from networkidle to load if network never settles."""
    try:
        return await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        logger.info("networkidle timeout for %s, falling back to domcontentloaded", url)
        # Page may already be usable -- check if we have content
        content = await page.content()
        if len(content) < 200:
            # Truly empty -- try a fresh navigation with lower wait
            return await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return None  # Page has content despite timeout


async def _handle_bot_challenge(page, timeout_ms: int) -> None:
    """Wait for Cloudflare/Akamai JS challenges to resolve."""
    content = await page.content()
    lower = content.lower()

    is_challenge = (
        "ray id" in lower
        or "cf-challenge" in lower
        or "checking your browser" in lower
        or "just a moment" in lower
        or ("akamai" in lower and "access denied" in lower)
    )

    if is_challenge:
        logger.info("Bot challenge detected, waiting for JS resolution...")
        # Simulate human presence while waiting
        await simulate_human_mouse(page)
        await asyncio.sleep(random.uniform(4, 7))
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeout:
            pass


async def handle_fetch_webpage(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
) -> dict:
    """Fetch a URL with a headless browser and return rendered HTML."""
    url = arguments["url"]
    wait_for = arguments.get("wait_for_selector")
    timeout_s = arguments.get("timeout_seconds", 30)
    timeout_ms = timeout_s * 1000
    response_mode = arguments.get("response_mode", "full")

    last_error = None
    for attempt in range(MAX_RETRIES):
        page = None
        try:
            page = await browser_mgr.get_page(url)
            page.set_default_timeout(timeout_ms)

            # Navigate with networkidle fallback
            response = await _navigate_with_fallback(page, url, timeout_ms)

            # Check HTTP status (404, 410, 5xx are hard failures)
            if response and response.status >= 400:
                status = response.status
                if status in (404, 410):
                    code = ErrorCode.HTTP_404 if status == 404 else ErrorCode.HTTP_410
                    return tool_error(code, f"HTTP {status}: Page not found at {url}")
                if status >= 500:
                    last_error = f"HTTP {status} server error for {url}"
                    if attempt < MAX_RETRIES - 1:
                        jitter = random.uniform(0, 2)
                        await asyncio.sleep(RETRY_DELAYS[attempt] + jitter)
                        continue

            # Handle bot challenge pages (403 with JS challenge)
            await _handle_bot_challenge(page, timeout_ms)

            # Human-like behavior before content extraction
            if browser_mgr.config.stealth_enabled:
                await simulate_human_scroll(page)

            # Wait for optional selector
            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=min(timeout_ms, 15000))
                except PlaywrightTimeout:
                    logger.warning("Selector '%s' not found within timeout", wait_for)

            # Small delay for remaining JS execution
            await asyncio.sleep(browser_mgr.config.random_delay())

            html = await page.content()

            # Truncate if too large
            truncated = False
            if len(html) > MAX_HTML_LENGTH:
                html = html[:MAX_HTML_LENGTH]
                truncated = True

            truncated_note = " [TRUNCATED]" if truncated else ""
            logger.info("Fetched %s (%d chars%s)", url, len(html), truncated_note)

            status_code = response.status if response else "N/A"
            metadata = (
                f"| Field | Value |\n"
                f"|-------|-------|\n"
                f"| URL | {url} |\n"
                f"| HTTP Status | {status_code} |\n"
                f"| Content Length | {len(html):,} chars{truncated_note} |\n"
            )

            return build_response(
                response_mode=response_mode,
                metadata=metadata,
                next_step=NEXT_STEP,
                payload_items=[{"type": "text", "text": html}],
            )

        except (PlaywrightError, PlaywrightTimeout, OSError) as e:
            last_error = str(e)
            logger.warning("Attempt %d/%d failed for %s: %s", attempt + 1, MAX_RETRIES, url, last_error)

            if not _is_retryable(last_error):
                break
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
    msg = f"Failed to fetch {url} after {MAX_RETRIES} attempts. Last error: {last_error}"
    return tool_error(error_code, msg)
