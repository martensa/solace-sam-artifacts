"""Tests for enrichment/ean.py -- validators + type detection + prefix lookup."""
from __future__ import annotations

import pytest

from price_comparison_mcp.enrichment import ean


# -----------------------------------------------------------------------------
# Normalisation
# -----------------------------------------------------------------------------


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("4012195887515", "4012195887515"),
            ("  4012195887515  ", "4012195887515"),
            ("978-3-16-148410-0", "9783161484100"),
            ("ISBN: 0-306-40615-2", "0306406152"),
            ("ISBN-13 9783161484100", "9783161484100"),
            ("isbn 0-306-40615-x", "030640615X"),   # trailing X upper-cased
            ("", None),
            (None, None),
            ("not-a-barcode-text", None),
            ("X4012195887515", None),               # X in non-last slot rejected
        ],
    )
    def test_examples(self, raw, expected):
        assert ean.normalize_ean(raw) == expected


# -----------------------------------------------------------------------------
# Type detection
# -----------------------------------------------------------------------------


class TestDetectCodeType:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("12345670", "gtin8"),              # 8 digits
            ("0123456789",     "isbn10"),       # 10 digits -> ISBN-10
            ("030640615X",     "isbn10"),       # ISBN-10 with X
            ("123456789012",   "upc12"),        # 12 digits -> UPC-A
            ("4012195887515",  "ean13"),        # 13 digits, not 978/979
            ("9783161484100",  "isbn13"),       # 13 digits, 978 -> ISBN-13
            ("9790000000000",  "isbn13"),       # 13 digits, 979 -> ISBN-13 (music ISMN range)
            ("14012195887512", "gtin14"),       # 14 digits
            ("abc",            "unknown"),
            ("",               "unknown"),
        ],
    )
    def test_examples(self, raw, expected):
        assert ean.detect_code_type(raw) == expected


# -----------------------------------------------------------------------------
# GTIN checksum
# -----------------------------------------------------------------------------


class TestValidateGTIN:
    # Real well-known GTINs used as golden samples
    VALID_EAN13 = [
        "4012195887515",  # OBO Bettermann ASM-C6A G (real)
        "9783161484100",  # ISBN-13 example (Bookland)
        "4006381333931",  # Staedtler (real, starts with 400 DE prefix)
    ]
    VALID_GTIN8 = [
        "12345670",       # minimal valid constructed
    ]
    INVALID = [
        "4012195887516",  # last digit off by one
        "4012195887500",  # wrong checksum
        "",
        "abc",
        "12345",          # wrong length
        "030640615X",     # ISBN-10 explicitly rejected by validate_gtin
    ]

    @pytest.mark.parametrize("code", VALID_EAN13 + VALID_GTIN8)
    def test_valid(self, code):
        assert ean.validate_gtin(code) is True

    @pytest.mark.parametrize("code", INVALID)
    def test_invalid(self, code):
        assert ean.validate_gtin(code) is False


# -----------------------------------------------------------------------------
# ISBN-10
# -----------------------------------------------------------------------------


class TestValidateISBN10:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("0306406152", True),       # The C Programming Language
            ("0-306-40615-2", True),    # with dashes, normalised first
            ("0201633612", True),       # Design Patterns (GoF)
            ("020163361X", False),      # wrong check char
            ("030640615X", False),      # wrong check char
            ("080442957X", True),       # Valid ISBN-10 with X check digit
            ("1234567890", False),      # fails mod 11
            ("12345", False),           # wrong length
            ("", False),
        ],
    )
    def test_examples(self, code, expected):
        assert ean.validate_isbn10(code) is expected


# -----------------------------------------------------------------------------
# validate_code dispatcher
# -----------------------------------------------------------------------------


class TestValidateCode:
    @pytest.mark.parametrize(
        "code",
        [
            "4012195887515",        # ean13
            "9783161484100",        # isbn13
            "0306406152",           # isbn10
            "080442957X",           # isbn10 with X
            "12345670",             # gtin8
        ],
    )
    def test_valid(self, code):
        assert ean.validate_code(code) is True

    @pytest.mark.parametrize(
        "code",
        [
            "4012195887516",        # wrong checksum
            "0306406151",           # wrong isbn10
            "",
            None,
            "not-a-barcode",
        ],
    )
    def test_invalid(self, code):
        assert ean.validate_code(code) is False


# -----------------------------------------------------------------------------
# ISBN-10 -> ISBN-13 upconversion
# -----------------------------------------------------------------------------


class TestIsbn10To13:
    def test_known_conversion(self):
        # "The C Programming Language" -- 0-306-40615-2 -> 978-0-306-40615-7
        assert ean.isbn10_to_isbn13("0306406152") == "9780306406157"

    def test_handles_dashes(self):
        assert ean.isbn10_to_isbn13("0-306-40615-2") == "9780306406157"

    def test_with_x_check_digit(self):
        # ISBN-10 0-8044-2957-X -> ISBN-13 978-0-8044-2957-0
        result = ean.isbn10_to_isbn13("080442957X")
        assert result is not None
        assert result.startswith("978")
        assert ean.validate_gtin(result) is True

    def test_invalid_input_returns_none(self):
        assert ean.isbn10_to_isbn13("0306406151") is None   # bad isbn10
        assert ean.isbn10_to_isbn13("not-an-isbn") is None
        assert ean.isbn10_to_isbn13("") is None


# -----------------------------------------------------------------------------
# GS1 prefix lookup
# -----------------------------------------------------------------------------


class TestPrefixCountry:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("4012195887515", "DE"),
            ("5000112637878", "GB"),
            ("8001120000019", "IT"),
            ("7310500000006", "SE"),
            ("9783161484100", None),
            ("9771234567003", None),
            ("4901330600006", "JP"),
            ("abc", None),
            ("", None),
        ],
    )
    def test_prefix(self, code, expected):
        assert ean.ean_prefix_country(code) == expected


class TestPrefixKind:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("9783161484100", "isbn"),
            ("9790000000000", "isbn"),
            ("9771234567003", "issn"),
            ("9812000000003", "coupon"),
            ("9802000000000", "refund"),
            ("2012345678905", "in-store"),
            ("4012195887515", None),        # regular product EAN
            ("", None),
        ],
    )
    def test_kind(self, code, expected):
        assert ean.ean_prefix_kind(code) == expected
