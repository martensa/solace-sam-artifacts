"""Tests for the Stage-1 heuristic classifier.

Covers:
  - barcode-shape hints (ISBN, ISSN)
  - structural code patterns (CAS, ISBN-10)
  - brand-name lookups (Bosch, Fluke, OBO, Sony, Nike, ...)
  - category keywords (Kabelschelle, Bohrhammer, Wein, ...)
  - the "no hit -> default / confidence=0" fallback (zero-regression contract)
"""
from __future__ import annotations

import pytest

from price_comparison_mcp.categories.classifier import classify


# -----------------------------------------------------------------------------
# Zero-regression: default falls through when nothing matches.
# -----------------------------------------------------------------------------


class TestFallthroughToDefault:
    @pytest.mark.parametrize(
        "query",
        [
            "asdf asdf",
            "Something random nobody recognises",
            "Widget 12345",          # bare digits, no other signal
            "",
            "   ",
        ],
    )
    def test_default_when_no_signal(self, query):
        r = classify(query)
        assert r.category == "default"
        assert r.confidence == 0.0


# -----------------------------------------------------------------------------
# Barcode-shape hints (fastest path)
# -----------------------------------------------------------------------------


class TestBarcodeShape:
    def test_isbn13(self):
        r = classify("9783161484100")
        assert r.category == "book_media"
        assert r.confidence >= 0.95

    def test_isbn10_upconverts_to_book_media(self):
        r = classify("0306406152")
        assert r.category == "book_media"
        assert r.confidence >= 0.95

    def test_issn(self):
        r = classify("9771234567003")
        assert r.category == "book_media"
        assert r.confidence >= 0.90

    def test_plain_ean13_no_category(self):
        """Regular EAN (not ISBN/ISSN) should NOT trigger a category
        alone -- the EAN doesn't disclose product class. Falls through
        to brand/keyword layers and ultimately default."""
        r = classify("4012195887515")   # real OBO EAN, no brand token
        # Without additional brand/keyword context, defaults to default.
        # (Enrichment in alpha3 will resolve the EAN -> product name.)
        assert r.category == "default"


# -----------------------------------------------------------------------------
# Structural patterns
# -----------------------------------------------------------------------------


class TestStructuralPatterns:
    def test_cas_number(self):
        r = classify("Methanol 67-56-1 99.9% p.a.")
        assert r.category == "chemicals_lab"
        assert r.confidence >= 0.9

    def test_cas_number_alone(self):
        r = classify("67-56-1")
        assert r.category == "chemicals_lab"


# -----------------------------------------------------------------------------
# Brand hits
# -----------------------------------------------------------------------------


class TestBrandHits:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("Bosch Professional GBH 2-26 F", "tools_hardware"),
            ("FLUKE 1674FC SCH Installationstester", "industrial_mro"),
            ("OBO Bettermann ASM-C6A G", "industrial_mro"),
            ("Niedax WRL 200.400 F", "industrial_mro"),
            ("Sony WH-1000XM5", "electronics"),
            ("Apple iPhone 15 Pro", "electronics"),
            ("Nike Air Max 42", "fashion_apparel"),
            ("Adidas Samba sneaker", "fashion_apparel"),
            ("LEGO 75192 Millennium Falcon", "toys_hobby"),
            ("Playmobil 70443 Ritterburg", "toys_hobby"),
            ("Sigma-Aldrich acetonitrile HPLC", "chemicals_lab"),
            ("Carl Roth Methanol", "chemicals_lab"),
            ("L'Oreal Pure Clay mask", "cosmetic_pharma"),
            ("Nivea Q10 Nachtcreme", "cosmetic_pharma"),
            ("Leitz Ordner 180", "office_supplies"),
            ("Lamy Safari Fueller", "office_supplies"),
            ("Decathlon Quechua Zelt", "sports_outdoor"),
            ("IKEA BILLY Regal", "home_garden"),
            ("Gardena Rasenmaeher", "home_garden"),
            ("Chateau Lafite 2015", "food_beverage"),
            ("Nespresso Vertuo Kapseln", "food_beverage"),
            ("Bilstein B6 Stossdaempfer", "automotive"),
        ],
    )
    def test_known_brands(self, query, expected):
        r = classify(query)
        assert r.category == expected, (
            f"expected {expected} for '{query}', got {r.category} "
            f"(source={r.source}, details={r.details})"
        )

    def test_bosch_professional_beats_bare_bosch(self):
        """Longest brand match wins -- 'bosch professional' -> tools_hardware
        (not 'bosch' alone)."""
        r = classify("Bosch Professional GBH 2-26 F")
        assert r.category == "tools_hardware"


# -----------------------------------------------------------------------------
# Category-keyword hits
# -----------------------------------------------------------------------------


class TestKeywordHits:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("Kabelschelle M25", "industrial_mro"),
            ("Anschlussmodul CAT 6A", "industrial_mro"),
            ("Installationstester Geraet", "industrial_mro"),
            ("Akkuschrauber 18V", "tools_hardware"),
            ("Winkelschleifer 125mm", "tools_hardware"),
            ("Maulschluessel Set 6-22", "tools_hardware"),
            ("4K Monitor 27 Zoll", "electronics"),
            ("Bluetooth Kopfhoerer noise cancelling", "electronics"),
            ("NVMe SSD 2TB", "electronics"),
            ("Laufschuh Groesse 42", "fashion_apparel"),
            ("Winterjacke Damen", "fashion_apparel"),
            ("Rotwein trocken Jahrgang 2018", "food_beverage"),
            ("Espresso Bohnen 1kg", "food_beverage"),
            ("Bremsscheibe BMW E46 320d", "automotive"),
            ("Sommerreifen 205/55 R16", "automotive"),
            ("Methanol 2.5L p.a.", "chemicals_lab"),
            ("HPLC Saeule C18", "chemicals_lab"),
            ("Shampoo Anti-Schuppen 400ml", "cosmetic_pharma"),
            ("Ibuprofen 400 Tabletten", "cosmetic_pharma"),
            ("Tintenpatrone HP 301", "office_supplies"),
            ("Druckerpapier A4 500 Blatt", "office_supplies"),
            ("Mountainbike 29 Zoll", "sports_outdoor"),
            ("Zelt 4 Personen", "sports_outdoor"),
            ("Sofa 3-Sitzer grau", "home_garden"),
            ("Rasenmaeher elektrisch 40cm", "home_garden"),
            ("Brettspiel ab 10 Jahren", "toys_hobby"),
            ("Taschenbuch Krimi", "book_media"),
        ],
    )
    def test_keywords(self, query, expected):
        r = classify(query)
        assert r.category == expected, (
            f"expected {expected} for '{query}', got {r.category} "
            f"(source={r.source}, details={r.details})"
        )


# -----------------------------------------------------------------------------
# Result metadata
# -----------------------------------------------------------------------------


class TestResultMeta:
    def test_source_distinguishes_barcode_from_brand(self):
        assert classify("9783161484100").source.startswith("heuristic:")
        assert classify("Bosch Professional GBH 2-26 F").source == "heuristic:brand"
        assert classify("Bohrhammer 850W").source == "heuristic:keyword"
        assert classify("asdfqwer").source == "heuristic:none"

    def test_details_non_empty_on_hit(self):
        r = classify("Nike Air Max 42")
        assert r.details != ""
