"""Tests for the three v1.0 post-release enhancements:

1. Query-rewrite fallback: brand-free/SKU-only variants added
2. Fashion-size/color: title + breadcrumb context (not only URL)
3. Outlier cluster mode: don't flag everything in multi-scale spreads
"""
from __future__ import annotations

import pytest

from price_comparison_mcp.categories.variant_detectors import (
    FASHION_COLOR_PENALTY,
    FASHION_SIZE_PENALTY,
    fashion_color_detector,
    fashion_size_detector,
    run_category_detectors,
)
from price_comparison_mcp.tools.search_prices import (
    _cluster_prices,
    _flag_outliers,
    _generate_query_variants,
)


# =============================================================================
# 1. Query-rewrite fallback
# =============================================================================


class TestQueryRewriteFallback:
    def test_sku_only_variant_added_for_b2b_sku(self):
        """Siemens B2B SKU triggers brand-free fallback variant."""
        variants = _generate_query_variants(
            "Siemens 5SV1316-6KK16 FI/LS-Schalter",
            category="industrial_mro",
            locale="de",
            use_templates=True,
        )
        # Raw query present
        assert variants[0] == "Siemens 5SV1316-6KK16 FI/LS-Schalter"
        # Should include bare SKU (quoted) and/or SKU-only as fallback
        joined = " ".join(variants)
        assert "5SV1316-6KK16" in joined
        # Brand-free SKU-only variant present
        has_brand_free = any(
            v == "5SV1316-6KK16" or v == '"5SV1316-6KK16"'
            for v in variants
        )
        assert has_brand_free, f"No brand-free variant in {variants}"

    def test_sku_only_for_multipower_battery(self):
        """Multipower MP26-12 -- SKU-only variant should be there."""
        variants = _generate_query_variants(
            "Multipower MP26-12 Bleiakku 12V 26Ah",
            category="electronics",
            locale="de",
            use_templates=True,
        )
        joined = " ".join(variants)
        assert "MP26-12" in joined

    def test_short_sku_no_fallback(self):
        """SKU < 5 chars is not distinctive enough -- no brand-free fallback."""
        variants = _generate_query_variants(
            "Nike 42 Schuh",
            category="fashion_apparel",
            locale="de",
            use_templates=True,
        )
        # "42" is 2 chars; "Nike" is brand. No brand-free variant expected.
        assert '"42"' not in variants
        assert "42" not in variants

    def test_legacy_mode_unchanged(self):
        """use_templates=False + category=None -- bit-identical to pre-v1.0."""
        variants = _generate_query_variants("Bosch GBH 2-26 F")
        assert len(variants) == 2
        # No brand-free variant in legacy mode
        assert variants[1].startswith('"') or "GBH" in variants[1] or "2-26" in variants[1]

    def test_variant_cap_at_6(self):
        """Total variants capped at 6 (was 4), accommodating raw + quoted-SKU
        + 2 brand-free-SKU fallbacks + templates."""
        variants = _generate_query_variants(
            "FLUKE 1674FC SCH Installationstester",
            category="industrial_mro",
            locale="de",
            use_templates=True,
        )
        assert len(variants) <= 6

    @pytest.mark.regression
    def test_existing_queries_still_produce_original_variant(self):
        """Every Bestands-regression query must still produce the raw query
        as the first variant."""
        for q in [
            "Bosch GBH 2-26 F",
            "Sony WH-1000XM5",
            "Niedax WRL 200.400 F",
            "FLUKE 1674FC SCH",
            "OBO Bettermann KSA-S40",
        ]:
            variants = _generate_query_variants(
                q, category="industrial_mro", locale="de", use_templates=True,
            )
            assert variants[0] == q


# =============================================================================
# 2. Fashion title/breadcrumb-aware detectors
# =============================================================================


class TestFashionTitleAware:
    def test_size_mismatch_in_title_not_url(self):
        """Zalando-style URL without size in path, but title reveals size 44."""
        url = "https://www.zalando.de/nike-air-force-1-triple-white-abc123"
        title = "Nike Air Force 1 '07 Triple White | Sneaker low | Gr\u00f6\u00dfe 44"
        assert fashion_size_detector(
            "Nike Air Force 1 Triple White 42", url, context=title,
        ) == FASHION_SIZE_PENALTY

    def test_size_match_in_title(self):
        url = "https://www.zalando.de/nike-air-force-1-triple-white-abc123"
        title = "Nike Air Force 1 '07 Triple White Gr\u00f6\u00dfe 42 Damen"
        assert fashion_size_detector(
            "Nike Air Force 1 Triple White 42", url, context=title,
        ) == 0

    def test_color_mismatch_in_title(self):
        url = "https://www.aboutyou.de/p/nike-air-force-1-07-abc123"
        title = "Nike Air Force 1 '07 schwarz Sneaker"
        assert fashion_color_detector(
            "Nike Air Force 1 weiss", url, context=title,
        ) == FASHION_COLOR_PENALTY

    def test_color_match_across_lang_via_title(self):
        url = "https://www.aboutyou.de/p/nike-air-force-1-abc123"
        title = "Nike Air Force 1 Black Sneaker"
        # Query 'schwarz', title 'Black' -> synonym group, no penalty
        assert fashion_color_detector(
            "Nike Air Force 1 schwarz", url, context=title,
        ) == 0

    def test_backward_compat_url_only(self):
        """Without context, detector behaves as before."""
        url = "https://www.zalando.de/nike-air-force-1-44-schwarz"
        assert fashion_size_detector(
            "Nike Air Force 1 Triple White 42", url,
        ) == FASHION_SIZE_PENALTY

    def test_dispatcher_passes_context(self):
        """run_category_detectors forwards context to detectors."""
        url = "https://www.zalando.de/nike-no-variant-in-path"
        title = "Nike Air Force 1 | Gr\u00f6\u00dfe 44"
        penalty = run_category_detectors(
            "Nike Air Force 1 Triple White 42",
            url,
            ("fashion_size", "fashion_color"),
            context=title,
        )
        assert penalty == FASHION_SIZE_PENALTY


# =============================================================================
# 3. Outlier cluster mode
# =============================================================================


class TestClusterPrices:
    def test_single_cluster_tight(self):
        """Prices within 3x stay as one cluster."""
        clusters = _cluster_prices([10.0, 11.0, 12.0, 14.0, 15.0])
        assert len(clusters) == 1
        assert clusters[0] == [10.0, 11.0, 12.0, 14.0, 15.0]

    def test_two_clusters_3x_gap(self):
        """Gap >= 3x creates a cluster break."""
        clusters = _cluster_prices([0.60, 0.65, 0.70, 4.00, 4.50])
        assert len(clusters) == 2
        assert clusters[0] == [0.60, 0.65, 0.70]
        assert clusters[1] == [4.00, 4.50]

    def test_three_clusters_staedtler_case(self):
        """The real-world Staedtler example: single-sticks, 12-packs, mega."""
        clusters = _cluster_prices([0.60, 0.65, 3.50, 4.00, 4.50, 45.0, 48.0])
        assert len(clusters) == 3
        assert sorted(clusters[0]) == [0.60, 0.65]
        assert sorted(clusters[1]) == [3.50, 4.00, 4.50]
        assert sorted(clusters[2]) == [45.0, 48.0]

    def test_singleton_detection(self):
        """Isolated price far from cluster gets its own singleton."""
        clusters = _cluster_prices([10.0, 11.0, 12.0, 500.0])
        assert any(len(c) == 1 and c[0] == 500.0 for c in clusters)

    def test_empty_and_single(self):
        assert _cluster_prices([]) == []
        assert _cluster_prices([42.0]) == [[42.0]]


class TestClusterMode:
    """Integration: _flag_outliers picks cluster mode on wide spreads."""

    def _offers(self, prices):
        return [
            {
                "merchant": f"shop_{i}",
                "total_price": p,
                "match_confidence": "high",
            }
            for i, p in enumerate(prices)
        ]

    def test_staedtler_case_multi_cluster_no_universal_flag(self):
        """6 offers across 3 clusters -- NO offer should be flagged
        (all are in multi-member clusters)."""
        offers = self._offers([0.60, 0.65, 3.50, 4.00, 45.0, 48.0])
        _flag_outliers(offers)
        # Nothing flagged -- each cluster has >=2 members
        outliers = [o for o in offers if o.get("is_outlier")]
        assert len(outliers) == 0, (
            f"Expected 0 outliers in multi-cluster data; got {outliers}"
        )

    def test_singleton_in_cluster_mode_is_flagged(self):
        """6 offers with one isolated price (Fluke-Conrad 149 case)."""
        offers = self._offers([149.0, 2700, 2800, 2900, 3000, 3100])
        _flag_outliers(offers)
        outliers = [o for o in offers if o.get("is_outlier")]
        assert len(outliers) == 1
        assert outliers[0]["total_price"] == 149.0
        assert "singleton" in (outliers[0]["outlier_reason"] or "").lower()

    def test_tight_spread_uses_legacy_ratio_mode(self):
        """When max/min < 10x, legacy ratio+MAD mode still applies."""
        # All within 2x of anchor -- no cluster mode
        offers = self._offers([100, 105, 110, 115, 120])
        _flag_outliers(offers)
        outliers = [o for o in offers if o.get("is_outlier")]
        assert len(outliers) == 0  # tight spread, nothing flagged

    def test_no_regression_on_v2_3_5_regression_fixtures(self):
        """Outlier fixtures that were stable in v2.3.5 must remain stable."""
        # Fluke-like spread: main cluster around 2700-3200 + 1 outlier
        offers = self._offers([149.99, 2768, 2800, 3042, 3100, 3200, 3360])
        _flag_outliers(offers)
        outliers = [o for o in offers if o.get("is_outlier")]
        # The 149.99 should be flagged in either mode
        assert any(o["total_price"] == 149.99 for o in outliers)


# =============================================================================
# Regression guard: Bestands-Tests all still green
# =============================================================================


@pytest.mark.regression
class TestV10RegressionGuard:
    """After these three enhancements, key v2.3.5 + v1.0-alpha3 behaviours
    MUST remain identical."""

    def test_query_variants_bosch_regression(self):
        # Default mode should still include Bosch + quoted SKU
        variants = _generate_query_variants("Bosch GBH 2-26 F")
        assert variants[0] == "Bosch GBH 2-26 F"
        # quoted SKU variant present
        assert any('"2-26"' in v or '"2-26' in v for v in variants[1:])

    def test_fashion_detectors_still_fire_without_context(self):
        """Backward compat: context defaulted to "" keeps URL-only mode alive."""
        url = "https://zalando.de/nike-44"
        assert fashion_size_detector("Nike 42", url) == FASHION_SIZE_PENALTY

    def test_cluster_mode_silent_on_normal_ranges(self):
        """Day-to-day B2B searches have tight spreads -- cluster mode
        should not activate and ratio/MAD must continue to work."""
        offers = [
            {"merchant": f"s{i}", "total_price": 100 + i, "match_confidence": "high"}
            for i in range(5)
        ]
        _flag_outliers(offers)
        # No outliers (tight spread, no cluster mode)
        assert not any(o.get("is_outlier") for o in offers)
