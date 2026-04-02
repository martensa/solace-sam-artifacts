"""Tests for scraper base class parsing functions."""

import pytest

from price_comparison_agent.scrapers.base import BaseScraper, _ResultCache


# ---------------------------------------------------------------------------
# _ResultCache
# ---------------------------------------------------------------------------


class TestResultCache:
    """Tests for the TTL-based result cache."""

    def test_set_and_get(self):
        cache = _ResultCache(ttl=60)
        cache.set("key1", [1, 2, 3])
        assert cache.get("key1") == [1, 2, 3]

    def test_missing_key_returns_none(self):
        cache = _ResultCache(ttl=60)
        assert cache.get("nonexistent") is None

    def test_clear(self):
        cache = _ResultCache(ttl=60)
        cache.set("key1", [1])
        cache.set("key2", [2])
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_expired_entry_returns_none(self):
        cache = _ResultCache(ttl=0)  # Immediate expiry
        cache.set("key1", [1, 2, 3])
        # With ttl=0, the entry should be expired on next get
        import time
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_evict_expired(self):
        cache = _ResultCache(ttl=0)
        cache.set("key1", [1])
        cache.set("key2", [2])
        import time
        time.sleep(0.01)
        cache._evict_expired()
        assert len(cache._store) == 0


# ---------------------------------------------------------------------------
# BaseScraper._parse_price (via concrete subclass)
# ---------------------------------------------------------------------------


class _TestScraper(BaseScraper):
    """Minimal concrete scraper for testing base class methods."""

    from price_comparison_agent.models import DataSource

    SOURCE = DataSource.IDEALO
    BASE_URL = "https://test.example.com"

    async def search_by_ean(self, ean, max_results=5):
        return []

    async def search_by_name(self, name, max_results=5):
        return []


class TestParsePrice:
    """Tests for _parse_price."""

    def setup_method(self):
        self.scraper = _TestScraper(timeout=5)

    def test_german_format(self):
        assert self.scraper._parse_price("1.234,56") == 1234.56

    def test_german_format_with_eur(self):
        assert self.scraper._parse_price("1.234,56 EUR") == 1234.56

    def test_simple_comma(self):
        assert self.scraper._parse_price("29,99") == 29.99

    def test_simple_dot(self):
        assert self.scraper._parse_price("29.99") == 29.99

    def test_integer(self):
        assert self.scraper._parse_price("100") == 100.0

    def test_with_eur_prefix(self):
        assert self.scraper._parse_price("EUR 49,99") == 49.99

    def test_empty_string(self):
        assert self.scraper._parse_price("") is None

    def test_none_like(self):
        assert self.scraper._parse_price("kein Preis") is None

    def test_large_german_price(self):
        assert self.scraper._parse_price("12.345,67 EUR") == 12345.67

    def test_price_with_whitespace(self):
        assert self.scraper._parse_price("  49,99  ") == 49.99


class TestParseShipping:
    """Tests for _parse_shipping."""

    def setup_method(self):
        self.scraper = _TestScraper(timeout=5)

    def test_gratis(self):
        assert self.scraper._parse_shipping("Gratis") == 0.0

    def test_kostenlos(self):
        assert self.scraper._parse_shipping("Kostenlos") == 0.0

    def test_frei(self):
        assert self.scraper._parse_shipping("Versandkostenfrei") == 0.0

    def test_zero_price(self):
        assert self.scraper._parse_shipping("0,00 EUR") == 0.0

    def test_actual_shipping(self):
        assert self.scraper._parse_shipping("4,99 EUR") == 4.99

    def test_empty_string(self):
        assert self.scraper._parse_shipping("") == 0.0

    def test_no_parseable_price(self):
        assert self.scraper._parse_shipping("auf Anfrage") == 0.0
