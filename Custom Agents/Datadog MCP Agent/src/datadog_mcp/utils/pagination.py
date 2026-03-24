"""Pagination helpers."""

from __future__ import annotations


def paginate_list(items: list, page: int = 1, page_size: int = 25) -> tuple[list, int, bool]:
    """Slice a list for pagination. Returns (page_items, total_count, has_more)."""
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], total, end < total
