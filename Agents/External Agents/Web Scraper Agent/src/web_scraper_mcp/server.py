"""MCP Server entry point for the Web Scraper Agent.

Implements MCP JSON-RPC 2.0 over stdio using newline-delimited JSON framing
as per the MCP specification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import uuid
from typing import Any

from .browser_manager import BrowserManager
from .config import MCP_LOG_FILE, MCP_LOG_LEVEL, MCP_MAX_RESPONSE_CHARS, BrowserConfig
from .errors import ErrorCode, classify_playwright_error, tool_error
from .response import validate_response_mode
from .tools.download_file import handle_download_file
from .tools.download_image import handle_download_image
from .tools.fetch_webpage import handle_fetch_webpage
from .tools.screenshot import handle_screenshot
from .tools.search_image import handle_search_image
from .validation import validate_output_format, validate_timeout, validate_url, validate_viewport

# -- Logging -------------------------------------------------------------------

log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
if MCP_LOG_FILE:
    log_handlers.append(logging.FileHandler(MCP_LOG_FILE))

logging.basicConfig(
    level=getattr(logging, MCP_LOG_LEVEL.upper(), logging.INFO),
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    handlers=log_handlers,
)
logger = logging.getLogger("web-scraper-mcp")

# -- Shared response_mode property added to every tool schema -----------------

RESPONSE_MODE_PROPERTY: dict[str, Any] = {
    "type": "string",
    "enum": ["full", "summary"],
    "description": (
        "Controls response detail. "
        "full: inline payload (default). "
        "summary: metadata only, no payload."
    ),
    "default": "full",
}

# -- Tool definitions exposed via MCP -----------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "fetch_protected_webpage",
        "description": (
            "Fetch a bot-protected webpage using a headless browser. Returns fully "
            "rendered HTML after JavaScript execution. Bypasses Cloudflare, Akamai, "
            "and similar bot protection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch (http or https)",
                },
                "wait_for_selector": {
                    "type": "string",
                    "description": "CSS selector to wait for before extracting content",
                    "maxLength": 500,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Max wait time in seconds (5-120, default: 30)",
                    "default": 30,
                    "minimum": 5,
                    "maximum": 120,
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["url"],
        },
    },
    {
        "name": "download_product_image",
        "description": (
            "Download the main product image from a page URL. Extracts the image "
            "using og:image, Schema.org, CSS selectors, and size heuristics. "
            "Returns base64-encoded image data."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Product page URL or direct image URL (http or https)",
                },
                "image_selector": {
                    "type": "string",
                    "description": "CSS selector for the image element",
                    "maxLength": 500,
                },
                "output_format": {
                    "type": "string",
                    "enum": ["png", "jpg", "webp"],
                    "description": "Output image format (default: png)",
                    "default": "png",
                },
                "max_width": {
                    "type": "integer",
                    "description": "Max image width in pixels (50-4096, default: 1200)",
                    "default": 1200,
                    "minimum": 50,
                    "maximum": 4096,
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["url"],
        },
    },
    {
        "name": "search_and_download_image",
        "description": (
            "Search for a product image by name or identifier and download the best "
            "match from Google or Bing Images. Use when no direct URL is available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "search_query": {
                    "type": "string",
                    "description": "Product name, article number, or search term",
                    "minLength": 2,
                    "maxLength": 500,
                },
                "preferred_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Preferred source domains for image selection",
                    "maxItems": 10,
                },
                "output_format": {
                    "type": "string",
                    "enum": ["png", "jpg", "webp"],
                    "description": "Output image format (default: png)",
                    "default": "png",
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["search_query"],
        },
    },
    {
        "name": "download_file",
        "description": (
            "Download any file from a URL. Supports PDFs, Excel, Word, videos, "
            "images, archives, and all other file types. Bypasses bot protection, "
            "CORS, and CDN restrictions. Max 50 MB. Returns base64 with MIME type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Direct URL to the file (http or https)",
                },
                "navigate_first": {
                    "type": "boolean",
                    "description": (
                        "Navigate to URL first to establish session and cookies. "
                        "True (default) for protected sites, false for direct links."
                    ),
                    "default": True,
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["url"],
        },
    },
    {
        "name": "screenshot_webpage",
        "description": (
            "Take a screenshot of a webpage or a specific element. Supports full-page "
            "and element-specific captures via CSS selector. Use as fallback when no "
            "individual resource can be extracted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to screenshot (http or https)",
                },
                "element_selector": {
                    "type": "string",
                    "description": "CSS selector for element-specific screenshot",
                    "maxLength": 500,
                },
                "viewport_width": {
                    "type": "integer",
                    "description": "Viewport width in pixels (320-3840, default: 1920)",
                    "default": 1920,
                    "minimum": 320,
                    "maximum": 3840,
                },
                "viewport_height": {
                    "type": "integer",
                    "description": "Viewport height in pixels (240-2160, default: 1080)",
                    "default": 1080,
                    "minimum": 240,
                    "maximum": 2160,
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (default: false)",
                    "default": False,
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["url"],
        },
    },
]


# -- MCP JSON-RPC Protocol (newline-delimited JSON over stdio) ----------------

async def read_message(reader: asyncio.StreamReader) -> dict | None:
    """Read a single JSON-RPC message (one line) from stdin."""
    while True:
        line = await reader.readline()
        if not line:
            return None  # EOF
        line = line.strip()
        if not line:
            continue  # skip blank lines
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("Malformed JSON-RPC message, skipping: %s", e)
            continue


def write_message(msg: dict) -> None:
    """Write a single JSON-RPC message (one line) to stdout."""
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def error_response(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _truncate_response(result: dict) -> dict:
    """Truncate text content to MCP_MAX_RESPONSE_CHARS if needed."""
    if MCP_MAX_RESPONSE_CHARS <= 0:
        return result
    content = result.get("content", [])
    total_chars = 0
    for item in content:
        if item.get("type") == "text":
            total_chars += len(item.get("text", ""))
    if total_chars <= MCP_MAX_RESPONSE_CHARS:
        return result
    # Truncate text items proportionally
    budget = MCP_MAX_RESPONSE_CHARS
    new_content = []
    for item in content:
        if item.get("type") == "text":
            text = item["text"]
            if len(text) > budget:
                text = text[:budget] + "\n\n[TRUNCATED -- response exceeded character limit]"
                budget = 0
            else:
                budget -= len(text)
            new_content.append({"type": "text", "text": text})
        else:
            new_content.append(item)
    return {**result, "content": new_content}


# -- Input validation wrappers -------------------------------------------------

def _validate_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Validate tool arguments. Returns error message or None if valid."""
    # Validate response_mode (shared across all tools)
    mode = arguments.get("response_mode")
    if mode is not None:
        err = validate_response_mode(mode)
        if err:
            return err

    if tool_name in ("fetch_protected_webpage", "download_product_image", "screenshot_webpage", "download_file"):
        url = arguments.get("url")
        if not url:
            return "Missing required parameter: url"
        err = validate_url(url)
        if err:
            return err

    if tool_name == "fetch_protected_webpage":
        timeout = arguments.get("timeout_seconds")
        if timeout is not None:
            err = validate_timeout(timeout)
            if err:
                return err

    if tool_name == "download_product_image":
        fmt = arguments.get("output_format")
        if fmt is not None:
            err = validate_output_format(fmt)
            if err:
                return err
        max_w = arguments.get("max_width")
        if max_w is not None and (max_w < 50 or max_w > 4096):
            return "max_width must be between 50 and 4096"

    if tool_name == "search_and_download_image":
        query = arguments.get("search_query")
        if not query or len(query.strip()) < 2:
            return "search_query must be at least 2 characters"
        fmt = arguments.get("output_format")
        if fmt is not None:
            err = validate_output_format(fmt)
            if err:
                return err

    if tool_name == "screenshot_webpage":
        vw = arguments.get("viewport_width")
        vh = arguments.get("viewport_height")
        if vw is not None or vh is not None:
            err = validate_viewport(vw or 1920, vh or 1080)
            if err:
                return err

    return None


# -- Tool dispatcher -----------------------------------------------------------

TOOL_HANDLERS = {
    "fetch_protected_webpage": handle_fetch_webpage,
    "download_product_image": handle_download_image,
    "download_file": handle_download_file,
    "search_and_download_image": handle_search_image,
    "screenshot_webpage": handle_screenshot,
}


async def handle_request(msg: dict, browser_mgr: BrowserManager) -> dict | None:
    """Route an incoming JSON-RPC request to the appropriate handler."""
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    # Generate request-scoped correlation ID for tracing
    request_id = str(uuid.uuid4())[:8]

    if method == "initialize":
        logger.info("[%s] Client initializing", request_id)
        return result_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "web-scraper-mcp", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        logger.info("[%s] Client initialized", request_id)
        return None  # notification, no response

    if method == "tools/list":
        return result_response(req_id, {"tools": TOOLS})

    if method == "health/check":
        logger.info("[%s] Health check requested", request_id)
        status = await browser_mgr.health_check()
        return result_response(req_id, status)

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)

        if not handler:
            return error_response(req_id, -32601, f"Unknown tool: {tool_name}")

        # Validate inputs before execution
        validation_err = _validate_tool_arguments(tool_name, arguments)
        if validation_err:
            logger.warning("[%s] Validation failed for %s: %s", request_id, tool_name, validation_err)
            return result_response(req_id, tool_error(ErrorCode.INVALID_PARAMETER, validation_err))

        logger.info("[%s] Executing tool=%s", request_id, tool_name)
        try:
            result = await handler(arguments, browser_mgr)
            is_error = result.get("isError", False)
            if not is_error:
                result = _truncate_response(result)
            logger.info("[%s] Tool=%s completed (error=%s)", request_id, tool_name, is_error)
            return result_response(req_id, result)
        except Exception as e:
            logger.exception("[%s] Tool=%s raised unhandled exception", request_id, tool_name)
            error_code = classify_playwright_error(str(e))
            return result_response(req_id, tool_error(error_code, f"Internal error: {type(e).__name__}: {e}"))

    # Unknown method
    if req_id is not None:
        return error_response(req_id, -32601, f"Method not found: {method}")
    return None  # unknown notification -- ignore


# -- Main loop -----------------------------------------------------------------

async def run_server() -> None:
    """Main MCP server loop reading from stdin, writing to stdout."""
    config = BrowserConfig()
    browser_mgr = BrowserManager(config)
    shutdown_event = asyncio.Event()

    logger.info("Web Scraper MCP Server v1.0.0 starting (headless=%s, browser=%s)",
                config.headless, config.browser_type)

    # Startup health check -- verify browser can launch
    startup_health = await browser_mgr.health_check()
    if startup_health["healthy"]:
        logger.info("Startup health check passed (browser ready)")
    else:
        logger.error("Startup health check FAILED: %s", startup_health.get("error"))
        logger.error("Server will start but tool calls may fail until browser is available")

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s, shutdown_event))

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    try:
        while not shutdown_event.is_set():
            try:
                msg = await asyncio.wait_for(read_message(reader), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # check shutdown_event again
            if msg is None:
                break  # EOF
            response = await handle_request(msg, browser_mgr)
            if response is not None:
                write_message(response)
    finally:
        logger.info("Shutting down -- closing browser contexts...")
        await browser_mgr.cleanup()
        logger.info("Server shut down cleanly")


def _handle_signal(sig: signal.Signals, shutdown_event: asyncio.Event) -> None:
    logger.info("Received signal %s, initiating graceful shutdown...", sig.name)
    shutdown_event.set()


def main() -> None:
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
