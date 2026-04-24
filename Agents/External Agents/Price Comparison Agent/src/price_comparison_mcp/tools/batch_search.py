"""Batch price search for multiple products with auto-chunking + dedup.

Scaling characteristics:
  - 1-5 items: single chunk, full parallelism within the chunk.
  - 6-25 items: auto-split into chunks of 5, processed sequentially.
    Each chunk gets the full per-URL timeout budget so aggregator
    pages don't get cut off. Total wall-clock scales linearly
    (~50-60s per chunk).
  - >25 items: rejected with guidance. For procurement lists >25
    items the Procurement Workflow (async orchestration) is the
    right tool -- a single synchronous MCP call would block the
    agent thread for minutes and exceed upstream timeouts.

A hard wall-clock ceiling (_MAX_BATCH_WALL_CLOCK_SECONDS) protects
the stdio connection: any chunks we can't finish before that
deadline have their items marked "skipped" so the caller still
sees which queries were NOT processed instead of getting a
truncated / partial response silently.

Deduplication (v1.0):
  Tender documents routinely contain the same article twice or more
  (position 8/9/10 = "5068-M LED-Netzteil"). We canonicalise each
  query (lower + whitespace-collapsed) and run the pipeline ONCE per
  unique key. Duplicates share the offer set but keep their own
  `label`, `quantity`, and original-casing `query`, and carry a
  `deduplicated_from_position` pointer so the caller can still
  render per-position rows. Empty queries stay as their own unique
  entries so the per-item error surfaces cleanly.

  Savings: a 10-item batch where 3 items are dupes completes with
  7 unique chunk slots instead of 10. Improves wall-clock by ~30%
  on tender data and keeps logs readable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any, Callable

from ..browser_manager import BrowserManager
from ..cache import TTLCache
from ..config import PriceSearchConfig
from ..errors import ErrorCode, tool_error
from ..searxng_client import SearXNGClient
from .search_prices import handle_search_prices

logger = logging.getLogger("price-comparison-mcp.batch")

# Configuration constants -- tuned for quality-first B2B procurement
# within a single synchronous MCP call.
_MAX_BATCH_ITEMS = 25        # hard cap per tool invocation
_CHUNK_SIZE = 5              # items per chunk (balances quality vs throughput)
_BATCH_CONCURRENCY = 3       # parallel items within a chunk
# Wall-clock ceiling for the batch tool. Must leave enough headroom
# below the stdio MCP timeout (360s in the agent config) for:
#   - JSON response serialization of up to 50KB (~1s)
#   - stdio pipe flush + framework post-processing (~5-10s)
# The 20s buffer below stdio is tight but sufficient; any larger
# would waste usable batch time.
_MAX_BATCH_WALL_CLOCK = 340
_SAFETY_MARGIN = 5           # seconds reserved for final formatting


async def handle_batch_search(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
    searxng_client: SearXNGClient,
    serpapi_client: Any,
    search_config: PriceSearchConfig,
    cache: TTLCache,
    brave_client: Any = None,
    serper_client: Any = None,
    apify_client: Any = None,
    llm_validator: Any = None,
) -> dict[str, Any]:
    """Search prices for multiple products with auto-chunking.

    Items are processed in parallel groups (chunks) of ``_CHUNK_SIZE``.
    Each chunk gets the full per-URL timeout so aggregator pages
    (Idealo/Geizhals) can yield prices reliably. Chunks run
    sequentially to keep the shared browser context pool healthy.

    Returns a batch result with per-item offers, insights, and
    login-gated B2B hints. Items not processed before the hard
    wall-clock deadline are returned with ``status: "skipped"``
    so the caller always sees the full input list.
    """
    items = arguments.get("items", [])
    # Quality-first: fetch_details=true by default.
    fetch_details = arguments.get("fetch_details", True)
    response_mode = arguments.get("response_mode", "full")

    if not items:
        return tool_error(ErrorCode.INVALID_PARAMETER, "items must not be empty")

    if len(items) > _MAX_BATCH_ITEMS:
        return tool_error(
            ErrorCode.INVALID_PARAMETER,
            f"Maximum {_MAX_BATCH_ITEMS} items per batch call. Received "
            f"{len(items)}. For larger procurement lists use the "
            f"Procurement Workflow (async orchestration via agent mesh) "
            f"instead of a single batch_search_prices call -- a "
            f"synchronous call with more items would exceed the "
            f"agent's response timeout.",
        )

    start_time = time.monotonic()
    hard_deadline = start_time + _MAX_BATCH_WALL_CLOCK

    # -- v1.0 Dedup --------------------------------------------------------
    # Compute canonical keys for every item and build the unique-item
    # list. Each original position is remembered via position_to_unique
    # so we can fan the result out at the end. Items with an empty
    # query get their own unique slot (they need to produce per-item
    # error output; deduping them would collapse the error signals).
    unique_items: list[dict[str, Any]] = []
    position_to_unique: list[int] = []
    seen_keys: dict[str, int] = {}
    for item in items:
        raw_query = (item.get("query") or "").strip()
        key = _canonical_key(raw_query)
        if not key:
            # Empty query: keep as its own unique entry (no dedup).
            unique_items.append(item)
            position_to_unique.append(len(unique_items) - 1)
            continue
        if key in seen_keys:
            position_to_unique.append(seen_keys[key])
        else:
            seen_keys[key] = len(unique_items)
            unique_items.append(item)
            position_to_unique.append(len(unique_items) - 1)

    dedupe_savings = len(items) - len(unique_items)
    if dedupe_savings > 0:
        logger.info(
            "Batch dedup: %d items -> %d unique queries (saved %d slot(s))",
            len(items), len(unique_items), dedupe_savings,
        )

    # Build the per-item search config once. Within each chunk we use
    # the same budget for every item; sequential chunks each get a fresh
    # share so per-URL timeout stays at the full 18 seconds.
    #
    # Rounds per chunk with _CHUNK_SIZE=5 and _BATCH_CONCURRENCY=3:
    #   ceil(5/3) = 2 rounds, per_item_budget = (85-5)/2 = ~40s,
    #   inner per_url_timeout = min(18, 40/2) = 18s (full budget).
    chunk_rounds = max(1, (_CHUNK_SIZE + _BATCH_CONCURRENCY - 1) // _BATCH_CONCURRENCY)
    usable_total = max(20, search_config.total_timeout_seconds - _SAFETY_MARGIN)
    per_item_budget = max(15, usable_total // chunk_rounds)

    per_item_config = PriceSearchConfig(
        searxng_url=search_config.searxng_url,
        serpapi_key=search_config.serpapi_key,
        # 8 URLs per item (was 5) so B2B specialists (voltus,
        # elektro4000, contorion, mercateo, rs-online) that rank
        # outside SearXNG top-5 still get probed alongside the
        # consumer aggregators.
        max_detail_urls=min(search_config.max_detail_urls, 8),
        max_detail_urls_per_domain=search_config.max_detail_urls_per_domain,
        max_offers_per_domain=search_config.max_offers_per_domain,
        detail_timeout_seconds=search_config.detail_timeout_seconds,
        total_timeout_seconds=per_item_budget,
        cache_ttl_seconds=search_config.cache_ttl_seconds,
        # 3 concurrent URLs per item (was 2). With batch_concurrency
        # of 3 items, peak simultaneous browser contexts = 9, which
        # is why max_contexts was raised to 10 in config.py.
        concurrent_fetches=3,
    )

    # Build chunks over the DEDUPLICATED unique item list.
    chunks: list[list[dict[str, Any]]] = [
        unique_items[i:i + _CHUNK_SIZE]
        for i in range(0, len(unique_items), _CHUNK_SIZE)
    ]
    logger.info(
        "Batch: %d items (%d unique) -> %d chunks x<=%d "
        "(fetch_details=%s, per_item_budget=%ds, wall_clock_cap=%ds)",
        len(items), len(unique_items), len(chunks), _CHUNK_SIZE, fetch_details,
        per_item_budget, _MAX_BATCH_WALL_CLOCK,
    )

    # Build an item->result dict progressively so we can return partial
    # results on deadline.
    all_results: list[dict[str, Any]] = []
    chunks_completed = 0
    deadline_hit = False

    for chunk_index, chunk in enumerate(chunks):
        now = time.monotonic()
        remaining_wall = hard_deadline - now

        # If the remaining wall clock is too small to meaningfully
        # process another chunk (need at least per_item_budget + some
        # overhead), mark the rest as skipped.
        min_needed = per_item_budget * chunk_rounds + 5
        if remaining_wall < min_needed:
            logger.warning(
                "Batch: wall-clock deadline reached after %d/%d chunks "
                "(%.1fs remaining < %ds needed). Marking remaining items skipped.",
                chunk_index, len(chunks), remaining_wall, min_needed,
            )
            deadline_hit = True
            for remaining_chunk in chunks[chunk_index:]:
                for item in remaining_chunk:
                    all_results.append(_skipped_result(
                        item,
                        "Batch wall-clock deadline reached before this "
                        "chunk could start. Retry with fewer items or "
                        "use the Procurement Workflow.",
                    ))
            break

        logger.info(
            "Batch: processing chunk %d/%d (%d items, %.1fs wall clock remaining)",
            chunk_index + 1, len(chunks), len(chunk), remaining_wall,
        )
        chunk_t0 = time.monotonic()
        chunk_results = await _process_chunk(
            chunk,
            browser_mgr=browser_mgr,
            searxng_client=searxng_client,
            serpapi_client=serpapi_client,
            per_item_config=per_item_config,
            cache=cache,
            fetch_details=fetch_details,
            brave_client=brave_client,
            serper_client=serper_client,
            apify_client=apify_client,
            llm_validator=llm_validator,
            chunk_deadline=min(
                time.monotonic() + per_item_budget * chunk_rounds + 5,
                hard_deadline,
            ),
        )
        all_results.extend(chunk_results)
        chunks_completed += 1
        logger.info(
            "Batch: chunk %d/%d done in %.1fs (%d success, %d no-results, %d error/skip)",
            chunk_index + 1, len(chunks),
            time.monotonic() - chunk_t0,
            sum(1 for r in chunk_results if r.get("status") == "success"),
            sum(1 for r in chunk_results if r.get("status") == "no_results"),
            sum(1 for r in chunk_results if r.get("status") in ("error", "skipped")),
        )

    # -- v1.0 Dedup fan-out -----------------------------------------------
    # all_results contains ONE entry per unique item. Expand back to
    # the full-length per-position list, cloning the offer-set for
    # duplicates while preserving each position's label / quantity /
    # original-casing query.
    final_results: list[dict[str, Any]] = []
    for orig_idx, item in enumerate(items):
        uniq_idx = position_to_unique[orig_idx]
        if uniq_idx >= len(all_results):
            # The unique item wasn't processed (partial-batch case).
            # Build a skipped placeholder so the caller still sees the
            # full input list.
            final_results.append(_skipped_result(
                item,
                "Unique source query for this position was not processed.",
            ))
            continue

        uniq_result = all_results[uniq_idx]
        cloned = dict(uniq_result)
        # Override with THIS position's original fields (preserves
        # user-facing query string + tender-document metadata).
        cloned["query"] = item.get("query", "")
        cloned["label"] = item.get("label", "")
        cloned["quantity"] = item.get("quantity", 1)
        # Mark duplicates transparently so the LLM / downstream can
        # render "Pos. 8 = Pos. 5" callouts without guessing. The
        # pointer uses 1-based positions (human-readable).
        if position_to_unique.index(uniq_idx) != orig_idx:
            cloned["deduplicated_from_position"] = (
                position_to_unique.index(uniq_idx) + 1
            )
        final_results.append(cloned)

    total_ms = int((time.monotonic() - start_time) * 1000)

    # Build summary over the expanded list so the user sees the full
    # 10-item status picture, not just the 7 unique runs.
    successful = sum(1 for r in final_results if r.get("status") == "success")
    no_results = sum(1 for r in final_results if r.get("status") == "no_results")
    errors = sum(1 for r in final_results if r.get("status") in ("error",))
    skipped = sum(1 for r in final_results if r.get("status") == "skipped")

    batch_result: dict[str, Any] = {
        "items": final_results,
        "summary": {
            "total_items": len(items),
            "unique_queries": len(unique_items),
            "dedupe_savings": dedupe_savings,
            "chunks_total": len(chunks),
            "chunks_completed": chunks_completed,
            "successful": successful,
            "no_results": no_results,
            "errors": errors,
            "skipped": skipped,
            "deadline_hit": deadline_hit,
            "total_ms": total_ms,
        },
    }

    if response_mode == "summary":
        dedup_note = (
            f", deduped {dedupe_savings}" if dedupe_savings > 0 else ""
        )
        summary_text = (
            f"Batch: {successful}/{len(items)} successful, "
            f"{no_results} no-results, {errors} errors, {skipped} skipped "
            f"(chunks {chunks_completed}/{len(chunks)}{dedup_note}, {total_ms}ms)"
        )
        return {"content": [{"type": "text", "text": summary_text}]}

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(batch_result, ensure_ascii=False, indent=2),
        }],
    }


async def _process_chunk(
    items: list[dict[str, Any]],
    *,
    browser_mgr: BrowserManager,
    searxng_client: SearXNGClient,
    serpapi_client: Any,
    per_item_config: PriceSearchConfig,
    cache: TTLCache,
    fetch_details: bool,
    chunk_deadline: float,
    brave_client: Any = None,
    serper_client: Any = None,
    apify_client: Any = None,
    llm_validator: Any = None,
) -> list[dict[str, Any]]:
    """Process one chunk of items in parallel (up to _BATCH_CONCURRENCY)."""
    semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

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
            # Chunk-level deadline check: abort if this chunk has
            # already run out of time (e.g. two items hit 90s each).
            if time.monotonic() >= chunk_deadline:
                return _skipped_result(
                    item, "Chunk deadline reached before this item could start."
                )

            try:
                result = await handle_search_prices(
                    arguments={
                        "query": query,
                        # 4 offers per item -- enough to show price
                        # spread + 2-3 alternatives. Larger payloads
                        # push the LLM past its interactive response
                        # window (frontends cut SSE at 2-3 minutes).
                        # For deeper drill-down, the user can call
                        # search_product_prices on a single item.
                        "max_results": 4,
                        "fetch_details": fetch_details,
                        "response_mode": "full",
                    },
                    browser_mgr=browser_mgr,
                    searxng_client=searxng_client,
                    serpapi_client=serpapi_client,
                    search_config=per_item_config,
                    cache=cache,
                    brave_client=brave_client,
                    serper_client=serper_client,
                    apify_client=apify_client,
                    llm_validator=llm_validator,
                )

                content = result.get("content", [])
                text = ""
                for c in content:
                    if c.get("type") == "text":
                        text = c.get("text", "")
                        break

                try:
                    data = json.loads(text)
                    offers = data.get("offers", [])
                    insights = data.get("insights", {})
                    login_gated = data.get("login_gated_merchants", [])
                except (json.JSONDecodeError, AttributeError):
                    offers = []
                    insights = {}
                    login_gated = []

                trimmed_offers = [_trim_offer(o) for o in offers]

                # Cheapest = first non-outlier (so we don't point the
                # user at a flagged sub-listing).
                cheapest = next(
                    (o for o in trimmed_offers if not o.get("is_outlier")),
                    trimmed_offers[0] if trimmed_offers else None,
                )

                item_result: dict[str, Any] = {
                    "query": query,
                    "label": label,
                    "quantity": quantity,
                    "status": "success" if trimmed_offers else "no_results",
                    "offers": trimmed_offers,
                    "insights": insights,
                    "cheapest": cheapest,
                }
                if login_gated:
                    item_result["login_gated_merchants"] = login_gated
                return item_result
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
    return await asyncio.gather(*tasks)


def _trim_offer(o: dict[str, Any]) -> dict[str, Any]:
    """Drop verbose fields and empty values to minimise payload size.

    The LLM doesn't render ``page_title`` and treats ``None``/``False``/
    empty-string fields as absence, so omitting them saves significant
    tokens for large batches. is_outlier is always preserved (semantic
    signal -- even false means "checked and OK").
    """
    trimmed = {
        k: v for k, v in o.items()
        if k not in ("page_title",) and v not in (None, "", False)
    }
    trimmed.setdefault("is_outlier", False)
    return trimmed


def _skipped_result(item: dict[str, Any], reason: str) -> dict[str, Any]:
    """Build a skipped-status result placeholder for deadline handling."""
    return {
        "query": item.get("query", ""),
        "label": item.get("label", ""),
        "quantity": item.get("quantity", 1),
        "status": "skipped",
        "error": reason,
        "offers": [],
    }


def _canonical_key(query: str) -> str:
    """Normalise a query for deduplication.

    Equivalence rules:
      - Whitespace-collapsed (multiple spaces/tabs treated as one space)
      - Case-insensitive ("Bosch" == "BOSCH" == "bosch")
      - Leading/trailing whitespace stripped

    Intentionally NOT normalising punctuation: "Nike Air Max 42" and
    "Nike Air Max 42." are intentionally NOT equivalent -- the caller
    may have meant a different SKU. Diacritics are preserved too --
    "muesli" != "m\u00fcsli" stays as two distinct queries.
    """
    return " ".join(query.strip().lower().split())
