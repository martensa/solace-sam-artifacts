"""MCP Server entry point for the Price Comparison Agent.

Implements MCP JSON-RPC 2.0 over stdio using newline-delimited JSON framing.
Combines SearXNG meta-search, optional SerpAPI, and Playwright for price extraction.
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
from .cache import TTLCache
from .config import MCP_LOG_FILE, MCP_LOG_LEVEL, MCP_MAX_RESPONSE_CHARS, BrowserConfig, PriceSearchConfig
from .errors import ErrorCode, classify_playwright_error, tool_error
from .response import validate_response_mode
from .searxng_client import SearXNGClient
from .serpapi_client import SerpAPIClient
from .brave_client import BraveSearchClient
from .serper_client import SerperClient
from .apify_client import ApifyGoogleShoppingClient
from .result_validator import create_validator_from_env
from .tools.batch_search import handle_batch_search
from .tools.export_report import handle_export_report
from .tools.search_prices import handle_search_prices

# -- Logging -------------------------------------------------------------------

log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
if MCP_LOG_FILE:
    log_handlers.append(logging.FileHandler(MCP_LOG_FILE))

logging.basicConfig(
    level=getattr(logging, MCP_LOG_LEVEL.upper(), logging.INFO),
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    handlers=log_handlers,
)
logger = logging.getLogger("price-comparison-mcp")

# -- Shared response_mode property added to every tool schema -----------------

RESPONSE_MODE_PROPERTY: dict[str, Any] = {
    "type": "string",
    "enum": ["full", "summary"],
    "description": (
        "Controls response detail. "
        "full: inline JSON payload (default). "
        "summary: brief text summary only."
    ),
    "default": "full",
}

# -- Tool definitions exposed via MCP -----------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_product_prices",
        "description": (
            "Search current market prices for a product by EAN barcode or "
            "product name. Queries SearXNG meta-search and optionally Google "
            "Shopping via SerpAPI, then fetches detail pages for accurate "
            "price extraction. Returns offers sorted by total price with "
            "merchant, shipping, and source URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Product name, EAN barcode (8/12/13/14 digits), or description",
                    "minLength": 2,
                    "maxLength": 500,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of offers to return (1-20, default: 10)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 20,
                },
                "fetch_details": {
                    "type": "boolean",
                    "description": (
                        "Whether to fetch detail pages via browser for accurate "
                        "prices (default: true). Set to false for faster but less "
                        "accurate results from search snippets only."
                    ),
                    "default": True,
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["query"],
        },
    },
    {
        "name": "batch_search_prices",
        "description": (
            "Search prices for multiple products at once (tender "
            "documents, procurement lists). Supports 1-25 items per call; "
            "items are auto-chunked into groups of 5 and processed "
            "sequentially so each item gets the full per-URL timeout. "
            "For lists >25 items the Procurement Workflow should be "
            "used instead (async orchestration). Typical wall-clock: "
            "~50s for 5 items, ~150s for 15 items, ~270s for 25 items."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "minLength": 2,
                                "maxLength": 500,
                            },
                            "quantity": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 1,
                            },
                            "label": {
                                "type": "string",
                                "description": "Position label from tender document",
                            },
                        },
                        "required": ["query"],
                    },
                    "minItems": 1,
                    "maxItems": 25,
                },
                "fetch_details": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Fetch detail pages via Playwright (accurate "
                        "distributor prices). Default true; the batch "
                        "pipeline automatically shrinks per-item budgets "
                        "to stay within the overall time window. Set "
                        "false only for very large lists (>=6 items) "
                        "where snippet-only prices are acceptable."
                    ),
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["items"],
        },
    },
    {
        "name": "export_comparison_report",
        "description": (
            "Export price comparison results as CSV text. Call "
            "search_product_prices first, then pass the results "
            "to this tool for CSV formatting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "results_json": {
                    "type": "string",
                    "description": (
                        "JSON string from a previous search_product_prices "
                        "or batch_search_prices result"
                    ),
                },
                "include_insights": {
                    "type": "boolean",
                    "default": True,
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["results_json"],
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
            continue
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


# -- Input validation ----------------------------------------------------------

def _validate_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Validate tool arguments. Returns error message or None if valid."""
    mode = arguments.get("response_mode")
    if mode is not None:
        err = validate_response_mode(mode)
        if err:
            return err

    if tool_name == "search_product_prices":
        query = arguments.get("query")
        if not query or len(query.strip()) < 2:
            return "query must be at least 2 characters"

    if tool_name == "batch_search_prices":
        items = arguments.get("items")
        if not items or not isinstance(items, list):
            return "items must be a non-empty array"
        if len(items) > 10:
            return "Maximum 10 items per batch"

    if tool_name == "export_comparison_report":
        results_json = arguments.get("results_json")
        if not results_json:
            return "results_json must not be empty"

    return None


# -- Tool dispatcher -----------------------------------------------------------

async def handle_request(
    msg: dict,
    browser_mgr: BrowserManager,
    searxng_client: SearXNGClient,
    serpapi_client: SerpAPIClient | None,
    search_config: PriceSearchConfig,
    cache: TTLCache,
    brave_client: BraveSearchClient | None = None,
    serper_client: SerperClient | None = None,
    apify_client: ApifyGoogleShoppingClient | None = None,
    llm_validator: Any = None,
) -> dict | None:
    """Route an incoming JSON-RPC request to the appropriate handler."""
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})

    request_id = str(uuid.uuid4())[:8]

    if method == "initialize":
        logger.info("[%s] Client initializing", request_id)
        return result_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "price-comparison-mcp", "version": "2.1.0"},
        })

    if method == "notifications/initialized":
        logger.info("[%s] Client initialized", request_id)
        return None

    if method == "tools/list":
        return result_response(req_id, {"tools": TOOLS})

    if method == "health/check":
        logger.info("[%s] Health check requested", request_id)
        status = await browser_mgr.health_check()
        status["searxng_url"] = search_config.searxng_url
        status["serpapi_available"] = serpapi_client is not None and serpapi_client.available
        return result_response(req_id, status)

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in ("search_product_prices", "batch_search_prices", "export_comparison_report"):
            return error_response(req_id, -32601, f"Unknown tool: {tool_name}")

        validation_err = _validate_tool_arguments(tool_name, arguments)
        if validation_err:
            logger.warning("[%s] Validation failed for %s: %s", request_id, tool_name, validation_err)
            return result_response(req_id, tool_error(ErrorCode.INVALID_PARAMETER, validation_err))

        logger.info("[%s] Executing tool=%s", request_id, tool_name)
        try:
            if tool_name == "search_product_prices":
                result = await handle_search_prices(
                    arguments, browser_mgr, searxng_client,
                    serpapi_client, search_config, cache,
                    brave_client=brave_client,
                    serper_client=serper_client,
                    apify_client=apify_client,
                    llm_validator=llm_validator,
                )
            elif tool_name == "batch_search_prices":
                result = await handle_batch_search(
                    arguments, browser_mgr, searxng_client,
                    serpapi_client, search_config, cache,
                    brave_client=brave_client,
                    serper_client=serper_client,
                    apify_client=apify_client,
                    llm_validator=llm_validator,
                )
            elif tool_name == "export_comparison_report":
                result = await handle_export_report(arguments)
            else:
                return error_response(req_id, -32601, f"Unknown tool: {tool_name}")

            is_error = result.get("isError", False)
            if not is_error:
                result = _truncate_response(result)
            logger.info("[%s] Tool=%s completed (error=%s)", request_id, tool_name, is_error)
            return result_response(req_id, result)
        except Exception as e:
            logger.exception("[%s] Tool=%s raised unhandled exception", request_id, tool_name)
            error_code = classify_playwright_error(str(e))
            return result_response(req_id, tool_error(error_code, f"Internal error: {type(e).__name__}: {e}"))

    if req_id is not None:
        return error_response(req_id, -32601, f"Method not found: {method}")
    return None


# -- Main loop -----------------------------------------------------------------

async def run_server() -> None:
    """Main MCP server loop reading from stdin, writing to stdout."""
    browser_config = BrowserConfig()
    search_config = PriceSearchConfig()
    browser_mgr = BrowserManager(browser_config)
    searxng_client = SearXNGClient(search_config.searxng_url)
    serpapi_client = SerpAPIClient(search_config.serpapi_key) if search_config.serpapi_key else None
    # Optional additional search backends. Each is inert when its API
    # key is empty, so we always instantiate them and let .available
    # decide whether they participate in discovery.
    brave_client = BraveSearchClient(search_config.brave_api_key)
    serper_client = SerperClient(search_config.serper_api_key)
    apify_client = ApifyGoogleShoppingClient(search_config.apify_token)
    llm_validator = create_validator_from_env()
    cache = TTLCache(ttl=search_config.cache_ttl_seconds)
    shutdown_event = asyncio.Event()

    logger.info(
        "Price Comparison MCP Server v2.3.5 starting "
        "(searxng=%s, serpapi=%s, brave=%s, serper=%s, apify=%s, "
        "llm_validator=%s, headless=%s)",
        search_config.searxng_url,
        "on" if serpapi_client and serpapi_client.available else "off",
        "on" if brave_client.available else "off",
        "on" if serper_client.available else "off",
        "on" if apify_client.available else "off",
        "on" if (llm_validator and llm_validator.available) else "off",
        browser_config.headless,
    )

    # Startup health check
    startup_health = await browser_mgr.health_check()
    if startup_health["healthy"]:
        logger.info("Startup health check passed (browser ready)")
    else:
        logger.error("Startup health check FAILED: %s", startup_health.get("error"))
        logger.error("Server will start but detail fetching may fail")

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
                continue
            if msg is None:
                break
            response = await handle_request(
                msg, browser_mgr, searxng_client,
                serpapi_client, search_config, cache,
                brave_client=brave_client,
                serper_client=serper_client,
                apify_client=apify_client,
                llm_validator=llm_validator,
            )
            if response is not None:
                write_message(response)
    finally:
        logger.info("Shutting down...")
        await browser_mgr.cleanup()
        await searxng_client.close()
        if serpapi_client:
            await serpapi_client.close()
        await brave_client.close()
        await serper_client.close()
        await apify_client.close()
        if llm_validator:
            await llm_validator.close()
        logger.info("Server shut down cleanly")


def _handle_signal(sig: signal.Signals, shutdown_event: asyncio.Event) -> None:
    logger.info("Received signal %s, initiating graceful shutdown...", sig.name)
    shutdown_event.set()


def main() -> None:
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
