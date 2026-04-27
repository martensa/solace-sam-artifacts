"""Tests for Phase E: structured `next_actions` output on empty offers.

Covers the `_build_next_actions` helper in isolation (no network, no
pipeline orchestration). The pipeline wire-up is exercised indirectly
by existing pipeline integration tests once this lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from price_comparison_mcp.tools.search_prices import _build_next_actions


# ---------------------------------------------------------------------------
# Minimal profile double -- we only need .key and .manufacturer_domains.
# ---------------------------------------------------------------------------


@dataclass
class _FakeProfile:
    key: str = "default"
    manufacturer_domains: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Aggregator action -- ALWAYS emitted regardless of category.
# ---------------------------------------------------------------------------


class TestAggregatorAlwaysEmitted:
    def test_includes_idealo_geizhals_conrad(self):
        actions = _build_next_actions(
            query="Bosch GBH 2-28 F",
            profile=None,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        aggs = [a for a in actions if a["type"] == "check_aggregator"]
        vendors = {a["vendor"] for a in aggs}
        assert "idealo.de" in vendors
        assert "geizhals.de" in vendors
        assert "conrad.de" in vendors

    def test_aggregator_url_contains_query(self):
        actions = _build_next_actions(
            query="Niedax WRL 200",
            profile=None,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        aggs = [a for a in actions if a["type"] == "check_aggregator"]
        # URL must be a quoted search URL
        for a in aggs:
            assert "Niedax" in a["url"] or "Niedax+WRL" in a["url"]


# ---------------------------------------------------------------------------
# Contact-manufacturer action -- emitted iff profile has manufacturer_domains.
# ---------------------------------------------------------------------------


class TestContactManufacturer:
    def test_manufacturer_domains_produce_actions(self):
        profile = _FakeProfile(
            key="tools_hardware",
            manufacturer_domains=frozenset({"bosch-professional.com", "makita.de"}),
        )
        actions = _build_next_actions(
            query="Bosch GBH 2-28 F",
            profile=profile,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        mfr = [a for a in actions if a["type"] == "contact_manufacturer"]
        vendors = {a["vendor"] for a in mfr}
        assert "bosch-professional.com" in vendors
        assert "makita.de" in vendors

    def test_no_manufacturer_no_contact_action(self):
        profile = _FakeProfile(key="default", manufacturer_domains=frozenset())
        actions = _build_next_actions(
            query="Wasserhahn chrom",
            profile=profile,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        mfr = [a for a in actions if a["type"] == "contact_manufacturer"]
        assert mfr == []


# ---------------------------------------------------------------------------
# request_catalog_access -- emitted for B2B-heavy categories.
# ---------------------------------------------------------------------------


class TestRequestCatalogAccess:
    def test_industrial_mro_emits_wholesalers(self):
        profile = _FakeProfile(key="industrial_mro")
        actions = _build_next_actions(
            query="Siemens 5SV1316-6KK16",
            profile=profile,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        rca = [a for a in actions if a["type"] == "request_catalog_access"]
        vendors = {a["vendor"] for a in rca}
        # Core DE wholesalers must be surfaced
        assert "sonepar.de" in vendors
        assert "rexel.de" in vendors
        assert "mercateo.com" in vendors

    def test_non_b2b_category_suppresses_wholesalers(self):
        profile = _FakeProfile(key="book_media")
        actions = _build_next_actions(
            query="Programming Python",
            profile=profile,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        rca = [a for a in actions if a["type"] == "request_catalog_access"]
        # Books are not B2B-heavy -> no Sonepar/Rexel suggestion
        assert rca == []

    def test_login_gated_offers_surface_as_actions(self):
        profile = _FakeProfile(key="default")
        login_gated = [
            {"merchant": "trilux.com", "url": "https://trilux.com/search?q=x",
             "source": "playwright"},
        ]
        actions = _build_next_actions(
            query="Trilux Leuchte",
            profile=profile,
            classification=None,
            login_gated_offers=login_gated,
            enrichment_hint=None,
        )
        rca = [a for a in actions if a["type"] == "request_catalog_access"]
        vendors = {a["vendor"] for a in rca}
        assert "trilux.com" in vendors

    def test_duplicate_suppressed_between_login_gated_and_profile(self):
        """If sonepar.de is already in login_gated_offers, don't re-emit
        it from the B2B-heavy category list."""
        profile = _FakeProfile(key="industrial_mro")
        login_gated = [
            {"merchant": "sonepar.de", "url": "https://sonepar.de/...",
             "source": "b2b-hint"},
        ]
        actions = _build_next_actions(
            query="Bosch IXO",
            profile=profile,
            classification=None,
            login_gated_offers=login_gated,
            enrichment_hint=None,
        )
        rca = [a for a in actions if a["type"] == "request_catalog_access"]
        sonepars = [a for a in rca if a["vendor"] == "sonepar.de"]
        assert len(sonepars) == 1


# ---------------------------------------------------------------------------
# refine_query -- always last, uses enrichment_hint when available.
# ---------------------------------------------------------------------------


class TestRefineQuery:
    def test_refine_with_enrichment_hint(self):
        actions = _build_next_actions(
            query="4013960362794",
            profile=None,
            classification=None,
            login_gated_offers=None,
            enrichment_hint="Bosch Akku-Schrauber IXO 3.6V",
        )
        rq = [a for a in actions if a["type"] == "refine_query"]
        assert len(rq) == 1
        assert rq[0]["suggested_query"] == "Bosch Akku-Schrauber IXO 3.6V"

    def test_refine_without_hint(self):
        actions = _build_next_actions(
            query="obscure-sku-xyz",
            profile=None,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        rq = [a for a in actions if a["type"] == "refine_query"]
        assert len(rq) == 1
        assert rq[0]["suggested_query"] is None

    def test_no_refine_when_hint_equals_query(self):
        """Enrichment hint that just echoes the query shouldn't suggest
        rerunning with the same string."""
        actions = _build_next_actions(
            query="Bosch IXO",
            profile=None,
            classification=None,
            login_gated_offers=None,
            enrichment_hint="Bosch IXO",
        )
        rq = [a for a in actions if a["type"] == "refine_query"]
        assert len(rq) == 1
        assert rq[0]["suggested_query"] is None


# ---------------------------------------------------------------------------
# Stable schema contract -- every action has `type` + `rationale`.
# ---------------------------------------------------------------------------


class TestSchemaContract:
    def test_every_action_has_type_and_rationale(self):
        profile = _FakeProfile(
            key="industrial_mro",
            manufacturer_domains=frozenset({"bosch-professional.com"}),
        )
        actions = _build_next_actions(
            query="Bosch GBH",
            profile=profile,
            classification=None,
            login_gated_offers=[
                {"merchant": "wuerth.de", "url": "https://wuerth.de",
                 "source": "playwright"},
            ],
            enrichment_hint="Bosch GBH 2-28 F",
        )
        for a in actions:
            assert "type" in a
            assert a["type"] in {
                "contact_manufacturer",
                "request_catalog_access",
                "check_aggregator",
                "refine_query",
            }
            assert "rationale" in a
            assert isinstance(a["rationale"], str)
            assert len(a["rationale"]) > 0

    def test_action_order_deterministic(self):
        """Same inputs -> same output order. The LLM instruction relies
        on this ordering to surface the highest-value actions first."""
        profile = _FakeProfile(
            key="electronics",
            manufacturer_domains=frozenset({"siemens.com"}),
        )
        a1 = _build_next_actions(
            query="Siemens 5SV", profile=profile,
            classification=None, login_gated_offers=None,
            enrichment_hint=None,
        )
        a2 = _build_next_actions(
            query="Siemens 5SV", profile=profile,
            classification=None, login_gated_offers=None,
            enrichment_hint=None,
        )
        assert [a["type"] for a in a1] == [a["type"] for a in a2]


# ---------------------------------------------------------------------------
# Null-safety -- all optional inputs must be safe to pass as None.
# ---------------------------------------------------------------------------


class TestNullSafety:
    def test_all_none_still_produces_refine_and_aggregators(self):
        actions = _build_next_actions(
            query="x",
            profile=None,
            classification=None,
            login_gated_offers=None,
            enrichment_hint=None,
        )
        types = {a["type"] for a in actions}
        assert "check_aggregator" in types
        assert "refine_query" in types
        # No profile -> no contact_manufacturer / no category-driven
        # request_catalog_access entries.
        assert "contact_manufacturer" not in types
        assert "request_catalog_access" not in types

    def test_empty_login_gated_list_ok(self):
        profile = _FakeProfile(key="tools_hardware")
        actions = _build_next_actions(
            query="x",
            profile=profile,
            classification=None,
            login_gated_offers=[],
            enrichment_hint=None,
        )
        # tools_hardware IS B2B-heavy -> Sonepar/Rexel still surface
        rca = [a for a in actions if a["type"] == "request_catalog_access"]
        assert len(rca) >= 1


# ---------------------------------------------------------------------------
# Phase K+: per-category branching for consumer goods.
#
# B2B-heavy categories (industrial, sanitary, ...) keep the Sonepar/Rexel
# path. Consumer categories (office_supplies, fashion, books, food, ...)
# get curated retail vendors instead. office_supplies REMOVED from the
# B2B-heavy set because Leitz / Staedtler / HP toner are sold via
# Amazon / Otto-Office / Viking, not industrial wholesalers.
# ---------------------------------------------------------------------------


class TestPhaseKConsumerVendors:
    def test_office_supplies_no_sonepar(self):
        """The Testlauf-4 leak: Leitz binders proposed Sonepar/Rexel."""
        profile = _FakeProfile(key="office_supplies")
        actions = _build_next_actions(
            query="Leitz Qualitaetsordner 180 Grad A4 grau",
            profile=profile, classification=None,
            login_gated_offers=None, enrichment_hint=None,
        )
        vendors = {a.get("vendor", "") for a in actions}
        assert "sonepar.de" not in vendors
        assert "rexel.de" not in vendors
        assert "mercateo.com" not in vendors
        # Must include consumer alternatives
        assert "amazon.de" in vendors
        assert "otto-office.com" in vendors

    def test_fashion_apparel_consumer_only(self):
        profile = _FakeProfile(key="fashion_apparel")
        actions = _build_next_actions(
            query="Nike Air Force 1 Triple White 42",
            profile=profile, classification=None,
            login_gated_offers=None, enrichment_hint=None,
        )
        vendors = {a.get("vendor", "") for a in actions}
        assert "sonepar.de" not in vendors
        assert "zalando.de" in vendors
        assert "aboutyou.de" in vendors

    def test_book_media_consumer_only(self):
        profile = _FakeProfile(key="book_media")
        actions = _build_next_actions(
            query="Krieg und Frieden Tolstoi",
            profile=profile, classification=None,
            login_gated_offers=None, enrichment_hint=None,
        )
        vendors = {a.get("vendor", "") for a in actions}
        assert "thalia.de" in vendors
        assert "rexel.de" not in vendors

    def test_food_beverage_consumer_only(self):
        profile = _FakeProfile(key="food_beverage")
        actions = _build_next_actions(
            query="Miele Ultraphase Waschmittel",
            profile=profile, classification=None,
            login_gated_offers=None, enrichment_hint=None,
        )
        vendors = {a.get("vendor", "") for a in actions}
        assert "rewe.de" in vendors or "kaufland.de" in vendors
        assert "sonepar.de" not in vendors

    def test_industrial_mro_keeps_b2b(self):
        """B2B-heavy categories must still get Sonepar/Rexel/Mercateo."""
        profile = _FakeProfile(key="industrial_mro")
        actions = _build_next_actions(
            query="OBO KSA-S40", profile=profile,
            classification=None, login_gated_offers=None,
            enrichment_hint=None,
        )
        rca_vendors = {
            a.get("vendor", "")
            for a in actions
            if a["type"] == "request_catalog_access"
        }
        assert "sonepar.de" in rca_vendors
        assert "rexel.de" in rca_vendors

    def test_sanitary_keeps_b2b(self):
        profile = _FakeProfile(key="sanitary")
        actions = _build_next_actions(
            query="MEPA ellipse", profile=profile,
            classification=None, login_gated_offers=None,
            enrichment_hint=None,
        )
        rca_vendors = {
            a.get("vendor", "")
            for a in actions
            if a["type"] == "request_catalog_access"
        }
        assert "sonepar.de" in rca_vendors

    def test_chemicals_lab_uses_chemistry_distributors(self):
        """chemicals_lab now consumer-routed -- Sigma/Roth/VWR ARE the
        channel, no separate wholesaler tier."""
        profile = _FakeProfile(key="chemicals_lab")
        actions = _build_next_actions(
            query="Methanol p.a. 99.8", profile=profile,
            classification=None, login_gated_offers=None,
            enrichment_hint=None,
        )
        vendors = {a.get("vendor", "") for a in actions}
        assert "sigmaaldrich.com" in vendors
        assert "carlroth.com" in vendors
        assert "sonepar.de" not in vendors

    def test_consumer_url_contains_query(self):
        """Each consumer-vendor URL must embed the query for click-through."""
        profile = _FakeProfile(key="office_supplies")
        actions = _build_next_actions(
            query="Leitz 1050", profile=profile,
            classification=None, login_gated_offers=None,
            enrichment_hint=None,
        )
        for a in actions:
            if a["type"] == "check_aggregator":
                assert "Leitz" in a["url"] or "Leitz+1050" in a["url"]
