"""Tests for the CategoryRegistry + CategoryProfile merge semantics."""
from __future__ import annotations

import pytest

from price_comparison_mcp.categories.registry import (
    CategoryRegistry,
    CategoryInheritanceCycle,
    DEFAULT_KEY,
)


# -----------------------------------------------------------------------------
# Registry load + shipped profiles
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry() -> CategoryRegistry:
    CategoryRegistry.reset()
    return CategoryRegistry.instance()


class TestLoad:
    def test_default_profile_present(self, registry):
        assert DEFAULT_KEY in registry

    def test_shipped_profiles(self, registry):
        """Each of the 13 profiles documented in the v1.0 plan is shipped."""
        expected = {
            "default",
            "industrial_mro", "electronics", "tools_hardware",
            "fashion_apparel", "book_media", "food_beverage",
            "automotive", "chemicals_lab", "cosmetic_pharma",
            "office_supplies", "sports_outdoor", "home_garden",
            "toys_hobby",
        }
        assert expected.issubset(set(registry.keys())), \
            f"Missing: {expected - set(registry.keys())}"

    def test_unknown_key_returns_default(self, registry):
        unknown = registry.get("does-not-exist")
        assert unknown.key == DEFAULT_KEY


# -----------------------------------------------------------------------------
# Default profile must be bit-identical to v2.3.5 behaviour
# -----------------------------------------------------------------------------


class TestDefaultProfile:
    def test_default_no_domain_overlay(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.domain_scores == {}

    def test_default_no_extra_manufacturers(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.manufacturer_domains == frozenset()

    def test_default_no_penalty_override(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.manufacturer_penalty_override is None

    def test_default_legacy_expansion(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.query_expansions == ("{q} Preis kaufen",)

    def test_default_no_extra_rule_c_fillers(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.rule_c_fillers == frozenset()

    def test_default_no_price_band(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.price_band is None

    def test_default_no_antilex(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.title_gate_antilex == frozenset()

    def test_default_engines_unset(self, registry):
        p = registry.get(DEFAULT_KEY)
        assert p.preferred_shopping_engines is None
        assert p.preferred_general_engines is None


# -----------------------------------------------------------------------------
# Inheritance semantics (industrial_mro -> default, tools_hardware -> industrial_mro)
# -----------------------------------------------------------------------------


class TestInheritance:
    def test_industrial_mro_inherits_default_but_extends_scores(self, registry):
        p = registry.get("industrial_mro")
        # Has its own domain_scores
        assert "wuerth.de" in p.domain_scores
        assert p.domain_scores["wuerth.de"] == 85
        # Has its own query expansions (child replaces)
        assert p.query_expansions != ("{q} Preis kaufen",)
        assert any("Datenblatt" in exp for exp in p.query_expansions)
        # Has price band
        assert p.price_band == (0.10, 200000.0)

    def test_tools_hardware_inherits_industrial_mro(self, registry):
        p = registry.get("tools_hardware")
        # Gets wuerth.de from industrial_mro (ancestor)
        assert "wuerth.de" in p.domain_scores
        # Adds tools-specific scores
        assert "toolineo.de" in p.domain_scores
        # Inherits manufacturer_domains from industrial_mro (union)
        assert "bosch-professional.com" in p.manufacturer_domains
        # Adds its own antilex
        assert "spuelmaschine" in p.title_gate_antilex

    def test_electronics_inherits_industrial_mro(self, registry):
        p = registry.get("electronics")
        assert "wuerth.de" in p.domain_scores       # from ancestor
        assert "cyberport.de" in p.domain_scores    # own
        assert p.price_band == (1.0, 50000.0)       # override

    def test_chemicals_penalty_override_zero(self, registry):
        """Chemistry must override the manufacturer penalty to 0
        (Sigma-Aldrich is both manufacturer AND retailer)."""
        p = registry.get("chemicals_lab")
        assert p.manufacturer_penalty_override == 0

    def test_book_media_penalty_override_60(self, registry):
        """Publishers are hard-deprioritised. Value is subtraction
        magnitude (positive int); base default = 40."""
        p = registry.get("book_media")
        assert p.manufacturer_penalty_override == 60


# -----------------------------------------------------------------------------
# Merge behaviour -- set fields are unioned, dicts merged, tuples replaced
# -----------------------------------------------------------------------------


class TestMergeSemantics:
    def test_manufacturer_domains_union(self, registry):
        base = registry.get("industrial_mro")
        child = registry.get("tools_hardware")
        assert base.manufacturer_domains.issubset(child.manufacturer_domains)

    def test_rule_c_fillers_union(self, registry):
        ind = registry.get("industrial_mro")
        # tools_hardware doesn't declare rule_c_fillers -> must inherit
        tool = registry.get("tools_hardware")
        assert ind.rule_c_fillers.issubset(tool.rule_c_fillers)

    def test_query_expansions_replaced_by_child(self, registry):
        ind = registry.get("industrial_mro")
        ele = registry.get("electronics")
        # industrial_mro uses "{q} Artikelnummer Preis" / "{q} Datenblatt" / '"{q}"'
        # electronics declares its own list
        assert ele.query_expansions != ind.query_expansions
        assert any("Test" in e for e in ele.query_expansions)


# -----------------------------------------------------------------------------
# Cycle detection
# -----------------------------------------------------------------------------


class TestCycleDetection:
    def test_synthetic_cycle_detected(self, tmp_path):
        # Minimal cyclic setup: default plus two mutually inheriting nodes
        (tmp_path / "_default.yaml").write_text(
            "key: default\ndisplay_name: d\ninherits: null\n"
        )
        (tmp_path / "a.yaml").write_text(
            "key: a\ndisplay_name: a\ninherits: b\n"
        )
        (tmp_path / "b.yaml").write_text(
            "key: b\ndisplay_name: b\ninherits: a\n"
        )
        with pytest.raises(CategoryInheritanceCycle):
            CategoryRegistry(data_dir=tmp_path)
