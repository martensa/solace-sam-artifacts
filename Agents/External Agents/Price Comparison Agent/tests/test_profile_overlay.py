"""Tests for the category profile overlay on _score_url and Rule C.

Zero-regression contract:
  - profile=None MUST yield scores identical to pre-v1.0 behaviour.
  - A category's domain_scores can only LIFT a score, never drop it.
  - manufacturer_penalty_override replaces the default -40 when set.
  - rule_c_fillers extend the base filler set (union).
"""
from __future__ import annotations

import pytest

from price_comparison_mcp.categories.registry import CategoryRegistry
from price_comparison_mcp.tools.search_prices import (
    _foreign_model_qualifier_penalty as fq,
    _score_url,
    _MANUFACTURER_PENALTY,
)


@pytest.fixture(scope="module")
def registry() -> CategoryRegistry:
    CategoryRegistry.reset()
    return CategoryRegistry.instance()


# -----------------------------------------------------------------------------
# Backward compatibility: profile=None is bit-identical to no profile
# -----------------------------------------------------------------------------


class TestBackwardCompat:
    @pytest.mark.regression
    def test_score_url_none_equals_no_profile(self):
        url = "https://www.amazon.de/Bosch-Professional-GBH-2-26-F/dp/B0DK22DGM6"
        query = "Bosch GBH 2-26 F"
        assert _score_url(url, query) == _score_url(url, query, profile=None)

    @pytest.mark.regression
    def test_fq_none_equals_no_profile(self):
        url = "https://geizhals.de/bosch-professional-gbh-18v-26-f-akku.html"
        query = "Bosch GBH 2-26 F"
        assert fq(query, url) == fq(query, url, profile=None)

    @pytest.mark.regression
    def test_default_profile_equivalent_to_none(self, registry):
        """The 'default' YAML profile is explicitly crafted to be empty.
        Every score MUST equal the profile=None score."""
        default_profile = registry.get("default")
        cases = [
            ("https://www.amazon.de/Bosch-Professional-GBH-2-26-F/dp/B0DK22DGM6", "Bosch GBH 2-26 F"),
            ("https://geizhals.de/bosch-professional-gbh-18v-26-f-akku.html", "Bosch GBH 2-26 F"),
            ("https://www.conrad.de/de/p/fluke-1674fc-sch-installationstester.html", "FLUKE 1674FC SCH"),
            ("https://www.tandmore.de/Installation/Niedax/Niedax-WRL200-400F", "Niedax WRL 200.400 F"),
        ]
        for url, q in cases:
            assert _score_url(url, q) == _score_url(url, q, profile=default_profile), \
                f"default profile changed score for {url[:60]}"
            assert fq(q, url) == fq(q, url, profile=default_profile), \
                f"default profile changed penalty for {url[:60]}"


# -----------------------------------------------------------------------------
# Domain overlay: LIFT domains the profile ranks higher
# -----------------------------------------------------------------------------


class TestDomainOverlay:
    def test_industrial_lifts_wuerth(self, registry):
        p = registry.get("industrial_mro")
        url = "https://www.wuerth.de/produkt/something/p-123"
        base = _score_url(url, "Schraube M8")
        with_profile = _score_url(url, "Schraube M8", profile=p)
        # wuerth.de is not in base _PRICE_SITE_SCORES, so it sits at the
        # "unknown domain with /produkt/" tier (~30 base). The profile
        # overlays 85. We expect a sizeable jump.
        assert with_profile > base
        # Must not be lower than the overlay
        assert with_profile >= 85 - 15  # -15 penalty for /kategorie/ etc doesn't apply here

    def test_book_media_lifts_thalia(self, registry):
        p = registry.get("book_media")
        url = "https://www.thalia.de/shop/home/detail-category/"
        base = _score_url(url, "any book")
        with_profile = _score_url(url, "any book", profile=p)
        assert with_profile > base

    def test_overlay_never_drops_score(self, registry):
        """For any domain, profile scoring must be >= base scoring."""
        p = registry.get("electronics")
        urls = [
            "https://www.amazon.de/whatever/dp/B01234",
            "https://www.idealo.de/preisvergleich/OffersOfProduct/1.html",
            "https://www.geizhals.de/product.html",
            "https://www.conrad.de/de/p/fluke-etc-123.html",
        ]
        for url in urls:
            base = _score_url(url, "test")
            with_profile = _score_url(url, "test", profile=p)
            assert with_profile >= base, f"overlay dropped score for {url}"


# -----------------------------------------------------------------------------
# Manufacturer-penalty override
# -----------------------------------------------------------------------------


class TestManufacturerPenaltyOverride:
    def test_chemicals_override_to_zero(self, registry):
        """Sigma-Aldrich is both manufacturer and distributor -- chemistry
        profile overrides the penalty to 0 so it scores as a real retailer."""
        p = registry.get("chemicals_lab")
        url = "https://www.sigmaaldrich.com/DE/de/product/sigald/m1775"
        no_profile = _score_url(url, "Methanol p.a.")
        with_profile = _score_url(url, "Methanol p.a.", profile=p)
        assert with_profile > no_profile
        # profile promotes sigma to 92 AND drops the -40 penalty
        assert with_profile >= 92 - 15  # any category-path penalty would cap it

    def test_book_media_penalty_stronger(self, registry):
        """Publishers: -60 override is STRONGER than default -40 --
        any profile-manufacturer URL scores strictly lower than base."""
        p = registry.get("book_media")
        # Pick a publisher domain listed in the book_media profile
        url = "https://www.suhrkamp.de/buch/titel-12345"
        base = _score_url(url, "any book")              # no profile -> no penalty
        with_profile = _score_url(url, "any book", profile=p)
        assert with_profile < base


# -----------------------------------------------------------------------------
# Rule C filler overlay
# -----------------------------------------------------------------------------


class TestRuleCFillerOverlay:
    def test_no_overlay_behaviour_preserved(self):
        """Without a profile, behaviour is unchanged."""
        # Rule C triggers for Fluke FTT (existing regression case)
        url = "https://geizhals.de/fluke-1674-fc-ftt-installationstester.html"
        assert fq("FLUKE 1674FC SCH", url) == 25

    def test_fillers_can_suppress_profile_specific_false_positives(self, registry):
        """Industrial profile adds 'nyy', 'vde', 'ip' -- a hypothetical URL
        with those tokens near a digit anchor would otherwise fire Rule C
        but must stay quiet under the industrial profile."""
        p = registry.get("industrial_mro")
        # Hypothetical URL with 'vde' 3-char token near anchor 400 --
        # would trigger Rule C without the filler.
        url = "https://example.de/niedax-wrl-400-vde-cable-trail-feuerverzinkt"
        query = "Niedax WRL 200.400 F"
        # Under the profile, "vde" is a filler -> no fire
        assert fq(query, url, profile=p) == 0

    def test_base_fillers_unchanged_under_profile(self, registry):
        """The built-in base fillers (html, und, der, ...) remain effective
        even when a profile is applied."""
        # "html" is already a base filler -- regression should stay quiet.
        p = registry.get("industrial_mro")
        url = "https://www.voelkner.de/products/11071460/Fluke-1674FC-SCH-Installationstester.html"
        assert fq("FLUKE 1674FC SCH", url, profile=p) == 0
