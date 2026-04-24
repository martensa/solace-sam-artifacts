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
    WINE_VINTAGE_PENALTY,
    automotive_oem_detector,
    book_edition_detector,
    fashion_color_detector,
    fashion_size_detector,
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
