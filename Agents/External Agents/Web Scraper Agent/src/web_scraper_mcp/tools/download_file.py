"""Tool: download_file -- downloads any file type from a URL (CORS-free).

Handles images, PDFs, videos, documents, spreadsheets, archives, and any other
media type. Uses the BrowserManager's API-level HTTP client which bypasses
CORS, CSP, and mixed-content restrictions.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser_manager import BrowserManager, DownloadResult, _extract_filename, _guess_mime, _parse_content_type
from ..errors import ErrorCode, classify_playwright_error, tool_error
from ..response import build_response

logger = logging.getLogger("web-scraper-mcp.tools.download-file")

MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]

# MIME types that can be displayed as text in the MCP response
TEXT_MIMES = {
    "text/plain", "text/html", "text/css", "text/csv",
    "application/json", "application/xml", "text/xml",
    "application/javascript", "text/javascript",
}

# Maximum file size: 50 MB (base64 expands ~33%, stay within reasonable MCP message size)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Next step hints by content category
NEXT_STEP_IMAGE = "Image file downloaded. Available for display or further processing."
NEXT_STEP_TEXT = "Text content downloaded. Available for analysis or further processing."
NEXT_STEP_BINARY = (
    "File downloaded. Document conversion agents can extract text from "
    "PDF, DOCX, XLSX, and similar formats."
)


def _format_size(size: int) -> str:
    """Format byte size for display."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


async def handle_download_file(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
) -> dict:
    """Download any file from a URL.

    Navigates to the page first (to establish cookies/session), then downloads
    the target resource via the CORS-free context.request API.
    """
    url = arguments["url"]
    navigate_first = arguments.get("navigate_first", True)
    response_mode = arguments.get("response_mode", "full")

    last_error = None
    for attempt in range(MAX_RETRIES):
        page = None
        try:
            page = await browser_mgr.get_page(url)
            page.set_default_timeout(browser_mgr.config.default_timeout_ms)

            if navigate_first:
                # Navigate to the page to establish session/cookies
                try:
                    response = await page.goto(url, wait_until="load", timeout=browser_mgr.config.navigation_timeout_ms)
                except PlaywrightTimeout:
                    logger.info("Navigation timeout for %s, attempting direct download", url)
                    response = None

                # If navigation returned the file directly (e.g. direct PDF link),
                # try to get the response body
                if response and response.ok:
                    content_type = response.headers.get("content-type", "")
                    # If it's not HTML, the navigation itself downloaded the file
                    if content_type and not content_type.startswith("text/html"):
                        try:
                            body = await response.body()
                            if body and len(body) > 0:
                                return _build_result(
                                    body, content_type, url,
                                    response.headers, response_mode,
                                )
                        except PlaywrightError:
                            pass  # body not available, try download_resource

                # Human-like delay
                if browser_mgr.config.stealth_enabled:
                    await asyncio.sleep(browser_mgr.config.random_delay())

            # Download via CORS-free method
            result = await browser_mgr.download_resource(page, url)
            if not result or not result.data:
                last_error = f"Failed to download resource from {url}"
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt] + random.uniform(0, 2))
                    continue
                return tool_error(ErrorCode.DOWNLOAD_FAILED, last_error)

            # Size check
            if len(result.data) > MAX_FILE_SIZE:
                return tool_error(
                    ErrorCode.FILE_TOO_LARGE,
                    f"File from {url} is too large ({result.size_str}). "
                    f"Maximum supported size is {MAX_FILE_SIZE // (1024*1024)} MB.",
                )

            return _build_download_result(result, url, response_mode)

        except (PlaywrightError, PlaywrightTimeout, OSError) as e:
            last_error = str(e)
            logger.warning("Attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, last_error)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt] + random.uniform(0, 2))
                continue
            break
        finally:
            if page:
                try:
                    await page.close()
                except PlaywrightError:
                    pass

    error_code = classify_playwright_error(last_error or "")
    msg = f"Failed to download {url} after {MAX_RETRIES} attempts. Last error: {last_error}"
    return tool_error(error_code, msg)


def _build_result(
    body: bytes,
    content_type: str,
    url: str,
    headers: dict,
    response_mode: str,
) -> dict:
    """Build MCP result from raw response."""
    mime = _parse_content_type(content_type) or _guess_mime(url)
    disposition = headers.get("content-disposition", "")
    filename = _extract_filename(url, disposition)

    return _build_download_result_inner(body, mime, filename, len(body), response_mode)


def _build_download_result(
    result: DownloadResult,
    url: str,
    response_mode: str,
) -> dict:
    """Build MCP tool result from a DownloadResult."""
    return _build_download_result_inner(
        result.data, result.mime_type, result.filename,
        len(result.data), response_mode,
    )


def _build_download_result_inner(
    data: bytes,
    mime: str,
    filename: str,
    size: int,
    response_mode: str,
) -> dict:
    """Build the final MCP response with appropriate content type."""
    size_str = _format_size(size)

    metadata = (
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| Filename | {filename} |\n"
        f"| MIME Type | {mime} |\n"
        f"| Size | {size_str} |\n"
    )

    # Determine next_step hint and payload based on content type
    if mime.startswith("image/"):
        encoded = base64.b64encode(data).decode("utf-8")
        return build_response(
            response_mode=response_mode,
            metadata=metadata,
            next_step=NEXT_STEP_IMAGE,
            payload_items=[{"type": "image", "data": encoded, "mimeType": mime}],
        )

    if mime in TEXT_MIMES:
        try:
            text_content = data.decode("utf-8")
            return build_response(
                response_mode=response_mode,
                metadata=metadata,
                next_step=NEXT_STEP_TEXT,
                payload_items=[{"type": "text", "text": text_content}],
            )
        except UnicodeDecodeError:
            pass  # Fall through to binary return

    # For all other types (PDF, video, Excel, etc.), return as base64 resource
    encoded = base64.b64encode(data).decode("utf-8")
    return build_response(
        response_mode=response_mode,
        metadata=metadata,
        next_step=NEXT_STEP_BINARY,
        payload_items=[{
            "type": "resource",
            "resource": {
                "uri": f"data:{mime};base64,{encoded}",
                "mimeType": mime,
                "text": f"[Binary file: {filename}]",
            },
        }],
    )
