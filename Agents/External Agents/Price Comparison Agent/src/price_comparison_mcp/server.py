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
        "Output detail level. 'full' (default): complete JSON payload "
        "with all offers, insights, next_actions, timing -- use this "
        "when the LLM needs to render a procurement table. 'summary': "
        "compact text summary -- use when only the bottom-line price "
        "+ winner is needed (e.g. orchestrator routing decisions)."
    ),
    "default": "full",
}

# -- Tool definitions exposed via MCP -----------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_product_prices",
        "description": (
            "Search current market prices for ONE product. Accepts an "
            "EAN/GTIN barcode (8/12/13/14 digits), a manufacturer SKU, "
            "or a free-text product name. Returns offers ranked by "
            "composite confidence then total price (incl. shipping), "
            "each with: merchant, url, price, shipping_cost, "
            "total_price, currency, vat_status (net/gross), "
            "availability (in_stock/out_of_stock/on_request/backorder), "
            "match_confidence (exact/high/medium/low), is_outlier, "
            "outlier_reason, has_tier_pricing, min_order_quantity. "
            "Top-level result also carries: category (auto-classified "
            "product domain), locale, insights (min/median/max + "
            "filtered values excluding outliers), login_gated_merchants "
            "(B2B portals where login is required), next_actions (when "
            "zero offers found, with category-appropriate vendor "
            "suggestions), sources_queried, and timing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Product identifier. Accepts EAN/GTIN-8/12/13/14, "
                        "manufacturer article number, or descriptive name. "
                        "Example: '4013339593408', 'OBO KSA-S40', "
                        "'Bosch Professional GBH 2-26 F Bohrhammer'."
                    ),
                    "minLength": 2,
                    "maxLength": 500,
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Maximum number of offers to return after dedup. "
                        "Default 10 is sufficient for procurement; raise "
                        "to 20 for price-distribution analysis."
                    ),
                    "default": 10,
                    "minimum": 1,
                    "maximum": 20,
                },
                "fetch_details": {
                    "type": "boolean",
                    "description": (
                        "If true (default): fetch detail pages via stealth "
                        "browser for accurate distributor prices, EAN "
                        "cross-match, and tier-pricing extraction. "
                        "If false: return inline prices from search "
                        "snippets only -- 5x faster but lower accuracy "
                        "and no tier-pricing data."
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
            "Search prices for 2-25 products in a SINGLE call. Optimised "
            "for tender Positionslisten and procurement spreadsheets. "
            "The tool auto-chunks internally (5 items per inner chunk, "
            "3 chunks parallel) -- do NOT split a single list into "
            "multiple batch calls; the LLM turn budget would exhaust on "
            "artifact loads. Per-item result includes: query, quantity, "
            "label (echoed for correlation), status "
            "(success/no_results/error/skipped), offers, cheapest "
            "non-outlier, insights, login_gated_merchants. "
            "Quantity-aware bulk pricing: when an offer carries "
            "tier_pricing data and the requested quantity meets a tier, "
            "price is rewritten to the bulk price and tagged with "
            "bulk_price_applied=true + unit_price_listed + "
            "unit_price_tier_min_qty (so the user sees BOTH the "
            "discount and the unit ladder). For >25 items use the "
            "Procurement Workflow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": (
                        "1-25 products to search. Each item must have a "
                        "`query` (EAN, SKU, or name) and may optionally "
                        "specify `quantity` (for bulk-tier matching) and "
                        "`label` (Position-ID from the tender document, "
                        "echoed back in the result for correlation)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Product identifier (EAN/GTIN, SKU, "
                                    "or name). Same format as "
                                    "search_product_prices."
                                ),
                                "minLength": 2,
                                "maxLength": 500,
                            },
                            "quantity": {
                                "type": "integer",
                                "description": (
                                    "Procurement quantity. Triggers "
                                    "tier-price match when an offer "
                                    "exposes a Staffelpreis ladder."
                                ),
                                "minimum": 1,
                                "default": 1,
                            },
                            "label": {
                                "type": "string",
                                "description": (
                                    "Position label / line number from "
                                    "the tender document (e.g. 'Pos 3' "
                                    "or 'LV-001'). Echoed back in the "
                                    "result for correlation. Optional."
                                ),
                            },
                        },
                        "required": ["query"],
                    },
                    "minItems": 1,
                    "maxItems": 25,
                },
                "fetch_details": {
                    "type": "boolean",
                    "description": (
                        "If true (default): full pipeline with detail "
                        "page fetch, EAN cross-match, tier-price "
                        "extraction. The batch tool auto-shrinks "
                        "per-item budgets to keep the overall wall-"
                        "clock under the deadline. If false: SearXNG "
                        "snippets only -- much faster, no tier "
                        "pricing, lower accuracy. Recommended only for "
                        "smoke tests or >25-item lists where speed "
                        "trumps precision."
                    ),
                    "default": True,
                },
                "response_mode": RESPONSE_MODE_PROPERTY,
            },
            "required": ["items"],
        },
    },
    {
        "name": "export_comparison_report",
        "description": (
            "Format a previous price-comparison result as CSV text. "
            "Call AFTER search_product_prices or batch_search_prices "
            "with the resulting JSON. CSV columns: position, query, "
            "quantity, status, cheapest_merchant, unit_price, "
            "shipping, total_price, total_x_quantity, min/median/max, "
            "match_confidence, outlier_flag + reason, availability, "
            "vat_status, ean, url, note. Suitable for award memos, "
            "procurement-system imports, and audit trails."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "results_json": {
                    "type": "string",
                    "description": (
                        "JSON string returned by search_product_prices "
                        "or batch_search_prices. Pass the full result "
                        "verbatim -- the tool extracts the offers array "
                        "and per-item structure automatically."
                    ),
                },
                "include_insights": {
                    "type": "boolean",
                    "description": (
                        "If true (default): include min/median/max "
                        "columns derived from the insights block. If "
                        "false: emit only per-offer rows."
                    ),
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
        "Price Comparison MCP Server v1.0.0 starting "
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
