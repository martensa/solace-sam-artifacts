"""Tests for price_comparison_agent.utils."""

import pytest

from price_comparison_agent.utils import (
    calculate_savings,
    detect_search_type,
    format_price,
    is_valid_ean,
    normalize_ean,
    sanitize_product_name,
    truncate_text,
)


# ---------------------------------------------------------------------------
# is_valid_ean
# ---------------------------------------------------------------------------


class TestIsValidEan:
    """Tests for EAN validation."""

    def test_valid_ean13(self):
        assert is_valid_ean("4006381333931") is True

    def test_valid_ean8(self):
        assert is_valid_ean("96385074") is True

    def test_valid_ean_with_spaces(self):
        assert is_valid_ean("4006 3813 3393 1") is True

    def test_valid_ean_with_hyphens(self):
        assert is_valid_ean("4006-3813-3393-1") is True

    def test_invalid_checksum(self):
        assert is_valid_ean("4006381333932") is False

    def test_too_short(self):
        assert is_valid_ean("12345") is False

    def test_too_long(self):
        assert is_valid_ean("123456789012345") is False

    def test_non_digit(self):
        assert is_valid_ean("400638abcd931") is False

    def test_empty_string(self):
        assert is_valid_ean("") is False

    def test_valid_upc_a(self):
        # UPC-A is 12 digits
        assert is_valid_ean("012345678905") is True

    def test_valid_gtin14(self):
        # GTIN-14: 14 digits
        assert is_valid_ean("00012345678905") is True


# ---------------------------------------------------------------------------
# normalize_ean
# ---------------------------------------------------------------------------


class TestNormalizeEan:
    """Tests for EAN normalization."""

    def test_strips_spaces(self):
        assert normalize_ean("4006 3813 3393 1") == "4006381333931"

    def test_strips_hyphens(self):
        assert normalize_ean("4006-3813-3393-1") == "4006381333931"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize_ean("  4006381333931  ") == "4006381333931"

    def test_clean_ean_unchanged(self):
        assert normalize_ean("4006381333931") == "4006381333931"

    def test_mixed_separators(self):
        assert normalize_ean(" 4006-381 333-931 ") == "4006381333931"


# ---------------------------------------------------------------------------
# detect_search_type
# ---------------------------------------------------------------------------


class TestDetectSearchType:
    """Tests for automatic search type detection."""

    def test_ean13(self):
        assert detect_search_type("4006381333931") == "ean"

    def test_ean8(self):
        assert detect_search_type("96385074") == "ean"

    def test_ean_with_spaces(self):
        assert detect_search_type("4006 3813 3393 1") == "ean"

    def test_ean_with_hyphens(self):
        assert detect_search_type("4006-3813-3393-1") == "ean"

    def test_product_name(self):
        assert detect_search_type("Bosch GSR 18V-28") == "name"

    def test_short_number_is_name(self):
        assert detect_search_type("12345") == "name"

    def test_alphanumeric_is_name(self):
        assert detect_search_type("ABC123DEF") == "name"

    def test_empty_string(self):
        assert detect_search_type("") == "name"


# ---------------------------------------------------------------------------
# format_price
# ---------------------------------------------------------------------------


class TestFormatPrice:
    """Tests for German price formatting."""

    def test_simple_price(self):
        assert format_price(29.99) == "29,99 EUR"

    def test_thousands(self):
        assert format_price(1234.56) == "1.234,56 EUR"

    def test_zero(self):
        assert format_price(0.0) == "0,00 EUR"

    def test_large_price(self):
        assert format_price(12345678.90) == "12.345.678,90 EUR"

    def test_small_price(self):
        assert format_price(0.01) == "0,01 EUR"

    def test_whole_number(self):
        assert format_price(100.0) == "100,00 EUR"


# ---------------------------------------------------------------------------
# calculate_savings
# ---------------------------------------------------------------------------


class TestCalculateSavings:
    """Tests for savings calculation."""

    def test_positive_savings(self):
        savings, pct = calculate_savings(100.0, 80.0)
        assert savings == 20.0
        assert pct == 20.0

    def test_no_savings(self):
        savings, pct = calculate_savings(100.0, 100.0)
        assert savings == 0.0
        assert pct == 0.0

    def test_negative_savings(self):
        # Alternative is more expensive
        savings, pct = calculate_savings(80.0, 100.0)
        assert savings == -20.0
        assert pct == -25.0

    def test_zero_reference(self):
        savings, pct = calculate_savings(0.0, 50.0)
        assert savings == -50.0
        assert pct == 0.0

    def test_rounding(self):
        savings, pct = calculate_savings(33.33, 22.22)
        assert savings == 11.11
        assert pct == 33.3


# ---------------------------------------------------------------------------
# sanitize_product_name
# ---------------------------------------------------------------------------


class TestSanitizeProductName:
    """Tests for product name sanitization."""

    def test_collapses_whitespace(self):
        assert sanitize_product_name("Bosch   GSR   18V") == "Bosch GSR 18V"

    def test_strips_edges(self):
        assert sanitize_product_name("  Bosch GSR 18V  ") == "Bosch GSR 18V"

    def test_normal_string_unchanged(self):
        assert sanitize_product_name("Bosch GSR 18V") == "Bosch GSR 18V"


# ---------------------------------------------------------------------------
# truncate_text
# ---------------------------------------------------------------------------


class TestTruncateText:
    """Tests for text truncation."""

    def test_short_text_unchanged(self):
        assert truncate_text("short", 100) == "short"

    def test_exact_length_unchanged(self):
        assert truncate_text("12345", 5) == "12345"

    def test_truncates_with_ellipsis(self):
        result = truncate_text("abcdefghij", 8)
        assert result == "abcde..."
        assert len(result) == 8

    def test_default_max_length(self):
        long_text = "x" * 200
        result = truncate_text(long_text)
        assert len(result) == 100
        assert result.endswith("...")
