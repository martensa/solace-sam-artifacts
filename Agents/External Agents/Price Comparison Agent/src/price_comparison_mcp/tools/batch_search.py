"""Batch price search for multiple products."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..browser_manager import BrowserManager
from ..cache import TTLCache
from ..config import PriceSearchConfig
from ..errors import ErrorCode, tool_error
from ..searxng_client import SearXNGClient
from .search_prices import handle_search_prices

logger = logging.getLogger("price-comparison-mcp.batch")


async def handle_batch_search(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
    searxng_client: SearXNGClient,
    serpapi_client: Any,
    search_config: PriceSearchConfig,
    cache: TTLCache,
) -> dict[str, Any]:
    """Search prices for multiple products with a shared timing budget."""
    items = arguments.get("items", [])
    fetch_details = arguments.get("fetch_details", False)
    response_mode = arguments.get("response_mode", "full")

    if not items:
        return tool_error(ErrorCode.INVALID_PARAMETER, "items must not be empty")

    if len(items) > 10:
        return tool_error(ErrorCode.INVALID_PARAMETER, "Maximum 10 items per batch")

    start_time = time.monotonic()
    results: list[dict[str, Any]] = []

    # Process items with concurrency limit
    semaphore = asyncio.Semaphore(3)

    async def search_item(item: dict[str, Any]) -> dict[str, Any]:
        query = item.get("query", "").strip()
        quantity = item.get("quantity", 1)
        label = item.get("label", "")

        if not query:
            return {
                "query": query,
                "label": label,
                "quantity": quantity,
                "status": "error",
                "error": "Empty query",
                "offers": [],
            }

        async with semaphore:
            # Check remaining time budget
            elapsed = time.monotonic() - start_time
            if elapsed > search_config.total_timeout_seconds - 5:
                return {
                    "query": query,
                    "label": label,
                    "quantity": quantity,
                    "status": "skipped",
                    "error": "Time budget exceeded",
                    "offers": [],
                }

            try:
                result = await handle_search_prices(
                    arguments={
                        "query": query,
                        "max_results": 5,
                        "fetch_details": fetch_details,
                        "response_mode": "full",
                    },
                    browser_mgr=browser_mgr,
                    searxng_client=searxng_client,
                    serpapi_client=serpapi_client,
                    search_config=search_config,
                    cache=cache,
                )

                # Parse the result content
                content = result.get("content", [])
                text = ""
                for c in content:
                    if c.get("type") == "text":
                        text = c.get("text", "")
                        break

                try:
                    data = json.loads(text)
                    offers = data.get("offers", [])
                except (json.JSONDecodeError, AttributeError):
                    offers = []

                return {
                    "query": query,
                    "label": label,
                    "quantity": quantity,
                    "status": "success" if offers else "no_results",
                    "offers": offers[:5],
                    "cheapest": offers[0] if offers else None,
                }
            except Exception as e:
                logger.warning("Batch item '%s' failed: %s", query[:40], e)
                return {
                    "query": query,
                    "label": label,
                    "quantity": quantity,
                    "status": "error",
                    "error": str(e),
                    "offers": [],
                }

    tasks = [search_item(item) for item in items]
    results = await asyncio.gather(*tasks)

    total_ms = int((time.monotonic() - start_time) * 1000)

    # Build summary
    successful = sum(1 for r in results if r.get("status") == "success")
    no_results = sum(1 for r in results if r.get("status") == "no_results")
    errors = sum(1 for r in results if r.get("status") in ("error", "skipped"))

    batch_result = {
        "items": list(results),
        "summary": {
            "total_items": len(items),
            "successful": successful,
            "no_results": no_results,
            "errors": errors,
            "total_ms": total_ms,
        },
    }

    if response_mode == "summary":
        summary_text = (
            f"Batch search: {successful}/{len(items)} with results, "
            f"{no_results} without results, {errors} errors. "
            f"Time: {total_ms}ms"
        )
        return {"content": [{"type": "text", "text": summary_text}]}

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(batch_result, ensure_ascii=False, indent=2),
        }],
    }
