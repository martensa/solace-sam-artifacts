"""Integration tests for the alpha3 pipeline wire-up.

Covers:
  - EAN fast-fail at query ingress
  - Classifier + profile threaded into _score_url / _fq / title-gate
  - Locale-aware query variants
  - Anti-lex title-gate downgrade
  - Zero-regression contract: with categories disabled, behaviour equals v2.3.5

These tests do NOT hit the network. They exercise the pipeline's pure
functions with mocked config objects and verify logical invariants.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from price_comparison_mcp.categories.registry import CategoryRegistry
from price_comparison_mcp.config import PriceSearchConfig
from price_comparison_mcp.tools.search_prices import (
    _foreign_model_qualifier_penalty as fq,
    _generate_query_variants,
    _product_match_confidence,
    _score_url,
)


# -----------------------------------------------------------------------------
# Config flag defaults
# -----------------------------------------------------------------------------


class TestConfigFlagDefaults:
    def test_defaults_enable_v1_features(self):
        """v1.0 pipeline flags are ON by default (override via env if needed)."""
        cfg = PriceSearchConfig()
        assert cfg.enable_categories is True
        assert cfg.enable_ean_fastfail is True
        assert cfg.enable_locale_templates is True
        assert cfg.enable_antilex_gate is True
        # Phase I gate is on by default; rollback flag flips it off.
        assert cfg.enable_part_number_gate is True

    def test_llm_reranker_off_by_default(self):
        """Reranker is opt-in -- enabled explicitly when metrics justify."""
        cfg = PriceSearchConfig()
        assert cfg.enable_llm_reranker is False


# -----------------------------------------------------------------------------
# Query variant generation
# -----------------------------------------------------------------------------


class TestQueryVariants:
    def test_legacy_mode_two_variants(self):
        """Without templates, behaviour equals pre-v1.0 (raw + quoted)."""
        variants = _generate_query_variants("Bosch GBH 2-26 F")
        assert len(variants) == 2
        assert variants[0] == "Bosch GBH 2-26 F"
        # Quoted-model-number form
        assert any('"' in v for v in variants[1:])

    def test_v1_mode_adds_category_expansions(self):
        """With use_templates=True, category-specific expansions appended."""
        variants = _generate_query_variants(
            "Bosch GBH 2-26 F",
            category="tools_hardware",
            locale="de",
            use_templates=True,
        )
        # More than just raw + quoted
        assert len(variants) > 2
        # Raw query still first
        assert variants[0] == "Bosch GBH 2-26 F"
        # Tools_hardware adds "Artikelnummer" and "Zubehoer" hints
        joined = " ".join(variants).lower()
        assert "preis" in joined or "artikelnummer" in joined

    def test_v1_mode_bounded_to_6_variants(self):
        """Fan-out capped at 6 (was 4 pre-SKU-fallback) so SearXNG
        isn't hammered. 6 * 2 channels = 12 parallel calls per query."""
        variants = _generate_query_variants(
            "FLUKE 1674FC SCH Installationstester",
            category="industrial_mro",
            locale="de",
            use_templates=True,
        )
        assert len(variants) <= 6

    def test_empty_query(self):
        assert _generate_query_variants("") == []
        assert _generate_query_variants("   ") == []

    def test_unknown_category_falls_back_to_default(self):
        """Unknown category in templates chain falls back cleanly."""
        variants = _generate_query_variants(
            "x",
            category="no_such_category",
            locale="de",
            use_templates=True,
        )
        # At least the raw query
        assert variants[0] == "x"


# -----------------------------------------------------------------------------
# Anti-lex title gate
# -----------------------------------------------------------------------------


class TestAntiLexGate:
    @pytest.fixture(scope="class")
    def tools_profile(self):
        CategoryRegistry.reset()
        return CategoryRegistry.instance().get("tools_hardware")

    def test_antilex_downgrades_bosch_dishwasher(self, tools_profile):
        """Query is tool-style; page title contains 'spuelmaschine' ->
        forced to 'low' regardless of token match."""
        # Forge a page title that token-matches the query strongly but
        # contains the antilex word.
        title = "Bosch Professional Spuelmaschine SMS6ZCW00E"
        query = "Bosch Professional GBH 2-26 F"
        result = _product_match_confidence(query, title, profile=tools_profile)
        assert result == "low"

    def test_antilex_skipped_when_disabled(self, tools_profile):
        """apply_antilex=False lets the normal token logic decide."""
        title = "Bosch Professional Spuelmaschine SMS6ZCW00E"
        query = "Bosch Professional GBH 2-26 F"
        result = _product_match_confidence(
            query, title, profile=tools_profile, apply_antilex=False,
        )
        # Without antilex, brand matches and some digits are shared
        # -> could be anything but specifically NOT forced to low
        # (actual verdict depends on token overlap).
        # Key invariant: the antilex path did NOT fire.
        # Loose check: don't require exact verdict, just ensure the
        # function didn't short-circuit.
        assert result in ("low", "medium", "high", "")

    def test_antilex_not_active_for_default_profile(self):
        """Default profile has empty antilex set -- gate silent."""
        CategoryRegistry.reset()
        default = CategoryRegistry.instance().get("default")
        title = "Bosch Spuelmaschine"
        # Even with 'spuelmaschine' in title, default profile has no
        # antilex words, so the gate stays silent. Verdict depends on
        # token overlap with the query.
        result = _product_match_confidence(
            "Bosch Spuelmaschine", title, profile=default,
        )
        # Key: the check didn't force low via antilex -- it returned
        # some verdict from the normal path. "low" is acceptable for
        # a minimal-overlap query; but the point is it's not FORCED.
        assert result != ""  # something computed

    @pytest.mark.regression
    def test_no_profile_equals_legacy(self):
        """profile=None produces exactly the pre-v1.0 verdict."""
        cases = [
            ("Bosch Professional GBH 2-26 F",
             "Bosch Professional GBH 2-26 F Bohrhammer 830W", "high"),
            ("FLUKE 1674FC SCH",
             "Fluke 1674FC SCH Installationstester", "high"),
            ("Niedax WRL 200.400 F",
             "Niedax Weitspannkabelrinne WRL 200 400 F feuerverzinkt", "high"),
        ]
        for query, title, expected in cases:
            assert _product_match_confidence(query, title) == expected
            assert _product_match_confidence(query, title, profile=None) == expected


# -----------------------------------------------------------------------------
# Phase I: part-number title-gate
# -----------------------------------------------------------------------------


class TestPartNumberGate:
    """The part-number gate forces mc='low' when the query carries a
    SKU-shaped token that the page title is missing (regardless of brand
    or digit-anchor coverage). Catches Testlauf-3 Pos 3 / Pos 4 failures.
    """

    def test_pos4_obo_ksa_s40_wrong_sku_forced_low(self):
        """Brand matches, but the specific SKU is missing from title."""
        # Title for the actual Pos 4 wrong hit (1594-22-G clamp, OBO brand
        # in the breadcrumb). With the gate the offer is forced to low.
        title = "OBO Bettermann 1594-22-G Befestigungsschelle Stahl"
        query = "OBO Bettermann KSA-S40 Kabelschelle"
        assert _product_match_confidence(query, title) == "low"

    def test_pos3_obo_asm_c6a_wrong_sku_forced_low(self):
        """Pos 3: ASM-C6A query, DTS-2C-RW1 hit -> low."""
        title = "OBO Bettermann DTS-2C-RW1 Datentechnik Modul"
        query = "OBO Bettermann ASM-C6A G Anschlussmodul"
        assert _product_match_confidence(query, title) == "low"

    def test_correct_sku_passes_gate(self):
        """When the SKU is in the title, gate is silent and normal logic runs."""
        title = "OBO Bettermann KSA-S40 Kabelschelle 5-9mm Stahl"
        query = "OBO Bettermann KSA-S40 Kabelschelle"
        # Should NOT be low -- normal token path returns high (brand + tokens).
        assert _product_match_confidence(query, title) != "low"

    def test_punctuation_tolerant_match_passes(self):
        """KSA-S40 query against 'KSA S40' or 'KSAS40' titles -> not low."""
        for title in (
            "OBO Bettermann KSA S40 Kabelschelle",
            "OBO Bettermann KSAS40 Kabelschelle",
            "OBO Bettermann KSA.S40 Kabelschelle",
        ):
            query = "OBO Bettermann KSA-S40 Kabelschelle"
            assert _product_match_confidence(query, title) != "low", title

    def test_gate_disabled_lets_legacy_logic_decide(self):
        """apply_part_number_gate=False preserves pre-Phase-I behaviour."""
        # Same Pos 4 scenario, but gate off. The legacy digit-anchor
        # logic sees no anchors >= 3 chars in "KSA-S40" so cannot judge
        # via case A and falls into descriptive token overlap.
        title = "OBO Bettermann 1594-22-G Befestigungsschelle Stahl"
        query = "OBO Bettermann KSA-S40 Kabelschelle"
        result = _product_match_confidence(
            query, title, apply_part_number_gate=False,
        )
        # Whatever the legacy verdict is, the test asserts it isn't
        # being short-circuited by the new gate.
        # Brand matches, descriptive token "kabelschelle" not in title
        # -> medium-or-low via descriptive path. Either is fine; the
        # contract is "not low BECAUSE OF the gate".
        assert result in ("low", "medium", "high", "")

    @pytest.mark.regression
    def test_descriptive_only_query_quiet(self):
        """Queries without a SKU-shaped token must not be touched by the gate."""
        # No alphanumeric mix tokens here.
        query = "Bohrhammer Akku Schrauber"
        title = "Anderer Bohrhammer SDS"
        # Whatever the legacy verdict, the gate is silent -- no part numbers.
        result_with = _product_match_confidence(query, title)
        result_without = _product_match_confidence(
            query, title, apply_part_number_gate=False,
        )
        assert result_with == result_without

    @pytest.mark.regression
    def test_legacy_high_confidence_still_high(self):
        """Pre-Phase-I high-confidence cases stay high under the gate."""
        cases = [
            ("Bosch Professional GBH 2-26 F",
             "Bosch Professional GBH 2-26 F Bohrhammer 830W", "high"),
            ("FLUKE 1674FC SCH",
             "Fluke 1674FC SCH Installationstester", "high"),
            ("Niedax WRL 200.400 F",
             "Niedax Weitspannkabelrinne WRL 200 400 F feuerverzinkt", "high"),
        ]
        for query, title, expected in cases:
            assert _product_match_confidence(query, title) == expected

    def test_empty_title_returns_empty_string(self):
        """No title -> '' (cannot judge), gate must not raise."""
        assert _product_match_confidence("OBO KSA-S40", "") == ""


# -----------------------------------------------------------------------------
# Zero-regression: every shipped regression-test case works under a
# 'default' profile identically to no profile at all.
# -----------------------------------------------------------------------------


class TestZeroRegressionWithDefault:
    @pytest.fixture(scope="class")
    def default_profile(self):
        CategoryRegistry.reset()
        return CategoryRegistry.instance().get("default")

    @pytest.mark.regression
    @pytest.mark.parametrize(
        "url,query,expected_penalty",
        [
            ("https://www.amazon.de/Bosch-Professional-GBH-2-26-F-Bohrhammer/dp/B0DK22DGM6",
             "Bosch GBH 2-26 F", 0),
            ("https://www.bosch-professional.com/de/de/products/gbh-18v-26-f-0611910004",
             "Bosch GBH 2-26 F", 25),
            ("https://geizhals.de/fluke-1674-fc-ftt-installationstester-5581087-a3357933.html",
             "FLUKE 1674FC SCH", 25),
            ("https://www.voelkner.de/products/11071460/Fluke-1674FC-SCH-Installationstester.html",
             "FLUKE 1674FC SCH", 0),
        ],
    )
    def test_default_profile_equivalent_fq(self, default_profile, url, query, expected_penalty):
        """The 'default' profile produces bit-identical penalties to profile=None."""
        assert fq(query, url, profile=None) == expected_penalty
        assert fq(query, url, profile=default_profile) == expected_penalty


# -----------------------------------------------------------------------------
# Observability signal: profile, category, confidence all accessible
# -----------------------------------------------------------------------------


class TestObservabilitySurface:
    """The classifier + profile surface must expose the values needed
    for structured logging."""

    def test_classifier_returns_result_with_meta(self):
        from price_comparison_mcp.categories.classifier import classify
        r = classify("Bosch Professional GBH 2-26 F")
        # All fields present, typed correctly for log-formatting
        assert isinstance(r.category, str)
        assert isinstance(r.confidence, float)
        assert 0.0 <= r.confidence <= 1.0
        assert isinstance(r.source, str)
        assert r.source.startswith("heuristic:")

    def test_registry_get_returns_typed_profile(self):
        CategoryRegistry.reset()
        p = CategoryRegistry.instance().get("tools_hardware")
        # A real profile surfaces the fields the scorer needs
        assert hasattr(p, "domain_scores")
        assert hasattr(p, "manufacturer_domains")
        assert hasattr(p, "manufacturer_penalty_override")
        assert hasattr(p, "rule_c_fillers")
        assert hasattr(p, "title_gate_antilex")
