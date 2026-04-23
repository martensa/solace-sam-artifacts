"""Tests for locale.detector and locale.templates."""
from __future__ import annotations

import pytest

from price_comparison_mcp.locale.detector import detect_locale, tld_for
from price_comparison_mcp.locale.templates import (
    TEMPLATES,
    expand,
    expand_single_legacy,
)


# -----------------------------------------------------------------------------
# detect_locale
# -----------------------------------------------------------------------------


class TestDetectLocale:
    @pytest.mark.parametrize(
        "query,expected",
        [
            # German umlauts / sz are dead giveaway
            ("Bürostuhl ergonomisch", "de"),
            ("Größe 42 kaufen", "de"),
            ("Straße", "de"),
            # German stop words in umlaut-free query
            ("Preis mit Angebot kaufen", "de"),
            ("Ordner A4 für das Büro", "de"),
            # English stop words
            ("the new Apple iPhone for sale", "en"),
            ("cheap online store for shoes", "en"),
            # French with accented chars
            ("téléphone portable pas cher", "fr"),
            # Italian
            ("caffè espresso macinato", "it"),
            # Spanish
            ("señorita moda verano", "es"),
            ("¿Qué es un nombre?", "es"),
            # No signal -> fallback
            ("asdfqwer", "de"),
            ("", "de"),
            ("123456", "de"),
            # Bosch GBH 2-26 F -- no signal, default DE
            ("Bosch GBH 2-26 F", "de"),
        ],
    )
    def test_detect(self, query, expected):
        assert detect_locale(query) == expected

    def test_explicit_fallback(self):
        assert detect_locale("asdf", fallback="en") == "en"

    def test_short_de_token(self):
        """'der' is a DE stop word -- should tip the balance."""
        assert detect_locale("der weisse hund") == "de"


# -----------------------------------------------------------------------------
# tld_for
# -----------------------------------------------------------------------------


class TestTldFor:
    @pytest.mark.parametrize(
        "agg,locale,expected",
        [
            ("amazon", "de", "amazon.de"),
            ("amazon", "en", "amazon.com"),
            ("amazon", "fr", "amazon.fr"),
            ("idealo", "de", "idealo.de"),
            ("idealo", "en", "idealo.co.uk"),
            ("ebay", "de", "ebay.de"),
        ],
    )
    def test_known(self, agg, locale, expected):
        assert tld_for(agg, locale) == expected

    def test_unknown_combo_uses_fallback_tld(self):
        # "amazon" + "xx" -> amazon.de (default fallback)
        assert tld_for("amazon", "xx") == "amazon.de"

    def test_unknown_aggregator(self):
        assert tld_for("contorion", "de") == "contorion.de"


# -----------------------------------------------------------------------------
# expand (query templates)
# -----------------------------------------------------------------------------


class TestExpand:
    def test_default_de_matches_legacy(self):
        """(default, de) must equal the legacy single-expansion helper."""
        q = "Bosch GBH 2-26 F"
        result = expand(q, "default", "de")
        assert result == ("Bosch GBH 2-26 F Preis kaufen",)
        assert result[0] == expand_single_legacy(q)

    def test_category_specific_de(self):
        r = expand("9783161484100", "book_media", "de")
        assert any("ISBN" in e for e in r)
        assert any("Buch kaufen" in e or "Taschenbuch" in e for e in r)

    def test_category_specific_en(self):
        r = expand("Bosch GBH 2-26 F", "tools_hardware", "en")
        assert all("{q}" not in e for e in r)
        joined = " ".join(r).lower()
        assert "price" in joined or "part number" in joined

    def test_fallback_chain_missing_locale(self):
        """(electronics, fr) is not in TEMPLATES -- falls back to (electronics, en)."""
        assert ("electronics", "fr") not in TEMPLATES
        r = expand("Monitor 27", "electronics", "fr")
        assert any("datasheet" in e for e in r)

    def test_fallback_chain_missing_category(self):
        """Unknown category falls back to default."""
        r = expand("whatever", "xxx-unknown", "de")
        assert r == ("whatever Preis kaufen",)

    def test_fallback_all_missing(self):
        """If nothing maps, bare query is returned."""
        r = expand("abc", "xxx-unknown", "xx")
        # ("default", "xx") isn't registered either -> falls to ("default", "en")
        assert any("buy price" in e for e in r)

    def test_strips_whitespace(self):
        r = expand("  Bosch GBH 2-26 F  ", "default", "de")
        assert r == ("Bosch GBH 2-26 F Preis kaufen",)

    def test_no_raw_brace_tokens_leak(self):
        # Sanity: every expansion template must substitute {q} fully
        for (cat, loc), tmpls in TEMPLATES.items():
            for t in tmpls:
                assert "{q}" in t, f"Template {cat}/{loc} missing placeholder: {t}"


class TestLegacyHelper:
    def test_exact_legacy_format(self):
        assert expand_single_legacy("Bosch GBH 2-26 F") == "Bosch GBH 2-26 F Preis kaufen"

    def test_strips(self):
        assert expand_single_legacy("  x  ") == "x Preis kaufen"
