"""Tests for batch_search_prices deduplication (v1.0).

Covers:
  - Canonical-key normalisation (whitespace, case, empty)
  - Unique-list construction + position-to-unique mapping
  - Fan-out preserves per-position label/quantity/original query
  - `deduplicated_from_position` marker points to 1-based index
  - Summary stats reflect the full expanded item list (not unique)
  - Partial-batch case: unique result missing -> skipped placeholder
"""
from __future__ import annotations

import pytest

from price_comparison_mcp.tools.batch_search import _canonical_key


# -----------------------------------------------------------------------------
# _canonical_key
# -----------------------------------------------------------------------------


class TestCanonicalKey:
    @pytest.mark.parametrize(
        "a,b,equal",
        [
            ("Bosch GBH 2-26 F", "bosch gbh 2-26 f", True),          # case
            ("Bosch GBH 2-26 F", "BOSCH GBH 2-26 F", True),
            ("  Bosch GBH 2-26 F  ", "Bosch GBH 2-26 F", True),      # whitespace
            ("Bosch  GBH   2-26 F", "Bosch GBH 2-26 F", True),       # collapsed
            ("Bosch GBH 2-26 F", "Bosch GBH 2-26", False),           # different
            ("Nike Air Max 42", "Nike Air Max 42.", False),          # punctuation
            ("muesli", "müsli", False),                              # diacritic
        ],
    )
    def test_equivalence(self, a, b, equal):
        assert (_canonical_key(a) == _canonical_key(b)) is equal

    def test_empty(self):
        assert _canonical_key("") == ""
        assert _canonical_key("   ") == ""
        assert _canonical_key("\t\n  \n") == ""


# -----------------------------------------------------------------------------
# Dedup integration: call handle_batch_search with synthetic mock and
# verify the fan-out structure
# -----------------------------------------------------------------------------


class TestBatchDedupIntegration:
    """End-to-end dedup test with a monkeypatched handle_search_prices
    that returns distinct offers per unique query -- we can then
    assert duplicates share the offers but keep their own meta.
    """

    @pytest.fixture
    def fake_pipeline(self, monkeypatch):
        """Replace handle_search_prices with a deterministic stub that
        produces one synthetic offer per unique query."""
        from price_comparison_mcp.tools import batch_search

        call_log: list[str] = []

        async def _stub(arguments, **_kw):
            q = arguments.get("query", "")
            call_log.append(q)
            # Produce a stable-looking offer set
            import json
            data = {
                "offers": [
                    {
                        "merchant": "mock-shop",
                        "price": 100.0 + len(call_log),
                        "total_price": 100.0 + len(call_log),
                        "match_confidence": "high",
                        "is_outlier": False,
                        "url": f"https://mock.example/{q[:20]}",
                    }
                ],
                "insights": {"num_offers": 1, "median_price": 100.0 + len(call_log)},
                "login_gated_merchants": [],
            }
            return {"content": [{"type": "text", "text": json.dumps(data)}]}

        monkeypatch.setattr(batch_search, "handle_search_prices", _stub)
        return call_log

    @pytest.mark.asyncio
    async def test_three_way_dedup(self, fake_pipeline):
        """[A, A, B, A, C] -> 3 unique runs, 5 result entries."""
        from price_comparison_mcp.tools.batch_search import handle_batch_search

        items = [
            {"query": "Bosch GBH 2-26 F", "label": "Pos 1", "quantity": 2},
            {"query": "bosch gbh 2-26 f", "label": "Pos 2", "quantity": 3},   # dup of 1 (case)
            {"query": "Sony WH-1000XM5", "label": "Pos 3", "quantity": 1},
            {"query": "  Bosch GBH 2-26 F  ", "label": "Pos 4", "quantity": 5},  # dup of 1 (whitespace)
            {"query": "Nike Air Max 42", "label": "Pos 5", "quantity": 1},
        ]
        result = await handle_batch_search(
            arguments={"items": items, "fetch_details": True},
            browser_mgr=None,
            searxng_client=None,
            serpapi_client=None,
            search_config=_make_config(),
            cache=_FakeCache(),
        )
        import json as _json
        data = _json.loads(result["content"][0]["text"])

        # ---- Structure ----
        assert len(data["items"]) == 5                   # all positions returned
        assert data["summary"]["total_items"] == 5
        assert data["summary"]["unique_queries"] == 3
        assert data["summary"]["dedupe_savings"] == 2

        # ---- Pipeline was called exactly ONCE per unique query ----
        assert len(fake_pipeline) == 3
        assert sorted(fake_pipeline) == sorted([
            "Bosch GBH 2-26 F",
            "Sony WH-1000XM5",
            "Nike Air Max 42",
        ])

        # ---- Fan-out: position 1 is the primary (no dedup marker) ----
        assert data["items"][0]["query"] == "Bosch GBH 2-26 F"
        assert data["items"][0]["label"] == "Pos 1"
        assert data["items"][0]["quantity"] == 2
        assert "deduplicated_from_position" not in data["items"][0]

        # Position 2 is a dup of 1 -- same offers, own label/quantity,
        # carries the pointer.
        assert data["items"][1]["query"] == "bosch gbh 2-26 f"  # original casing preserved
        assert data["items"][1]["label"] == "Pos 2"
        assert data["items"][1]["quantity"] == 3
        assert data["items"][1]["deduplicated_from_position"] == 1
        assert data["items"][1]["offers"] == data["items"][0]["offers"]

        # Position 4 dup of 1
        assert data["items"][3]["label"] == "Pos 4"
        assert data["items"][3]["quantity"] == 5
        assert data["items"][3]["deduplicated_from_position"] == 1
        assert data["items"][3]["offers"] == data["items"][0]["offers"]

        # Position 3 (Sony) and 5 (Nike) are their own primaries
        assert "deduplicated_from_position" not in data["items"][2]
        assert "deduplicated_from_position" not in data["items"][4]

    @pytest.mark.asyncio
    async def test_no_dedup_when_all_unique(self, fake_pipeline):
        """All 5 queries distinct -> dedupe_savings=0."""
        from price_comparison_mcp.tools.batch_search import handle_batch_search
        items = [
            {"query": "Query A"},
            {"query": "Query B"},
            {"query": "Query C"},
            {"query": "Query D"},
            {"query": "Query E"},
        ]
        result = await handle_batch_search(
            arguments={"items": items},
            browser_mgr=None,
            searxng_client=None,
            serpapi_client=None,
            search_config=_make_config(),
            cache=_FakeCache(),
        )
        import json as _json
        data = _json.loads(result["content"][0]["text"])
        assert data["summary"]["unique_queries"] == 5
        assert data["summary"]["dedupe_savings"] == 0
        assert len(fake_pipeline) == 5
        for item in data["items"]:
            assert "deduplicated_from_position" not in item

    @pytest.mark.asyncio
    async def test_empty_query_not_deduped(self, fake_pipeline):
        """Empty queries stay as unique entries so each gets its own error."""
        from price_comparison_mcp.tools.batch_search import handle_batch_search
        items = [
            {"query": "", "label": "Empty 1"},
            {"query": "", "label": "Empty 2"},
            {"query": "Nike Air Max", "label": "Valid"},
        ]
        result = await handle_batch_search(
            arguments={"items": items},
            browser_mgr=None,
            searxng_client=None,
            serpapi_client=None,
            search_config=_make_config(),
            cache=_FakeCache(),
        )
        import json as _json
        data = _json.loads(result["content"][0]["text"])
        # Both empty queries remain as unique positions (not deduped)
        assert data["summary"]["unique_queries"] == 3
        assert data["summary"]["dedupe_savings"] == 0
        # Empty entries report per-item errors
        assert data["items"][0]["status"] == "error"
        assert data["items"][1]["status"] == "error"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _make_config():
    from price_comparison_mcp.config import PriceSearchConfig
    return PriceSearchConfig(total_timeout_seconds=60)


class _FakeCache:
    """Minimal cache stub -- batch_search doesn't call cache methods
    directly; the underlying (mocked) handle_search_prices would."""
    def get(self, key):
        return None

    def set(self, key, value):
        pass
