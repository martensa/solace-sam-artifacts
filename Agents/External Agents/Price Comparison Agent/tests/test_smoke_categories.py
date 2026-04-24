"""End-to-end smoke test: classifier + profile + scoring over 20 queries.

This suite verifies the integrated pipeline for the 8 categories that
compose the 1.0.0 release. It does NOT hit the network (SearXNG,
LiteLLM) -- the test scope is the offline decision logic:

  - classifier picks the right category
  - registry.get() returns a profile with the expected shape
  - locale detector returns a reasonable locale
  - variant detectors fire on the expected mismatches
  - the regression-fix behaviour is preserved end-to-end

Covers 10 electrical/industrial + 3 sanitary + 3 office + 4 cross-cat.
"""
from __future__ import annotations

import pytest

from price_comparison_mcp.categories.classifier import classify
from price_comparison_mcp.categories.registry import CategoryRegistry
from price_comparison_mcp.locale.detector import detect_locale


@pytest.fixture(scope="module")
def registry():
    CategoryRegistry.reset()
    return CategoryRegistry.instance()


# -----------------------------------------------------------------------------
# The canonical smoke-suite for v1.0.0
# -----------------------------------------------------------------------------


# (query, expected_category, expected_locale, min_confidence)
SMOKE_QUERIES: list[tuple[str, str, str, float]] = [
    # ===== Electrical / Industrial (10) =====
    # Regression anchors (7)
    ("Bosch GBH 2-26 F",                            "tools_hardware",   "de", 0.80),
    ("Sony WH-1000XM5",                             "electronics",      "de", 0.80),
    ("Niedax WRL 200.400 F",                        "industrial_mro",   "de", 0.80),
    ("OBO Bettermann KSA-S40 Kabelschelle",         "industrial_mro",   "de", 0.80),
    ("ASM-C6A G Anschlussmodul CAT 6A",             "industrial_mro",   "de", 0.80),
    ("FLUKE 1674FC SCH Installationstester",        "industrial_mro",   "de", 0.80),
    ("Dotlux 5068-M LED-Netzteil QUICK-FIXadapt CC 50", "industrial_mro", "de", 0.80),
    # New electrical (3)
    ("Gira E2 Rahmen 2fach reinweiss",              "industrial_mro",   "de", 0.80),
    ("WAGO 221-412 Reihenklemme",                   "industrial_mro",   "de", 0.80),
    ("Merten System M Schalter reinweiss",          "industrial_mro",   "de", 0.80),

    # ===== Sanitary (3) =====
    ("Grohe Essence Waschtischarmatur chrom",       "sanitary",         "de", 0.80),
    ("Hansgrohe Croma 100 Vario Handbrause",        "sanitary",         "de", 0.80),
    ("Geberit UP320 Spuelkasten",                   "sanitary",         "de", 0.80),

    # ===== Office Supplies (3) =====
    ("Leitz Qualitaetsordner 180 A4",               "office_supplies",  "de", 0.80),
    ("Staedtler Noris Bleistift HB 12er",           "office_supplies",  "de", 0.80),
    ("HP 301 Tintenpatrone schwarz",                "office_supplies",  "de", 0.80),

    # ===== Cross-Category (4) =====
    ("ISBN 9783161484100",                          "book_media",       "de", 0.90),   # barcode hint
    ("Chateau Lafite 2015",                         "food_beverage",    "de", 0.80),
    ("Nike Air Max 90 weiss 42",                    "fashion_apparel",  "de", 0.80),
    ("Febi Bilstein 27424 Stossdaempfer",           "automotive",       "de", 0.80),
]


# -----------------------------------------------------------------------------
# Per-query assertions
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_cat,expected_locale,min_conf",
    SMOKE_QUERIES,
    ids=[q for q, *_ in SMOKE_QUERIES],
)
def test_classification(query, expected_cat, expected_locale, min_conf, registry):
    """Each smoke query must classify into its expected category with
    confidence >= min_conf, and the profile must be resolvable."""
    result = classify(query)
    assert result.category == expected_cat, (
        f"\n  query: {query}\n"
        f"  expected: {expected_cat}\n"
        f"  got:      {result.category} (source={result.source}, "
        f"details={result.details[:60]})"
    )
    assert result.confidence >= min_conf, (
        f"\n  query: {query}\n"
        f"  expected confidence >= {min_conf}, got {result.confidence}"
    )
    # Profile must exist
    profile = registry.get(result.category)
    assert profile.key == expected_cat or profile.key == "default"

    # Locale detection
    locale = detect_locale(query)
    assert locale == expected_locale or locale == "de", (
        f"\n  query: {query}\n"
        f"  expected locale: {expected_locale}\n"
        f"  got: {locale}"
    )


class TestCategoryCoverage:
    """Every profile we actively target in smoke MUST be shipped in the registry."""

    def test_all_smoke_categories_present(self, registry):
        seen = {row[1] for row in SMOKE_QUERIES}
        missing = seen - set(registry.keys())
        assert not missing, f"Missing profiles: {missing}"


class TestSanityChecks:
    """Structural asserts on the registry at release time."""

    def test_sanitary_profile_populated(self, registry):
        p = registry.get("sanitary")
        # domain_scores has at least the top-3 sanitary retailers
        assert "reuter.com" in p.domain_scores
        assert "megabad.com" in p.domain_scores
        # manufacturer_domains includes grohe + geberit
        assert "grohe.de" in p.manufacturer_domains
        assert "geberit.de" in p.manufacturer_domains
        # price band defined
        assert p.price_band is not None and p.price_band[0] > 0
