"""Tests covering the Tier-1 + Tier-2 follow-up fixes after Testlauf 6.

Tier 1 -- bug fixes
  1.1 otto.de relative URL must be absolutized
  1.2 Aggregator-search detection covers Amazon `?k=`, Otto `/suche/`,
      `/s?`, MainSearchProductCategory paths
  1.3 prisma.film and similar spam hosts capped at off-locale score
  1.4 LLM-validator prompt mentions wrong-category guard (smoke test)
  1.5 Wine price band raised to 5000 EUR (Premier Grand Cru territory)

Tier 2 -- classifier brand-maps
  2.6 Sanitary brands (wilo, stiebel-eltron, ideal-standard, mepa,
      vaillant, viessmann, ...) classify as `sanitary`.
  2.7 Kitchen-appliance brand combinations (bosch mum, kenwood,
      kitchenaid, severin, krups, ...) classify as `home_garden`
      without breaking the bosch professional / bosch tool path.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Tier 1.1 -- _absolutize_url
# ---------------------------------------------------------------------------


class TestAbsolutizeURL:
    def test_relative_path_joined_to_base(self):
        from price_comparison_mcp.price_extractor import _absolutize_url
        result = _absolutize_url(
            "/p/hama-kabelkanal-CS03H50TA/",
            "https://www.otto.de/produkt/123",
        )
        assert result.startswith("https://www.otto.de/")
        assert "hama-kabelkanal" in result

    def test_protocol_relative_uses_base_scheme(self):
        from price_comparison_mcp.price_extractor import _absolutize_url
        result = _absolutize_url(
            "//cdn.example.com/img.jpg",
            "https://shop.example.de/p/1",
        )
        assert result.startswith("https://cdn.example.com/")

    def test_absolute_url_returned_unchanged(self):
        from price_comparison_mcp.price_extractor import _absolutize_url
        url = "https://www.shop.de/produkt/abc"
        assert _absolutize_url(url, "https://www.shop.de/") == url

    def test_empty_href_returns_base(self):
        from price_comparison_mcp.price_extractor import _absolutize_url
        assert _absolutize_url("", "https://www.shop.de/") == "https://www.shop.de/"

    def test_relative_navigates_one_level_up(self):
        from price_comparison_mcp.price_extractor import _absolutize_url
        result = _absolutize_url(
            "../produkte/foo",
            "https://www.shop.de/kategorie/bar",
        )
        assert result == "https://www.shop.de/produkte/foo"

    def test_handles_invalid_base_gracefully(self):
        from price_comparison_mcp.price_extractor import _absolutize_url
        # urljoin returns "/foo" when base lacks scheme; that's fine.
        out = _absolutize_url("/foo", "")
        assert out == "/foo" or out.endswith("/foo")


# ---------------------------------------------------------------------------
# Tier 1.2 -- aggregator-search-URL detection
# ---------------------------------------------------------------------------


class TestAggregatorSearchURL:
    def test_amazon_search_with_k_param_detected(self):
        from price_comparison_mcp.price_extractor import _is_aggregator_search_url
        # The Testlauf-6 false-positive: amazon /s?k= surfaced as a
        # high-confidence Top-1 hit instead of being skipped.
        url = "https://www.amazon.de/lego-42131/s?k=lego+42131"
        assert _is_aggregator_search_url(url) is True

    def test_otto_path_search_detected(self):
        from price_comparison_mcp.price_extractor import _is_aggregator_search_url
        assert _is_aggregator_search_url(
            "https://www.otto.de/suche/lego%20technic"
        ) is True

    def test_idealo_main_search_path_detected(self):
        from price_comparison_mcp.price_extractor import _is_aggregator_search_url
        assert _is_aggregator_search_url(
            "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html"
        ) is True

    def test_classic_query_param_still_works(self):
        from price_comparison_mcp.price_extractor import _is_aggregator_search_url
        assert _is_aggregator_search_url(
            "https://geizhals.de/?fs=foo+bar"
        ) is True
        assert _is_aggregator_search_url(
            "https://www.shop.de/?q=foo"
        ) is True

    def test_real_product_page_not_detected(self):
        from price_comparison_mcp.price_extractor import _is_aggregator_search_url
        assert _is_aggregator_search_url(
            "https://www.amazon.de/Bosch-GBH-2-26/dp/B01M07Y"
        ) is False
        assert _is_aggregator_search_url(
            "https://www.idealo.de/preisvergleich/OffersOfProduct/12345_-product.html"
        ) is False

    def test_garbage_url_no_crash(self):
        from price_comparison_mcp.price_extractor import _is_aggregator_search_url
        assert _is_aggregator_search_url("") is False
        assert _is_aggregator_search_url("not a url") is False


# ---------------------------------------------------------------------------
# Tier 1.3 -- spam hosts capped
# ---------------------------------------------------------------------------


class TestSpamHostCap:
    def test_prisma_film_capped(self):
        from price_comparison_mcp.tools.search_prices import _score_url
        score = _score_url(
            "https://www.prisma.film/Staedtler-Bleistift-Noris-12er-Pack",
            query="Staedtler Noris HB 12er Set",
        )
        assert score <= 5  # off-locale max score

    def test_finanzaonline_capped(self):
        from price_comparison_mcp.tools.search_prices import _score_url
        score = _score_url(
            "https://www.finanzaonline.com/notizie/foo",
            query="Bosch MUM58720",
        )
        assert score <= 5

    def test_quiz_spam_capped(self):
        from price_comparison_mcp.tools.search_prices import _score_url
        score = _score_url(
            "https://play.howstuffworks.com/quiz/can-pass-this-australian",
            query="Bosch GBH 2-26",
        )
        assert score <= 5

    def test_real_de_shop_unaffected(self):
        from price_comparison_mcp.tools.search_prices import _score_url
        score = _score_url(
            "https://www.galaxus.de/de/product/1234",
            query="Sony WH-1000XM5",
        )
        assert score >= 60


# ---------------------------------------------------------------------------
# Tier 1.4 -- validator prompt smoke test
# ---------------------------------------------------------------------------


class TestValidatorPromptCategory:
    def test_prompt_mentions_wrong_category_guard(self):
        from price_comparison_mcp.result_validator import _SYSTEM_PROMPT
        # The prompt must explicitly tell the LLM about noun-collision
        # traps observed in TL6 (Waschmittel vs Kleber).
        assert "WRONG-CATEGORY" in _SYSTEM_PROMPT or "Wrong-category" in _SYSTEM_PROMPT
        assert "Komponenten" in _SYSTEM_PROMPT
        assert "Kleber" in _SYSTEM_PROMPT

    def test_prompt_handles_search_pages(self):
        from price_comparison_mcp.result_validator import _SYSTEM_PROMPT
        # Search-result pages must be steered to "unsure".
        assert "Suchergebnisse" in _SYSTEM_PROMPT or "Search Results" in _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tier 1.5 -- wine price band raised
# ---------------------------------------------------------------------------


class TestWinePriceBand:
    def test_food_beverage_band_extends_to_premier_cru(self):
        from price_comparison_mcp.categories.registry import CategoryRegistry
        CategoryRegistry.reset()
        profile = CategoryRegistry.instance().get("food_beverage")
        assert profile.price_band is not None
        lo, hi = profile.price_band
        assert lo == 0.50  # cheap-side floor unchanged
        # New ceiling must allow real Margaux 2015 territory.
        assert hi >= 5000.0


# ---------------------------------------------------------------------------
# Tier 2.6 -- sanitary brand-map
# ---------------------------------------------------------------------------


class TestSanitaryClassification:
    def _classify(self, query: str) -> str:
        from price_comparison_mcp.categories.classifier import classify
        result = classify(query)
        return result.category if result else ""

    def test_wilo_classified_as_sanitary(self):
        cat = self._classify("Wilo Stratos MAXO 30/0,5-10 PN10 Pumpe G2")
        assert cat == "sanitary"

    def test_stiebel_eltron_classified_as_sanitary(self):
        cat = self._classify(
            "Stiebel Eltron SNU 5 Plus Kleinspeicher 5 Liter 2 kW"
        )
        assert cat == "sanitary"

    def test_ideal_standard_classified_as_sanitary(self):
        cat = self._classify(
            "Ideal Standard Ceraplan Kuechenarmatur Ausladung 219 mm"
        )
        assert cat == "sanitary"

    def test_mepa_classified_as_sanitary(self):
        cat = self._classify(
            "MEPA ellipse Betaetigungsplatte 2-Mengen Chrom"
        )
        assert cat == "sanitary"

    def test_vaillant_classified_as_sanitary(self):
        cat = self._classify("Vaillant ecoTEC plus VC 206/5-5 Brennwerttherme")
        assert cat == "sanitary"

    def test_viessmann_classified_as_sanitary(self):
        cat = self._classify("Viessmann Vitodens 200-W")
        assert cat == "sanitary"

    def test_oventrop_classified_as_sanitary(self):
        cat = self._classify("Oventrop Hydrocontrol VTR DN 50")
        assert cat == "sanitary"


# ---------------------------------------------------------------------------
# Tier 2.7 -- home-appliance brand-map (Bosch home goods)
# ---------------------------------------------------------------------------


class TestHomeKitchenClassification:
    def _classify(self, query: str) -> str:
        from price_comparison_mcp.categories.classifier import classify
        result = classify(query)
        return result.category if result else ""

    def test_bosch_mum_classified_as_home_garden(self):
        cat = self._classify("Bosch MUM5 Styline Kuechenmaschine MUM58720")
        assert cat == "home_garden"

    def test_kenwood_classified_as_home_garden(self):
        cat = self._classify("Kenwood Chef Titanium KVC5320S Kuechenmaschine")
        assert cat == "home_garden"

    def test_kitchenaid_classified_as_home_garden(self):
        cat = self._classify("KitchenAid Artisan 5KSM175 Kuechenmaschine")
        assert cat == "home_garden"

    def test_severin_classified_as_home_garden(self):
        cat = self._classify("Severin KA 4845 Kaffeemaschine")
        assert cat == "home_garden"

    def test_delonghi_classified_as_home_garden(self):
        cat = self._classify("De Longhi Magnifica S Kaffeevollautomat")
        assert cat == "home_garden"

    def test_bosch_professional_remains_tools_hardware(self):
        # Tier-2.7 must NOT break the existing bosch-professional path.
        cat = self._classify("Bosch Professional GBH 2-26 F Bohrhammer")
        assert cat == "tools_hardware"
