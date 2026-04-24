"""Tests for shop_ean_lookup.py -- parallel EAN-reverse on B2B shops.

All HTTP is mocked via respx. No network I/O.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from price_comparison_mcp.enrichment import shop_ean_lookup
from price_comparison_mcp.enrichment.shop_ean_lookup import (
    SHOPS,
    SUPPORTED_SHOPS,
    ShopProbe,
    _extract_jsonld_price,
    _extract_text_price_de,
    _parse_price_de,
    _parse_price_en,
    lookup,
)


# =============================================================================
# Parsers
# =============================================================================


class TestPriceParsers:
    def test_de_format_thousands(self):
        assert _parse_price_de("1.234,56") == 1234.56
        assert _parse_price_de("234,56") == 234.56
        assert _parse_price_de("234567,89") == 234567.89

    def test_en_format_dot(self):
        assert _parse_price_en("1234.56") == 1234.56
        assert _parse_price_en("45") == 45.0

    def test_en_comma_only(self):
        assert _parse_price_en("45,99") == 45.99

    def test_en_both_separators(self):
        # German thousands + comma decimal
        assert _parse_price_en("1.234,56") == 1234.56

    def test_parser_rejects_garbage(self):
        assert _parse_price_de("abc") is None


# =============================================================================
# JSON-LD extraction
# =============================================================================


class TestJsonLdExtraction:
    def test_extracts_price_and_currency(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type":"Product","name":"X","offers":{"@type":"Offer",
         "price":"149.99","priceCurrency":"EUR"}}
        </script>
        </head></html>
        """
        price, cur = _extract_jsonld_price(html)
        assert price == 149.99
        assert cur == "EUR"

    def test_ignores_non_product_jsonld(self):
        html = """
        <script type="application/ld+json">
        {"@type":"BreadcrumbList","price":"99.99"}
        </script>
        """
        price, _ = _extract_jsonld_price(html)
        assert price is None

    def test_defaults_currency_eur_when_missing(self):
        html = """
        <script type="application/ld+json">
        {"@type":"Product","offers":{"price":"42.00"}}
        </script>
        """
        price, cur = _extract_jsonld_price(html)
        assert price == 42.0
        assert cur == "EUR"

    def test_no_jsonld_returns_none(self):
        assert _extract_jsonld_price("<html>nothing here</html>") == (None, "EUR")


class TestTextPriceDE:
    def test_first_price_wins(self):
        html = "<div>Preis: 67,01 EUR inkl. MwSt.</div>"
        assert _extract_text_price_de(html) == 67.01

    def test_thousands(self):
        html = "<p>nur 1.499,00 EUR heute</p>"
        assert _extract_text_price_de(html) == 1499.0

    def test_none_when_no_currency(self):
        # No EUR marker -> must not false-positive on article numbers
        assert _extract_text_price_de("Artikel 1.234,56 ohne Waehrung") is None


# =============================================================================
# Module surface
# =============================================================================


class TestModuleSurface:
    def test_supported_shops_matches_list(self):
        assert set(SUPPORTED_SHOPS) == {s.name for s in SHOPS}
        assert len(SHOPS) >= 6

    def test_shop_probe_url_substitution(self):
        probe = SHOPS[0]
        url = probe.url("4013960362794")
        assert "4013960362794" in url


# =============================================================================
# lookup() integration
# =============================================================================


class TestLookup:
    @pytest.mark.asyncio
    async def test_invalid_ean_returns_empty(self):
        assert await lookup("") == []
        assert await lookup("abc") == []
        assert await lookup("123") == []     # too short
        assert await lookup("1" * 15) == []  # too long

    @pytest.mark.asyncio
    @respx.mock
    async def test_jsonld_hit_one_shop(self):
        # Arm just the Conrad probe; rest return empty (404).
        respx.get(host="www.conrad.de").mock(
            return_value=httpx.Response(
                200,
                text="""<html><head>
                <script type="application/ld+json">
                {"@type":"Product","offers":{"@type":"Offer","price":"149.99","priceCurrency":"EUR"}}
                </script>
                </head><body>x</body></html>""",
            )
        )
        respx.get(host="www.reichelt.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.voelkner.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.distrelec.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.automation24.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.pollin.de").mock(return_value=httpx.Response(404))
        respx.get(host="de.rs-online.com").mock(return_value=httpx.Response(404))
        respx.get(host="www.buerklin.com").mock(return_value=httpx.Response(404))

        offers = await lookup("4013960362794")
        assert len(offers) == 1
        assert offers[0]["merchant"] == "Conrad.de"
        assert offers[0]["total_price"] == 149.99
        assert offers[0]["currency"] == "EUR"
        assert offers[0]["source"].startswith("shop_ean:")

    @pytest.mark.asyncio
    @respx.mock
    async def test_text_price_fallback(self):
        """Reichelt has no JSON-LD -- falls through to text regex."""
        respx.get(host="www.conrad.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.reichelt.de").mock(
            return_value=httpx.Response(
                200,
                text="<html><body><div class='price'>3,45 EUR</div></body></html>" + "x" * 300,
            )
        )
        respx.get(host="www.voelkner.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.distrelec.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.automation24.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.pollin.de").mock(return_value=httpx.Response(404))
        respx.get(host="de.rs-online.com").mock(return_value=httpx.Response(404))
        respx.get(host="www.buerklin.com").mock(return_value=httpx.Response(404))

        offers = await lookup("4013960362794")
        assert len(offers) == 1
        assert offers[0]["merchant"] == "Reichelt.de"
        assert offers[0]["total_price"] == 3.45

    @pytest.mark.asyncio
    @respx.mock
    async def test_all_404_returns_empty(self):
        for host in ("www.conrad.de", "www.reichelt.de", "www.voelkner.de",
                     "www.distrelec.de", "www.automation24.de", "www.pollin.de",
                     "de.rs-online.com", "www.buerklin.com"):
            respx.get(host=host).mock(return_value=httpx.Response(404))
        offers = await lookup("4013960362794")
        assert offers == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_multiple_hits(self):
        """Two shops return valid prices; both end up in the offers list."""
        jsonld = """<html>
        <script type="application/ld+json">
        {"@type":"Product","offers":{"price":"10.00","priceCurrency":"EUR"}}
        </script>
        </html>""" + "x" * 300
        # Conrad uses jsonld_or_text; Voelkner as well.
        respx.get(host="www.conrad.de").mock(return_value=httpx.Response(200, text=jsonld))
        respx.get(host="www.voelkner.de").mock(return_value=httpx.Response(200, text=jsonld))
        respx.get(host="www.reichelt.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.distrelec.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.automation24.de").mock(return_value=httpx.Response(404))
        respx.get(host="www.pollin.de").mock(return_value=httpx.Response(404))
        respx.get(host="de.rs-online.com").mock(return_value=httpx.Response(404))
        respx.get(host="www.buerklin.com").mock(return_value=httpx.Response(404))

        offers = await lookup("4013960362794")
        assert len(offers) == 2
        merchants = {o["merchant"] for o in offers}
        assert "Conrad.de" in merchants
        assert "Voelkner.de" in merchants

    @pytest.mark.asyncio
    @respx.mock
    async def test_short_body_treated_as_empty(self):
        """A 200 response with a tiny body (redirect landing) is ignored."""
        for host in ("www.conrad.de", "www.reichelt.de", "www.voelkner.de",
                     "www.distrelec.de", "www.automation24.de", "www.pollin.de",
                     "de.rs-online.com", "www.buerklin.com"):
            respx.get(host=host).mock(return_value=httpx.Response(200, text="<html/>"))
        offers = await lookup("4013960362794")
        assert offers == []

    @pytest.mark.asyncio
    async def test_non_digit_ean_rejected(self):
        # Must never launch network probes for garbage input.
        assert await lookup("abc12345") == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_custom_shops_list(self):
        """Caller can restrict to a subset -- smoke-test for composability."""
        probe = ShopProbe(
            name="only-conrad",
            merchant="ConradOnly",
            url_template="https://www.conrad.de/de/search.html?search={ean}",
            extract=lambda _h, _e: 99.0,
        )
        respx.get(host="www.conrad.de").mock(
            return_value=httpx.Response(200, text="<html>" + "x" * 300 + "</html>")
        )
        offers = await lookup("4013960362794", shops=[probe])
        assert len(offers) == 1
        assert offers[0]["total_price"] == 99.0
