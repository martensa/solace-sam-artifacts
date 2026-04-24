"""Tests for the free enrichment providers (OpenFoodFacts + Wikidata)
and their dispatcher.

All tests mock httpx via respx -- no network calls.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from price_comparison_mcp.enrichment import (
    dispatcher,
    openfoodfacts,
    wikidata,
)
from price_comparison_mcp.enrichment.openfoodfacts import EnrichmentResult


# =============================================================================
# EnrichmentResult.to_query_hint
# =============================================================================


class TestToQueryHint:
    def test_brand_plus_product(self):
        r = EnrichmentResult(
            source="openfoodfacts",
            ean="4105180000140",
            brand="Rothaus",
            product_name="Tannenzaepfle Pils",
            quantity="0.33 L",
        )
        hint = r.to_query_hint()
        assert "Rothaus" in hint
        assert "Tannenzaepfle" in hint
        assert "0.33 L" in hint

    def test_skip_product_name_equals_brand(self):
        r = EnrichmentResult(
            source="openfoodfacts",
            ean="123",
            brand="Amazon",
            product_name="Amazon",
            quantity="",
        )
        assert r.to_query_hint() == "Amazon"

    def test_empty(self):
        r = EnrichmentResult(source="openfoodfacts", ean="123")
        assert r.to_query_hint() == ""


# =============================================================================
# OpenFoodFacts
# =============================================================================


class TestOpenFoodFacts:
    @pytest.mark.asyncio
    @respx.mock
    async def test_successful_lookup(self):
        respx.get("https://world.openfoodfacts.org/api/v2/product/4105180000140.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 1,
                    "product": {
                        "product_name_de": "Tannenzaepfle Pils",
                        "brands": "Rothaus",
                        "quantity": "0.33 L",
                        "categories_tags": ["en:beverages", "en:alcoholic-drinks"],
                    },
                },
            )
        )
        async with httpx.AsyncClient() as client:
            result = await openfoodfacts.fetch(client, "4105180000140")
        assert result is not None
        assert result.brand == "Rothaus"
        assert "Tannenzaepfle" in result.product_name
        assert result.quantity == "0.33 L"
        assert result.category_hint == "food_beverage"

    @pytest.mark.asyncio
    @respx.mock
    async def test_cosmetic_tag(self):
        respx.get("https://world.openfoodfacts.org/api/v2/product/3600522920007.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 1,
                    "product": {
                        "product_name_de": "Elvital Shampoo",
                        "brands": "L'Oreal",
                        "quantity": "250 ml",
                        "categories_tags": ["en:beauty", "en:hair-care"],
                    },
                },
            )
        )
        async with httpx.AsyncClient() as client:
            result = await openfoodfacts.fetch(client, "3600522920007")
        assert result is not None
        assert result.category_hint == "cosmetic_pharma"

    @pytest.mark.asyncio
    @respx.mock
    async def test_product_not_found(self):
        respx.get("https://world.openfoodfacts.org/api/v2/product/0000000000000.json").mock(
            return_value=httpx.Response(200, json={"status": 0})
        )
        async with httpx.AsyncClient() as client:
            result = await openfoodfacts.fetch(client, "0000000000000")
        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_500_fails_silent(self):
        respx.get("https://world.openfoodfacts.org/api/v2/product/1234567890123.json").mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            result = await openfoodfacts.fetch(client, "1234567890123")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_ean_returns_none(self):
        # Non-digit input rejected without any HTTP call (respx not armed
        # but the function must still not throw).
        async with httpx.AsyncClient() as client:
            assert await openfoodfacts.fetch(client, "ABCDEFG") is None
            assert await openfoodfacts.fetch(client, "") is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_comma_brand_takes_first(self):
        respx.get("https://world.openfoodfacts.org/api/v2/product/4000000000000.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 1,
                    "product": {
                        "product_name": "Something",
                        "brands": "PrimaryBrand, SecondaryBrand",
                    },
                },
            )
        )
        async with httpx.AsyncClient() as client:
            result = await openfoodfacts.fetch(client, "4000000000000")
        assert result is not None
        assert result.brand == "PrimaryBrand"


# =============================================================================
# Wikidata
# =============================================================================


class TestWikidata:
    @pytest.mark.asyncio
    @respx.mock
    async def test_isbn_lookup(self):
        respx.get("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "itemLabel": {"value": "Effektive Java"},
                                "authorLabel": {"value": "Joshua Bloch"},
                                "publisherLabel": {"value": "Addison-Wesley"},
                            }
                        ]
                    }
                },
            )
        )
        async with httpx.AsyncClient() as client:
            result = await wikidata.fetch_isbn(client, "9780134685991")
        assert result is not None
        assert "Effektive Java" in result.product_name
        assert "Joshua Bloch" in result.product_name
        assert result.category_hint == "book_media"
        assert result.brand == "Addison-Wesley"

    @pytest.mark.asyncio
    @respx.mock
    async def test_isbn_no_results(self):
        respx.get("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(
                200,
                json={"results": {"bindings": []}},
            )
        )
        async with httpx.AsyncClient() as client:
            result = await wikidata.fetch_isbn(client, "9780000000000")
        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_ean_product(self):
        respx.get("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "itemLabel": {"value": "iPhone 15 Pro"},
                                "manufacturerLabel": {"value": "Apple Inc."},
                            }
                        ]
                    }
                },
            )
        )
        async with httpx.AsyncClient() as client:
            result = await wikidata.fetch_ean(client, "0195949019586")
        assert result is not None
        assert "iPhone" in result.product_name
        assert result.brand == "Apple Inc."

    @pytest.mark.asyncio
    async def test_invalid_code_returns_none(self):
        async with httpx.AsyncClient() as client:
            assert await wikidata.fetch(client, "") is None
            assert await wikidata.fetch(client, "123") is None  # too short
            assert await wikidata.fetch(client, "ABCDEFG") is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatch_isbn13_prefers_isbn_query(self):
        """A 978-prefix 13-digit code first tries ISBN query, falls back."""
        respx.get("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "itemLabel": {"value": "The Book"},
                                "authorLabel": {"value": "Some Author"},
                                "publisherLabel": {"value": "Some Publisher"},
                            }
                        ]
                    }
                },
            )
        )
        async with httpx.AsyncClient() as client:
            result = await wikidata.fetch(client, "9783662456033")
        assert result is not None
        assert result.category_hint == "book_media"


# =============================================================================
# Dispatcher
# =============================================================================


class TestDispatcher:
    @pytest.mark.asyncio
    @respx.mock
    async def test_off_hit_skips_wikidata(self):
        respx.get("https://world.openfoodfacts.org/api/v2/product/4105180000140.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 1,
                    "product": {
                        "product_name": "Tannenzaepfle",
                        "brands": "Rothaus",
                        "quantity": "0.33 L",
                        "categories_tags": ["en:beverages"],
                    },
                },
            )
        )
        # Wikidata not armed -- if dispatcher calls it the test would fail
        # because respx rejects unmocked requests.
        async with httpx.AsyncClient() as client:
            result = await dispatcher.enrich(client, "4105180000140")
        assert result is not None
        assert result.source == "openfoodfacts"

    @pytest.mark.asyncio
    @respx.mock
    async def test_off_miss_falls_through_to_wikidata(self):
        respx.get("https://world.openfoodfacts.org/api/v2/product/0195949019586.json").mock(
            return_value=httpx.Response(200, json={"status": 0})
        )
        respx.get("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "itemLabel": {"value": "iPhone 15 Pro"},
                                "manufacturerLabel": {"value": "Apple"},
                            }
                        ]
                    }
                },
            )
        )
        async with httpx.AsyncClient() as client:
            result = await dispatcher.enrich(client, "0195949019586")
        assert result is not None
        assert result.source == "wikidata"
        assert "iPhone" in result.product_name

    @pytest.mark.asyncio
    async def test_invalid_code(self):
        async with httpx.AsyncClient() as client:
            assert await dispatcher.enrich(client, "") is None
            assert await dispatcher.enrich(client, "abc") is None
            assert await dispatcher.enrich(client, "123") is None  # too short

    @pytest.mark.asyncio
    @respx.mock
    async def test_isbn10_wikidata_only(self):
        respx.get("https://query.wikidata.org/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "itemLabel": {"value": "A Book"},
                                "publisherLabel": {"value": "Publisher"},
                            }
                        ]
                    }
                },
            )
        )
        # OpenFoodFacts endpoint NOT mocked -- if dispatcher calls it for
        # a 10-char code, respx raises and the test fails.
        async with httpx.AsyncClient() as client:
            result = await dispatcher.enrich(client, "0134685997")
        assert result is not None
        assert result.source == "wikidata"
        assert result.category_hint == "book_media"
