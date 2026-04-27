"""Phase R: quantity-aware tier-pricing match.

Tests the in-place rewrite that swaps an offer's listed unit price for
the bulk-tier price when the requested batch quantity meets a tier.
"""
from __future__ import annotations

from price_comparison_mcp.tools.batch_search import _apply_tier_pricing


def _offer(
    price: float,
    tiers: list[dict] | None = None,
    *,
    shipping: float = 0.0,
    total: float | None = None,
) -> dict:
    return {
        "merchant": "test-shop",
        "price": price,
        "shipping_cost": shipping,
        "total_price": total if total is not None else price + shipping,
        "currency": "EUR",
        "url": "https://example.com",
        "tier_pricing": tiers,
    }


class TestQuantityNoOp:
    def test_quantity_one_no_swap(self):
        """Quantity 1 must never swap -- listed price already reflects 1pc."""
        offers = [_offer(10.0, [{"min_qty": 1, "price": 10.0},
                                  {"min_qty": 50, "price": 7.5}])]
        _apply_tier_pricing(offers, 1)
        assert offers[0]["price"] == 10.0
        assert "bulk_price_applied" not in offers[0]

    def test_quantity_zero_no_swap(self):
        offers = [_offer(10.0, [{"min_qty": 1, "price": 10.0},
                                  {"min_qty": 50, "price": 7.5}])]
        _apply_tier_pricing(offers, 0)
        assert offers[0]["price"] == 10.0

    def test_no_tier_data_no_swap(self):
        offers = [_offer(10.0, None)]
        _apply_tier_pricing(offers, 100)
        assert offers[0]["price"] == 10.0
        assert "bulk_price_applied" not in offers[0]

    def test_empty_tier_list_no_swap(self):
        offers = [_offer(10.0, [])]
        _apply_tier_pricing(offers, 100)
        assert offers[0]["price"] == 10.0


class TestTierMatch:
    def test_quantity_meets_higher_tier(self):
        """Qty 100 hits the 50+ tier -> swap to 7.50."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 10, "price": 8.50},
            {"min_qty": 50, "price": 7.50},
            {"min_qty": 200, "price": 6.00},
        ])]
        _apply_tier_pricing(offers, 100)
        o = offers[0]
        assert o["price"] == 7.50
        assert o["unit_price_listed"] == 10.0
        assert o["unit_price_tier_min_qty"] == 50
        assert o["bulk_price_applied"] is True
        assert o["total_price"] == 7.50

    def test_quantity_below_first_tier_no_swap(self):
        """Qty 5 doesn't meet 10+ minimum -> stays at 1+ price."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 10, "price": 8.50},
        ])]
        _apply_tier_pricing(offers, 5)
        # min_qty 1 hits but tier price equals listed -> no-op
        assert offers[0]["price"] == 10.0
        assert "bulk_price_applied" not in offers[0]

    def test_quantity_at_exact_tier_boundary(self):
        """Qty exactly equal to tier min -> swap."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 10, "price": 8.50},
        ])]
        _apply_tier_pricing(offers, 10)
        assert offers[0]["price"] == 8.50

    def test_picks_highest_tier_below_quantity(self):
        """Qty 60 picks tier 50 (largest <=60), not tier 10."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 10, "price": 8.50},
            {"min_qty": 50, "price": 7.50},
        ])]
        _apply_tier_pricing(offers, 60)
        assert offers[0]["price"] == 7.50
        assert offers[0]["unit_price_tier_min_qty"] == 50

    def test_shipping_added_to_total_after_swap(self):
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 50, "price": 7.50},
        ], shipping=4.95)]
        _apply_tier_pricing(offers, 100)
        assert offers[0]["price"] == 7.50
        assert offers[0]["total_price"] == round(7.50 + 4.95, 2)


class TestRobustness:
    def test_handles_missing_min_qty(self):
        """Tier dict without min_qty is ignored, not crashed on."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"price": 7.50},  # broken
            {"min_qty": 50, "price": 7.50},
        ])]
        _apply_tier_pricing(offers, 100)
        assert offers[0]["price"] == 7.50

    def test_handles_string_min_qty(self):
        """min_qty as string is coerced to int."""
        offers = [_offer(10.0, [
            {"min_qty": "1", "price": 10.0},
            {"min_qty": "50", "price": 7.50},
        ])]
        _apply_tier_pricing(offers, 100)
        assert offers[0]["price"] == 7.50

    def test_skips_zero_or_negative_tier_price(self):
        """Tier with price <= 0 is bogus -> ignored."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 50, "price": 0.0},
        ])]
        _apply_tier_pricing(offers, 100)
        # Only tier-1 valid; tier-1 == listed -> no-op
        assert offers[0]["price"] == 10.0

    def test_ignores_higher_tier_price_than_listed(self):
        """Tier prices higher than listed are nonsensical -> never applied."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 50, "price": 12.0},  # bogus
        ])]
        _apply_tier_pricing(offers, 100)
        assert offers[0]["price"] == 10.0
        assert "bulk_price_applied" not in offers[0]

    def test_no_op_when_tier_price_equals_listed(self):
        """A 'discount' that's not actually a discount -> no swap."""
        offers = [_offer(10.0, [
            {"min_qty": 1, "price": 10.0},
            {"min_qty": 50, "price": 10.0},  # rounded equal
        ])]
        _apply_tier_pricing(offers, 100)
        assert offers[0]["price"] == 10.0


class TestBatchOfOffers:
    def test_only_offers_with_tiers_get_swapped(self):
        """Mixed batch: some offers have tiers, others don't."""
        offers = [
            _offer(10.0, [{"min_qty": 1, "price": 10.0},
                          {"min_qty": 50, "price": 7.50}]),
            _offer(11.0, None),  # no tiers
            _offer(9.0, [{"min_qty": 1, "price": 9.0},
                         {"min_qty": 100, "price": 8.0}]),
        ]
        _apply_tier_pricing(offers, 50)
        assert offers[0]["price"] == 7.50
        assert offers[1]["price"] == 11.0
        # offer 2 needs 100+ -> 50 not enough, listed kept
        assert offers[2]["price"] == 9.0
