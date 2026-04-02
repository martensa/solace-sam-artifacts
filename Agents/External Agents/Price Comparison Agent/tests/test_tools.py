"""Tests for price_comparison_agent.tools helper functions."""

import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock solace_agent_mesh before importing tools module.
# PriceComparisonToolProvider inherits from DynamicToolProvider and uses
# @PriceComparisonToolProvider.register_tool as a decorator on module-level
# functions. The mock must support this inheritance + decorator pattern.
# ---------------------------------------------------------------------------

_sam = types.ModuleType("solace_agent_mesh")
_sam_agent = types.ModuleType("solace_agent_mesh.agent")
_sam_tools = types.ModuleType("solace_agent_mesh.agent.tools")
_sam_dynamic = types.ModuleType("solace_agent_mesh.agent.tools.dynamic_tool")


class _FakeToolResult:
    """Minimal ToolResult mock."""

    @staticmethod
    def ok(**kwargs):
        return {"ok": True, **kwargs}

    @staticmethod
    def error(msg):
        return {"error": msg}


class _FakeDynamicToolProvider:
    """Minimal DynamicToolProvider mock that supports register_tool decorator."""

    config_model = None

    @classmethod
    def register_tool(cls, fn):
        """Decorator that returns the function unchanged."""
        return fn

    def _create_tools_from_decorators(self, tool_config=None):
        return []


_sam_tools.ToolResult = _FakeToolResult
_sam_dynamic.DynamicTool = MagicMock()
_sam_dynamic.DynamicToolProvider = _FakeDynamicToolProvider

sys.modules.setdefault("solace_agent_mesh", _sam)
sys.modules.setdefault("solace_agent_mesh.agent", _sam_agent)
sys.modules.setdefault("solace_agent_mesh.agent.tools", _sam_tools)
sys.modules.setdefault("solace_agent_mesh.agent.tools.dynamic_tool", _sam_dynamic)
sys.modules.setdefault("solace_agent_mesh.agent.sac", types.ModuleType("solace_agent_mesh.agent.sac"))
sys.modules.setdefault("solace_agent_mesh.agent.sac.app", types.ModuleType("solace_agent_mesh.agent.sac.app"))

from price_comparison_agent.models import (
    DataSource,
    PriceInsights,
    ProductOffer,
    ProductResult,
)
from price_comparison_agent.tools import (
    _compute_insights,
    _dedup_key,
    _merge_products,
    _parse_batch_input,
)


# ---------------------------------------------------------------------------
# _parse_batch_input
# ---------------------------------------------------------------------------


class TestParseBatchInput:
    """Tests for batch input parser."""

    def test_single_product(self):
        items = _parse_batch_input("Bosch GSR 18V")
        assert len(items) == 1
        assert items[0].query == "Bosch GSR 18V"
        assert items[0].quantity == 1
        assert items[0].label is None

    def test_quantity_with_x(self):
        items = _parse_batch_input("10 x Bosch GSR 18V")
        assert len(items) == 1
        assert items[0].query == "Bosch GSR 18V"
        assert items[0].quantity == 10

    def test_quantity_uppercase_x(self):
        items = _parse_batch_input("5 X Hilti TE 30")
        assert len(items) == 1
        assert items[0].query == "Hilti TE 30"
        assert items[0].quantity == 5

    def test_quantity_no_space_after_x(self):
        items = _parse_batch_input("3x Produkt A")
        assert len(items) == 1
        assert items[0].query == "Produkt A"
        assert items[0].quantity == 3

    def test_label_with_pipe(self):
        items = _parse_batch_input("10 x 4006381333931 | Pos. 1.1")
        assert len(items) == 1
        assert items[0].query == "4006381333931"
        assert items[0].quantity == 10
        assert items[0].label == "Pos. 1.1"

    def test_multiline(self):
        raw = "Bosch GSR 18V\n5 x Hilti TE 30\n3 x Fischer FIS V 360 | Pos. 3"
        items = _parse_batch_input(raw)
        assert len(items) == 3
        assert items[0].query == "Bosch GSR 18V"
        assert items[0].quantity == 1
        assert items[1].query == "Hilti TE 30"
        assert items[1].quantity == 5
        assert items[2].query == "Fischer FIS V 360"
        assert items[2].quantity == 3
        assert items[2].label == "Pos. 3"

    def test_comma_separated(self):
        raw = "Produkt A, Produkt B, Produkt C"
        items = _parse_batch_input(raw)
        assert len(items) == 3
        assert items[0].query == "Produkt A"
        assert items[1].query == "Produkt B"
        assert items[2].query == "Produkt C"

    def test_empty_lines_skipped(self):
        raw = "Produkt A\n\n\nProdukt B\n"
        items = _parse_batch_input(raw)
        assert len(items) == 2

    def test_empty_input(self):
        items = _parse_batch_input("")
        assert len(items) == 0

    def test_whitespace_only(self):
        items = _parse_batch_input("   \n  \n  ")
        assert len(items) == 0

    def test_pipe_without_quantity(self):
        items = _parse_batch_input("Produkt A | Position 1")
        assert len(items) == 1
        assert items[0].query == "Produkt A"
        assert items[0].quantity == 1
        assert items[0].label == "Position 1"

    def test_ean_input(self):
        items = _parse_batch_input("4006381333931")
        assert len(items) == 1
        assert items[0].query == "4006381333931"
        assert items[0].quantity == 1

    def test_comma_with_pipe_uses_lines(self):
        # When pipes are present, comma splitting is NOT used
        raw = "Produkt A | Pos 1, Produkt B | Pos 2"
        items = _parse_batch_input(raw)
        # Since there's a pipe, it treats it as single line with pipe
        assert len(items) == 1


# ---------------------------------------------------------------------------
# _dedup_key
# ---------------------------------------------------------------------------


class TestDedupKey:
    """Tests for deduplication key generation."""

    def test_lowercase(self):
        assert _dedup_key("Bosch GSR 18V") == "bosch gsr 18v"

    def test_strips_whitespace(self):
        assert _dedup_key("  Bosch GSR 18V  ") == "bosch gsr 18v"

    def test_identical_keys(self):
        assert _dedup_key("Product A") == _dedup_key("product a")


# ---------------------------------------------------------------------------
# _compute_insights
# ---------------------------------------------------------------------------


def _make_product(name: str, prices: list[float]) -> ProductResult:
    """Helper to create a ProductResult with offers at given prices."""
    offers = [
        ProductOffer(
            merchant_name=f"Merchant_{i}",
            price=p,
            shipping_cost=0.0,
            total_price=p,
            product_url=f"https://example.com/{i}",
            source_url=f"https://example.com/{i}",
            source=DataSource.IDEALO,
        )
        for i, p in enumerate(prices)
    ]
    return ProductResult(name=name, offers=offers)


class TestComputeInsights:
    """Tests for price insights computation."""

    def test_basic_insights(self):
        products = [_make_product("A", [10.0, 20.0, 30.0])]
        insights = _compute_insights(products)
        assert insights is not None
        assert insights.min_price == 10.0
        assert insights.max_price == 30.0
        assert insights.median_price == 20.0
        assert insights.avg_price == 20.0
        assert insights.price_spread == 20.0
        assert insights.num_offers == 3
        assert insights.num_merchants == 3

    def test_single_offer_returns_none(self):
        products = [_make_product("A", [10.0])]
        insights = _compute_insights(products)
        assert insights is None

    def test_empty_products(self):
        insights = _compute_insights([])
        assert insights is None

    def test_no_offers(self):
        product = ProductResult(name="Empty", offers=[])
        insights = _compute_insights([product])
        assert insights is None

    def test_multiple_products(self):
        products = [
            _make_product("A", [10.0, 20.0]),
            _make_product("B", [15.0, 25.0]),
        ]
        insights = _compute_insights(products)
        assert insights is not None
        assert insights.min_price == 10.0
        assert insights.max_price == 25.0
        assert insights.num_offers == 4

    def test_spread_percent(self):
        products = [_make_product("A", [100.0, 200.0])]
        insights = _compute_insights(products)
        assert insights is not None
        # median = 150, spread = 100, pct = 100/150*100 = 66.7
        assert insights.price_spread_percent == 66.7

    def test_sources_tracked(self):
        products = [_make_product("A", [10.0, 20.0])]
        insights = _compute_insights(products)
        assert insights is not None
        assert "idealo" in insights.sources_with_results


# ---------------------------------------------------------------------------
# _merge_products
# ---------------------------------------------------------------------------


class TestMergeProducts:
    """Tests for product merging and deduplication."""

    def test_merge_same_product(self):
        p1 = _make_product("Product A", [10.0])
        p2 = _make_product("Product A", [8.0])
        outputs = [([p1], None), ([p2], None)]
        products, total = _merge_products(outputs)
        assert len(products) == 1
        assert total == 2
        assert len(products[0].offers) == 2
        assert products[0].cheapest_price == 8.0

    def test_merge_case_insensitive(self):
        p1 = _make_product("product a", [10.0])
        p2 = _make_product("Product A", [8.0])
        outputs = [([p1], None), ([p2], None)]
        products, total = _merge_products(outputs)
        assert len(products) == 1

    def test_different_products_kept(self):
        p1 = _make_product("Product A", [10.0])
        p2 = _make_product("Product B", [8.0])
        outputs = [([p1], None), ([p2], None)]
        products, total = _merge_products(outputs)
        assert len(products) == 2

    def test_sorted_by_cheapest(self):
        p1 = _make_product("Expensive", [100.0])
        p2 = _make_product("Cheap", [5.0])
        outputs = [([p1, p2], None)]
        products, _ = _merge_products(outputs)
        assert products[0].name == "Cheap"
        assert products[1].name == "Expensive"

    def test_empty_outputs(self):
        outputs = [([], None)]
        products, total = _merge_products(outputs)
        assert len(products) == 0
        assert total == 0

    def test_enriches_metadata(self):
        p1 = ProductResult(name="Product A", offers=[])
        p2 = ProductResult(
            name="Product A",
            ean="1234567890128",
            brand="BrandX",
            image_url="https://img.example.com/a.jpg",
            offers=[],
        )
        outputs = [([p1], None), ([p2], None)]
        products, _ = _merge_products(outputs)
        assert len(products) == 1
        assert products[0].ean == "1234567890128"
        assert products[0].brand == "BrandX"
        assert products[0].image_url == "https://img.example.com/a.jpg"
