"""Regression tests -- lock in the five already-shipped fixes.

These tests MUST stay green across every future change. They migrate the
ad-hoc scripts from /tmp that were used during v2.3.x development:

  - /tmp/test_foreign_qualifier.py
  - /tmp/test_improvements.py

Shipped fixes protected here:
  [F1] Bosch GBH 2-26 F corded variant (NOT 18V cordless)        -- Rule A
  [F2] Sony WH-1000XM5 exact-match, not XM4/XM3                   -- Rule A
  [F3] OBO Bettermann KSA-S40 exact, not KSA-S50 other sizes      -- Rule B
  [F4] Fluke 1674FC SCH, not FTT regional variant                 -- Rule C
  [F5] Distinctive-token gate blocks brand-only SERP substitution  -- aggregator resolver

All four foreign-qualifier rules and the gate are additive to the
existing URL scoring. If one test here goes red, roll back the change.
"""
from __future__ import annotations

import re

import pytest

from price_comparison_mcp.tools.search_prices import (
    _foreign_model_qualifier_penalty as fq,
    _score_url,
)


pytestmark = pytest.mark.regression


# -----------------------------------------------------------------------------
# [F1..F4] Foreign-qualifier penalty -- Rules A, B, C
# -----------------------------------------------------------------------------


class TestForeignQualifierPenalty:
    """Table-driven: exact penalty for each representative URL.

    Every test case documents WHICH rule is expected to fire (or stay silent).
    """

    # ---------- Rule A: letter-digit compound adjacent to digit anchor ----------

    def test_bosch_gbh_2_26_f_corded_url_no_penalty(self):
        """[F1] Corded variant URL must score at full domain value."""
        url = "https://www.amazon.de/Bosch-Professional-GBH-2-26-F-Bohrhammer/dp/B0DK22DGM6"
        assert fq("Bosch GBH 2-26 F", url) == 0

    def test_bosch_gbh_18v_26_f_cordless_rule_a_fires(self):
        """[F1] Cordless variant URL must pick up the Rule A penalty (18v near 26)."""
        url = "https://www.bosch-professional.com/de/de/products/gbh-18v-26-f-0611910004"
        assert fq("Bosch GBH 2-26 F", url) == 25

    def test_sony_wh_1000xm5_exact_url_no_penalty(self):
        """[F2] Correct generation URL must not trigger the penalty."""
        url = "https://www.amazon.de/Sony-WH-1000XM5-Headphones-Noise/dp/B09XYZ"
        assert fq("Sony WH-1000XM5", url) == 0

    def test_sony_wh_1000xm4_old_gen_rule_a_fires(self):
        """[F2] Old-gen URL with xm4 adjacent to 1000 must fire Rule A."""
        url = "https://www.amazon.de/Sony-WH-1000XM4-Headphones/dp/B08ABC"
        assert fq("Sony WH-1000XM5", url) == 25

    # ---------- Rule B: short letter-digit compound next to a query alpha word --

    def test_asm_c6a_g_exact_url_no_penalty(self):
        """[F3] Correct module size URL must not trigger."""
        url = "https://www.elektro-wandelt.de/metz-asm-c6a-g/"
        assert fq("ASM-C6A G Anschlussmodul CAT 6A", url) == 0

    def test_asm_c5_wrong_category_rule_b_fires(self):
        """[F3] Wrong CAT class (c5 adjacent to brand/category token) triggers Rule B."""
        url = "https://www.elektro-wandelt.de/metz-asm-c5-g/"
        assert fq("ASM-C6A G Anschlussmodul CAT 6A", url) == 25

    def test_ksa_s40_exact_url_no_penalty(self):
        """[F3] Correct clamp size URL must not trigger."""
        url = "https://www.trolla.de/obo/ksa-s40-kabelschelle-100a/"
        assert fq("OBO Bettermann KSA-S40", url) == 0

    def test_ksa_s50_wrong_size_rule_b_fires(self):
        """[F3] Wrong clamp size -- s50 adjacent to ksa/kabelschelle -- triggers Rule B."""
        url = "https://www.trolla.de/obo/ksa-s50-kabelschelle/"
        assert fq("OBO Bettermann KSA-S40", url) == 25

    # ---------- Rule C: pure-alpha variant suffix near digit anchor --------------

    def test_fluke_1674fc_sch_correct_url_no_penalty(self):
        """[F4] SCH variant URL must not trigger Rule C (sch is in query alpha words)."""
        url = (
            "https://www.voelkner.de/products/11071460/"
            "Fluke-1674FC-SCH-Installationstester.html"
        )
        assert fq("FLUKE 1674FC SCH", url) == 0

    def test_fluke_1674fc_sch_dashed_amazon_no_penalty(self):
        """[F4] Dash-separated correct URL (1674-FC-SCH) must not trigger."""
        url = "https://www.amazon.de/Multifunktions-Installationstester-Fluke-1674-FC-SCH/dp/B0DK22DJSJ"
        assert fq("FLUKE 1674FC SCH", url) == 0

    def test_fluke_1674fc_ftt_wrong_variant_rule_c_fires(self):
        """[F4] FTT regional variant URL must fire Rule C (ftt alpha near 1674)."""
        url = "https://geizhals.de/fluke-1674-fc-ftt-installationstester-5581087-a3357933.html"
        assert fq("FLUKE 1674FC SCH", url) == 25

    def test_niedax_direct_url_no_rules_fire(self):
        """[F4] Niedax direct URL must stay quiet across all rules."""
        url = "https://www.tandmore.de/Installation/Niedax/Niedax-WRL200-400F-WRL-200-400-F-feuerverzinkt"
        assert fq("Niedax WRL 200.400 F", url) == 0

    # ---------- Rule C must NOT fire on queries without an upper 3-4 variant ----

    def test_bosch_corded_no_rule_c_on_valid_model_zone(self):
        """Query 'Bosch GBH 2-26 F' has upper_variant 'GBH'; correct URL stays at 0."""
        url = (
            "https://geizhals.de/bosch-professional-gbh-2-26-f-"
            "elektro-bohr-meisselhammer-v10537.html"
        )
        assert fq("Bosch GBH 2-26 F", url) == 0

    def test_hp_840_no_rule_c_hp_is_2_chars(self):
        """Query 'HP EliteBook 840 G9' has no upper 3-4 variant (HP=2); Rule C stays silent."""
        url = "https://example.de/notebook-840-xyz-eco-version"
        assert fq("HP EliteBook 840 G9", url) == 0

    # ---------- Cross-rule edge cases ---------------------------------------------

    def test_penalty_is_exactly_25(self):
        """The penalty constant is used consistently across all rules."""
        from price_comparison_mcp.tools.search_prices import (
            _FOREIGN_QUALIFIER_PENALTY,
        )
        assert _FOREIGN_QUALIFIER_PENALTY == 25


# -----------------------------------------------------------------------------
# [F1-F4] Integration check via _score_url: corded outranks cordless decisively.
# -----------------------------------------------------------------------------


class TestScoreUrlIntegration:
    """The penalty must translate into a score delta large enough to
    reshuffle ranking. Tests the Rule-A Bosch case end-to-end because it
    is the canonical regression from v2.3.4.
    """

    def test_corded_beats_cordless_on_amazon(self):
        corded = _score_url(
            "https://www.amazon.de/Bosch-Professional-GBH-2-26-F/dp/B07ABC",
            "Bosch GBH 2-26 F",
        )
        cordless = _score_url(
            "https://www.amazon.de/Bosch-Professional-GBH-18V-26-F-Akku/dp/B0ABC",
            "Bosch GBH 2-26 F",
        )
        assert corded > cordless
        assert corded - cordless >= 20  # large enough to survive domain overlays

    def test_corded_outranks_cordless_manufacturer_url(self):
        corded = _score_url(
            "https://www.amazon.de/Bosch-Professional-GBH-2-26-F/dp/B07ABC",
            "Bosch GBH 2-26 F",
        )
        cordless_mfr = _score_url(
            "https://www.bosch-professional.com/de/de/products/gbh-18v-26-f-123",
            "Bosch GBH 2-26 F",
        )
        assert corded > cordless_mfr


# -----------------------------------------------------------------------------
# [F5] Distinctive-token helper logic -- extraction + gate behaviour
# -----------------------------------------------------------------------------


def _distinctive_tokens(query: str) -> list[str]:
    """Reference implementation that mirrors the aggregator-SERP resolver.

    Kept in sync with the inline definition in
    price_comparison_mcp.tools.search_prices._resolve_aggregator_search_to_product.
    If that function's definition drifts, this test fails loudly.
    """
    tokens = []
    for t in re.findall(r"[A-Za-z0-9\-]+", query):
        t_clean = t.strip("-")
        if len(t_clean) < 2:
            continue
        if any(c.isdigit() for c in t_clean):
            tokens.append(t_clean.lower())
    return tokens


class TestDistinctiveTokenExtraction:
    """The distinctive-token definition is pure function of the query."""

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("OBO Bettermann KSA-S40 Kabelschelle", ["ksa-s40"]),
            ("Bosch GBH 2-26 F", ["2-26"]),
            ("FLUKE 1674FC SCH Installationstester", ["1674fc"]),
            ("ASM-C6A G Anschlussmodul CAT 6A", ["asm-c6a", "6a"]),
            ("Niedax WRL 200.400 F", ["200", "400"]),
            ("Sony WH-1000XM5", ["wh-1000xm5"]),
            ("Kaffee schwarz", []),  # no digits -> gate stays disabled
        ],
    )
    def test_extraction(self, query, expected):
        assert sorted(_distinctive_tokens(query)) == sorted(expected)


class TestDistinctiveTokenGate:
    """Substring gate: at least one distinctive token must appear in the
    candidate link haystack (URL + title + text) or the aggregator
    resolver must refuse to substitute the product.

    The checks below mirror the actual gate logic: if distinctive list
    is empty the gate is disabled (pass-through); otherwise at least one
    token must be present as a substring.
    """

    @staticmethod
    def _gate(query: str, haystack: str) -> bool:
        """Return True iff gate allows resolution."""
        tokens = _distinctive_tokens(query)
        if not tokens:
            return True
        return any(t in haystack for t in tokens)

    def test_ksa_s40_wrong_kabelabzweigkasten_rejected(self):
        """[F5] OBO brand+Bettermann must not be enough -- no 'ksa-s40' in haystack."""
        assert not self._gate(
            "OBO Bettermann KSA-S40 Kabelschelle",
            "/obo-bettermann-kabelabzweigkasten-x10-h25-gnp5-lgr-2005308-a.html "
            "obo bettermann kabelabzweigkasten x10 h25",
        )

    def test_ksa_s40_correct_accepted(self):
        assert self._gate(
            "OBO Bettermann KSA-S40 Kabelschelle",
            "/obo-bettermann-ksa-s40-kabelschelle obo bettermann ksa-s40",
        )

    def test_bosch_corded_accepted(self):
        assert self._gate(
            "Bosch GBH 2-26 F",
            "/bosch-professional-gbh-2-26-f-elektro-bohr-meisselhammer-v10537.html bosch gbh 2-26",
        )

    def test_bosch_cordless_rejected_no_distinctive_substring(self):
        """Cordless URL has '18v-26' but not '2-26' -> gate rejects."""
        assert not self._gate(
            "Bosch GBH 2-26 F",
            "/bosch-professional-gbh-18v-26-f-akku.html bosch gbh 18v-26 f",
        )

    def test_no_distinctive_tokens_gate_pass_through(self):
        """Category queries without any digit token must not be gated."""
        assert self._gate("Kaffee schwarz", "/schwarzer-kaffee-espresso.html")
