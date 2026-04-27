"""Tests for the per-category variant detectors.

Covers:
  - fashion_size_detector  (XL vs 42)
  - fashion_color_detector (schwarz vs black; schwarz vs white)
  - wine_vintage_detector  (2015 vs 2018)
  - book_edition_detector  (ISBN mismatch)
  - automotive_oem_detector (E46 vs E90)
"""
from __future__ import annotations

import pytest

from price_comparison_mcp.categories.variant_detectors import (
    AUTOMOTIVE_OEM_PENALTY,
    BOOK_EDITION_PENALTY,
    DETECTOR_REGISTRY,
    FASHION_COLOR_PENALTY,
    FASHION_SIZE_PENALTY,
    PART_NUMBER_PENALTY,
    WINE_VINTAGE_PENALTY,
    _extract_part_number_tokens,
    _normalize_for_match,
    automotive_oem_detector,
    book_edition_detector,
    fashion_color_detector,
    fashion_size_detector,
    manufacturer_part_number_detector,
    run_category_detectors,
    wine_vintage_detector,
)


# -----------------------------------------------------------------------------
# Fashion size
# -----------------------------------------------------------------------------


class TestFashionSizeDetector:
    def test_numeric_match(self):
        """Query '42' matches URL '42' -> no penalty."""
        assert fashion_size_detector(
            "Nike Air Max 90 42",
            "https://www.zalando.de/nike-air-max-90-42-schwarz",
        ) == 0

    def test_numeric_mismatch(self):
        """Query '42', URL has '44' -> penalty."""
        assert fashion_size_detector(
            "Nike Air Max 90 42",
            "https://www.zalando.de/nike-air-max-90-44-schwarz",
        ) == FASHION_SIZE_PENALTY

    def test_letter_match(self):
        assert fashion_size_detector(
            "H&M T-Shirt XL",
            "https://www2.hm.com/tshirt-xl",
        ) == 0

    def test_letter_mismatch(self):
        assert fashion_size_detector(
            "H&M T-Shirt XL",
            "https://www2.hm.com/tshirt-XXL",
        ) == FASHION_SIZE_PENALTY

    def test_no_size_in_url_quiet(self):
        """URL has no size info -> no penalty (can't prove mismatch)."""
        assert fashion_size_detector(
            "Nike Air Max 90 42",
            "https://www.zalando.de/nike-air-max-90-schwarz",
        ) == 0

    def test_no_size_in_query_quiet(self):
        """Query lacks size -> no penalty."""
        assert fashion_size_detector(
            "Nike Air Max 90",
            "https://www.zalando.de/nike-air-max-90-44-schwarz",
        ) == 0

    def test_ignore_large_numbers(self):
        """'2015' is a year, not a fashion size."""
        assert fashion_size_detector(
            "shirt 2015 limited",
            "https://example.de/shirt-2023",
        ) == 0


# -----------------------------------------------------------------------------
# Fashion color
# -----------------------------------------------------------------------------


class TestFashionColorDetector:
    def test_same_color_de(self):
        assert fashion_color_detector(
            "Nike schwarz 42",
            "https://www.zalando.de/nike-schwarz-42",
        ) == 0

    def test_same_color_across_languages(self):
        """schwarz -> black must be recognised as same color."""
        assert fashion_color_detector(
            "Nike schwarz 42",
            "https://www.zalando.com/nike-black-42",
        ) == 0

    def test_different_colors(self):
        assert fashion_color_detector(
            "Nike schwarz 42",
            "https://www.zalando.de/nike-weiss-42",
        ) == FASHION_COLOR_PENALTY

    def test_no_color_quiet(self):
        assert fashion_color_detector(
            "Nike Air Max",
            "https://www.zalando.de/nike-air-max-sneaker",
        ) == 0


# -----------------------------------------------------------------------------
# Wine vintage
# -----------------------------------------------------------------------------


class TestWineVintageDetector:
    def test_matching_vintage(self):
        assert wine_vintage_detector(
            "Chateau Lafite 2015",
            "https://vinatis.de/chateau-lafite-rothschild-2015",
        ) == 0

    def test_mismatching_vintage(self):
        assert wine_vintage_detector(
            "Chateau Lafite 2015",
            "https://vinatis.de/chateau-lafite-rothschild-2018",
        ) == WINE_VINTAGE_PENALTY

    def test_no_year_in_url_quiet(self):
        assert wine_vintage_detector(
            "Chateau Lafite 2015",
            "https://vinatis.de/chateau-lafite-rothschild",
        ) == 0

    def test_no_year_in_query_quiet(self):
        assert wine_vintage_detector(
            "Chateau Lafite",
            "https://vinatis.de/chateau-lafite-rothschild-2015",
        ) == 0


# -----------------------------------------------------------------------------
# Book edition / ISBN
# -----------------------------------------------------------------------------


class TestBookEditionDetector:
    def test_matching_isbn_in_url(self):
        assert book_edition_detector(
            "ISBN 9783161484100",
            "https://www.thalia.de/shop/home/detail/9783161484100.html",
        ) == 0

    def test_mismatching_isbn(self):
        """Query ISBN 978-X, URL has ISBN 978-Y -> penalty."""
        assert book_edition_detector(
            "ISBN 9783161484100",
            "https://www.thalia.de/shop/home/detail/9781234567897.html",
        ) == BOOK_EDITION_PENALTY

    def test_no_isbn_in_url_quiet(self):
        """URL has no ISBN at all -> quiet (can't prove mismatch)."""
        assert book_edition_detector(
            "ISBN 9783161484100",
            "https://www.thalia.de/shop/home/detail/artikel-a1b2c3d4.html",
        ) == 0

    def test_no_isbn_in_query_quiet(self):
        assert book_edition_detector(
            "Harry Potter Band 1",
            "https://www.thalia.de/shop/home/detail/9783551551672.html",
        ) == 0

    def test_isbn_with_dashes_in_query(self):
        """Dashes in the query ISBN are normalised before comparison."""
        assert book_edition_detector(
            "978-3-16-148410-0",
            "https://www.thalia.de/shop/home/detail/9783161484100.html",
        ) == 0


# -----------------------------------------------------------------------------
# Automotive OEM / vehicle generation
# -----------------------------------------------------------------------------


class TestAutomotiveOemDetector:
    def test_matching_bmw_code(self):
        assert automotive_oem_detector(
            "Bremsscheibe BMW E46 320d",
            "https://www.autoteiledirekt.de/bmw-e46-bremsscheibe-vorn",
        ) == 0

    def test_mismatching_bmw_code(self):
        assert automotive_oem_detector(
            "Bremsscheibe BMW E46 320d",
            "https://www.autoteiledirekt.de/bmw-e90-bremsscheibe-vorn",
        ) == AUTOMOTIVE_OEM_PENALTY

    def test_mercedes_w_code(self):
        assert automotive_oem_detector(
            "Stossdaempfer W204",
            "https://www.autoteiledirekt.de/w205-stossdaempfer",
        ) == AUTOMOTIVE_OEM_PENALTY

    def test_no_code_in_query_quiet(self):
        assert automotive_oem_detector(
            "Bremsscheibe 320d",
            "https://www.autoteiledirekt.de/bmw-bremsscheibe-vorn",
        ) == 0


# -----------------------------------------------------------------------------
# Registry dispatcher
# -----------------------------------------------------------------------------


class TestRegistry:
    def test_registered_names(self):
        assert "fashion_size" in DETECTOR_REGISTRY
        assert "fashion_color" in DETECTOR_REGISTRY
        assert "wine_vintage" in DETECTOR_REGISTRY
        assert "book_edition" in DETECTOR_REGISTRY
        assert "automotive_oem" in DETECTOR_REGISTRY
        assert "manufacturer_part_number" in DETECTOR_REGISTRY

    def test_unknown_detector_name_silently_skipped(self):
        """Forward-compat: YAML can name a detector the binary doesn't ship."""
        result = run_category_detectors(
            "query",
            "url",
            ("no_such_detector", "fashion_size"),
        )
        assert result == 0  # no_such_detector skipped, fashion_size fires nothing

    def test_first_hit_wins(self):
        """Early-stop on first non-zero penalty -- no stacking."""
        # fashion_size fires penalty 25 for 42 vs 44
        result = run_category_detectors(
            "shoe 42",
            "https://example.com/shoe-44-black",
            ("fashion_size", "fashion_color"),
        )
        assert result == FASHION_SIZE_PENALTY


# =============================================================================
# Phase I: manufacturer_part_number_detector
# =============================================================================


class TestExtractPartNumberTokens:
    """Token extraction edge cases."""

    def test_empty_query_returns_empty(self):
        assert _extract_part_number_tokens("") == []

    def test_brand_only_returns_empty(self):
        # No alphanumeric mix -> no tokens
        assert _extract_part_number_tokens("OBO Bettermann Kabelschelle") == []

    def test_pure_digits_skipped(self):
        # 503800 has no letter; query yields empty
        assert _extract_part_number_tokens("Gira 503800 Aktor") == []

    def test_pure_letters_skipped(self):
        # GBH alone has no digit; not extracted
        assert _extract_part_number_tokens("Bosch GBH Bohrhammer") == []

    def test_year_rejected(self):
        # 2015 is a year, not a part number
        toks = _extract_part_number_tokens("Chateau Margaux 2015 Bordeaux")
        assert "2015" not in toks

    def test_decimal_rejected(self):
        # 5.0 is a decimal, not a part number
        toks = _extract_part_number_tokens("Akku 5.0 Ah Schrauber")
        assert "5.0" not in toks

    def test_too_short_skipped(self):
        # 3-char tokens are below the length floor
        toks = _extract_part_number_tokens("Modul X1 Bauteil")
        assert "X1" not in toks

    def test_part_number_with_hyphen(self):
        toks = _extract_part_number_tokens("OBO Bettermann KSA-S40 Kabelschelle")
        assert "KSA-S40" in toks

    def test_part_number_with_slash(self):
        toks = _extract_part_number_tokens("ABB LK/S4.2 Linienkoppler")
        assert "LK/S4.2" in toks

    def test_part_number_dense_alphanumeric(self):
        toks = _extract_part_number_tokens("Bosch MUM58720 Kuechenmaschine")
        assert "MUM58720" in toks

    def test_multiple_tokens_preserved(self):
        toks = _extract_part_number_tokens("Bosch MUM5 Styline MUM58720")
        assert toks == ["MUM5", "MUM58720"]

    def test_dedup_case_insensitive(self):
        # Same logical token at different case -> kept once
        toks = _extract_part_number_tokens("Test ksa-s40 KSA-S40 product")
        assert len(toks) == 1


class TestNormalizeForMatch:
    def test_strips_punctuation(self):
        assert _normalize_for_match("KSA-S40") == "ksas40"
        assert _normalize_for_match("KSA S40") == "ksas40"
        assert _normalize_for_match("KSA/S40") == "ksas40"
        assert _normalize_for_match("KSA.S40") == "ksas40"

    def test_lowercases(self):
        assert _normalize_for_match("MEG6921-0001") == "meg69210001"

    def test_empty_in_empty_out(self):
        assert _normalize_for_match("") == ""
        assert _normalize_for_match(None or "") == ""


class TestPartNumberDetector:
    """Hard regression cases from Testlauf 3."""

    def test_pos3_obo_asm_c6a_vs_dts_2c_rw1(self):
        """Testlauf 3 Pos 3: query KSA was for ASM-C6A, hit was DTS-2C-RW1."""
        penalty = manufacturer_part_number_detector(
            "OBO Bettermann ASM-C6A G Anschlussmodul CAT 6A geschirmt",
            "https://shop.de/dts-2c-rw1",
            "DTS-2C-RW1 Datentechnik Modul",
        )
        assert penalty == PART_NUMBER_PENALTY

    def test_pos3_obo_asm_c6a_correct_match(self):
        """Same query but the correct ASM-C6A page -> no penalty."""
        penalty = manufacturer_part_number_detector(
            "OBO Bettermann ASM-C6A G Anschlussmodul CAT 6A geschirmt",
            "https://shop.de/obo-asm-c6a",
            "OBO Bettermann ASM-C6A Anschlussmodul",
        )
        assert penalty == 0

    def test_pos4_obo_ksa_s40_vs_1594_22_g(self):
        """Testlauf 3 Pos 4: KSA-S40 query, 1594-22-G result."""
        penalty = manufacturer_part_number_detector(
            "OBO Bettermann KSA-S40 Kabelschelle",
            "https://elektro-shop.de/1594-22-g",
            "Befestigungsschelle 1594-22-G",
        )
        assert penalty == PART_NUMBER_PENALTY

    def test_punctuation_tolerant_match(self):
        """KSA-S40 in query, 'KSA S40' (space) in title -> match."""
        penalty = manufacturer_part_number_detector(
            "KSA-S40 Kabelschelle",
            "https://shop.de/p/123",
            "OBO KSA S40 Kabelschelle",
        )
        assert penalty == 0

    def test_punctuation_tolerant_match_no_separator(self):
        """KSA-S40 in query, 'KSAS40' (concatenated) in URL -> match."""
        penalty = manufacturer_part_number_detector(
            "KSA-S40 Kabelschelle",
            "https://shop.de/produkte/ksas40-bunt",
            "",
        )
        assert penalty == 0

    def test_match_via_url_path_only(self):
        """Token in URL path is sufficient even if context is empty."""
        penalty = manufacturer_part_number_detector(
            "Merten MEG6921-0001 KNX",
            "https://voltus.de/merten-meg6921-0001-knx-stellantrieb",
            "",
        )
        assert penalty == 0

    def test_match_via_context_only(self):
        """Token in context is sufficient even if URL path doesn't have it."""
        penalty = manufacturer_part_number_detector(
            "Merten MEG6921-0001 KNX",
            "https://voltus.de/produkte/p/12345",
            "Merten MEG6921-0001 KNX Stellantrieb",
        )
        assert penalty == 0


class TestPartNumberDetectorNullSafety:
    def test_short_token_below_floor_quiet(self):
        """Tokens shorter than the 4-char floor (e.g. 'A4', '18V') are
        ignored at extraction time -- they are too generic to base a
        veto on."""
        penalty = manufacturer_part_number_detector(
            "Bohrhammer Akku 18V",
            "https://shop.de/anything",
            "Random title",
        )
        # 18V is below the length floor -> no tokens extracted -> 0
        assert penalty == 0

    def test_truly_no_token_query_quiet(self):
        """Descriptive query with no alphanumeric mix at all."""
        penalty = manufacturer_part_number_detector(
            "Bohrhammer Akku Schrauber",
            "https://shop.de/anything",
            "Random title",
        )
        assert penalty == 0

    def test_empty_url_quiet(self):
        penalty = manufacturer_part_number_detector(
            "OBO KSA-S40", "", "",
        )
        # No haystack at all -> can't prove mismatch -> 0
        assert penalty == 0

    def test_invalid_url_does_not_raise(self):
        """urlparse on a garbage string returns the string AS the path.
        The detector must not raise; whatever verdict it returns is fine
        as long as it doesn't crash."""
        # Should not raise -- contract is defensiveness, not specific verdict.
        penalty = manufacturer_part_number_detector(
            "OBO KSA-S40", "not a url", "",
        )
        assert penalty in (0, PART_NUMBER_PENALTY)

    def test_brand_in_title_without_part_number_still_penalized(self):
        """Even with brand match, missing part number triggers penalty."""
        penalty = manufacturer_part_number_detector(
            "OBO Bettermann KSA-S40 Kabelschelle",
            "https://shop.de/obo-bettermann-zubehoer",
            "OBO Bettermann Sortiment",
        )
        assert penalty == PART_NUMBER_PENALTY


class TestPartNumberDetectorViaRegistry:
    def test_dispatched_through_run_category_detectors(self):
        """Detector reachable by name from the registry."""
        penalty = run_category_detectors(
            "OBO KSA-S40",
            "https://shop.de/wrong-product",
            ("manufacturer_part_number",),
            context="Some other product 1594-22-G",
        )
        assert penalty == PART_NUMBER_PENALTY

    def test_unknown_token_in_query_silent(self):
        """Tokens-but-they-match-haystack -> silent (0)."""
        penalty = run_category_detectors(
            "Sony WH-1000XM5 Bluetooth Kopfhoerer",
            "https://amazon.de/sony-wh-1000xm5-schwarz",
            ("manufacturer_part_number",),
            context="Sony WH-1000XM5",
        )
        assert penalty == 0
