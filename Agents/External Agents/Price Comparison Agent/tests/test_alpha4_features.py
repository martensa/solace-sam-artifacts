"""Tests for alpha4 features: price bounds + composite confidence + source propagation."""
from __future__ import annotations

import pytest

from price_comparison_mcp.categories.registry import CategoryRegistry
from price_comparison_mcp.price_extractor import ExtractedOffer
from price_comparison_mcp.tools.search_prices import (
    _composite_confidence,
    _flag_outliers,
    _score_url,
)


# -----------------------------------------------------------------------------
# Composite confidence
# -----------------------------------------------------------------------------


class TestCompositeConfidence:
    @pytest.mark.parametrize(
        "mc,src,min_val,max_val",
        [
            ("exact", "json_ld",     0.99, 1.01),   # best case
            ("high",  "json_ld",     0.88, 0.92),
            ("high",  "css_site",    0.72, 0.78),
            ("medium","css_generic", 0.40, 0.44),
            ("low",   "regex",       0.15, 0.17),   # worst realistic case
            ("",      "",            0.44, 0.46),   # defaults
        ],
    )
    def test_ranges(self, mc, src, min_val, max_val):
        score = _composite_confidence(mc, src)
        assert min_val <= score <= max_val, (
            f"({mc},{src}) -> {score} outside [{min_val}, {max_val}]"
        )

    def test_monotonic_in_match_conf(self):
        """Holding source fixed, higher mc => higher composite."""
        prev = -1.0
        for mc in ["low", "medium", "high", "exact"]:
            s = _composite_confidence(mc, "json_ld")
            assert s > prev
            prev = s

    def test_monotonic_in_source(self):
        """Holding mc fixed, better source => higher composite."""
        prev = -1.0
        for src in ["regex", "css_generic", "css_site", "microdata", "json_ld"]:
            s = _composite_confidence("high", src)
            assert s > prev
            prev = s


# -----------------------------------------------------------------------------
# ExtractedOffer carries price_source
# -----------------------------------------------------------------------------


class TestExtractedOfferSource:
    def test_default_empty(self):
        o = ExtractedOffer(merchant="x", price=10.0)
        assert o.price_source == ""

    def test_set_and_serialize(self):
        o = ExtractedOffer(
            merchant="idealo", price=99.0,
            price_source="json_ld",
        )
        d = o.to_dict()
        assert d["price_source"] == "json_ld"


# -----------------------------------------------------------------------------
# Per-category price bounds
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    CategoryRegistry.reset()
    return CategoryRegistry.instance()


class TestPriceBands:
    def _make_offers(self, prices: list[float]) -> list[dict]:
        # Minimal shape _flag_outliers needs
        return [
            {
                "merchant": f"m{i}",
                "total_price": p,
                "match_confidence": "high",
            }
            for i, p in enumerate(prices)
        ]

    def test_book_band_rejects_expensive_book(self, registry):
        """Book band is [0.50, 500] -- a 1200 EUR "book" is an outlier."""
        book = registry.get("book_media")
        offers = self._make_offers([20, 25, 28, 30, 1200])
        flagged, _ = _flag_outliers(offers, profile=book)
        # Only the 1200 entry should be flagged as outlier
        outliers = [o for o in offers if o.get("is_outlier")]
        assert len(outliers) == 1
        assert outliers[0]["total_price"] == 1200
        # Reason mentions the category name
        assert "price band" in outliers[0]["outlier_reason"].lower()

    def test_wine_band_accepts_premium_wine(self, registry):
        """Wine band is [0.50, 2000] -- 1500 EUR Premium is NOT an outlier
        on the band axis (but statistical tests may flag it)."""
        food = registry.get("food_beverage")
        offers = self._make_offers([10, 12, 15, 20, 1500])
        flagged, _ = _flag_outliers(offers, profile=food)
        # 1500 is INSIDE the food_beverage band (0.50-2000) but
        # statistical outlier -- still flagged, but reason is NOT band.
        if offers[-1].get("is_outlier"):
            assert "price band" not in (offers[-1]["outlier_reason"] or "").lower()

    def test_no_profile_no_band_check(self):
        """Without profile, band is never invoked -- behaviour stable."""
        offers = self._make_offers([20, 25, 28, 30, 1200])
        _flag_outliers(offers, profile=None)
        # 1200 may still be flagged by statistical tests but reason
        # won't mention "price band"
        outliers = [o for o in offers if o.get("is_outlier")]
        for o in outliers:
            assert "price band" not in (o.get("outlier_reason") or "").lower()

    def test_band_check_works_with_few_offers(self, registry):
        """Band is a hard bound -- fires even with < 4 offers."""
        book = registry.get("book_media")
        offers = self._make_offers([20, 25, 1500])  # only 3 offers
        _flag_outliers(offers, profile=book)
        # Statistical tests need 4+ offers, but band does not
        outliers = [o for o in offers if o.get("is_outlier")]
        assert len(outliers) == 1
        assert outliers[0]["total_price"] == 1500


# -----------------------------------------------------------------------------
# Variant detectors reach _score_url via profile
# -----------------------------------------------------------------------------


class TestVariantDetectorsInScoring:
    def test_fashion_size_demotes_wrong_size(self, registry):
        """fashion_size_detector fires in _score_url when profile=fashion_apparel."""
        fashion = registry.get("fashion_apparel")
        correct = _score_url(
            "https://www.zalando.de/nike-air-max-42-schwarz",
            "Nike Air Max 42 schwarz",
            profile=fashion,
        )
        wrong = _score_url(
            "https://www.zalando.de/nike-air-max-44-schwarz",
            "Nike Air Max 42 schwarz",
            profile=fashion,
        )
        assert correct > wrong

    def test_wine_vintage_demotes_wrong_year(self, registry):
        food = registry.get("food_beverage")
        correct = _score_url(
            "https://vinatis.de/chateau-lafite-2015",
            "Chateau Lafite 2015",
            profile=food,
        )
        wrong = _score_url(
            "https://vinatis.de/chateau-lafite-2018",
            "Chateau Lafite 2015",
            profile=food,
        )
        assert correct > wrong

    def test_automotive_oem_demotes_wrong_generation(self, registry):
        auto = registry.get("automotive")
        correct = _score_url(
            "https://autoteiledirekt.de/bmw-e46-bremsscheibe",
            "Bremsscheibe BMW E46",
            profile=auto,
        )
        wrong = _score_url(
            "https://autoteiledirekt.de/bmw-e90-bremsscheibe",
            "Bremsscheibe BMW E46",
            profile=auto,
        )
        assert correct > wrong

    def test_default_profile_no_variant_effect(self):
        """profile=None or default should NOT apply variant detectors."""
        no_profile = _score_url(
            "https://autoteiledirekt.de/bmw-e90-bremsscheibe",
            "Bremsscheibe BMW E46",
        )
        CategoryRegistry.reset()
        default = CategoryRegistry.instance().get("default")
        with_default = _score_url(
            "https://autoteiledirekt.de/bmw-e90-bremsscheibe",
            "Bremsscheibe BMW E46",
            profile=default,
        )
        assert no_profile == with_default  # equivalent
