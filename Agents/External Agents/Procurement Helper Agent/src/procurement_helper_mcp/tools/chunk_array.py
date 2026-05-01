"""Tool: chunk_array -- split a JSON array into fixed-size sub-arrays
(used by the procurement workflow to chunk items for the
PriceComparisonAgent's batch_search_prices, which has a 25-item limit).

Pure list slicing, no network, no LLM.
"""

from __future__ import annotations

from typing import Any


def chunk_array(arguments: dict[str, Any]) -> dict[str, Any]:
    """Split items[] into chunks of at most chunk_size.

    Args:
        arguments: dict with keys:
            items: list -- the input array.
            chunk_size: int (default 25) -- max items per chunk.

    Returns:
        dict with keys:
            chunks: list of lists.
            chunk_count: int.
            total_items: int.
    """
    items = arguments.get("items", [])
    chunk_size = int(arguments.get("chunk_size", 25))

    if not isinstance(items, list):
        raise ValueError(f"items must be a list, got {type(items).__name__}")
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

    chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
    return {
        "chunks": chunks,
        "chunk_count": len(chunks),
        "total_items": len(items),
    }
