"""Phase L+: per-domain concurrency cap + per-domain retry backoff.

Both helpers live in tools/search_prices.py. Tests are pure-Python --
no Playwright, no real network. They exercise the registry lookups
and the asyncio Semaphore semantics.
"""
from __future__ import annotations

import asyncio

import pytest

from price_comparison_mcp.tools.search_prices import (
    _DOMAIN_CONCURRENCY,
    _DOMAIN_RETRY_BACKOFF_SECONDS,
    _domain_for_concurrency,
    _domain_semaphore,
    _retry_backoff_for_url,
)


class TestDomainKeyResolution:
    def test_exact_match(self):
        assert _domain_for_concurrency("https://www.idealo.de/x") == "idealo.de"

    def test_subdomain_match(self):
        assert _domain_for_concurrency("https://m.idealo.de/x") == "idealo.de"

    def test_geizhals_at(self):
        assert _domain_for_concurrency("https://geizhals.at/?fs=foo") == "geizhals.at"

    def test_billiger(self):
        assert _domain_for_concurrency("https://www.billiger.de/produkt/123") == "billiger.de"

    def test_unknown_domain_empty(self):
        assert _domain_for_concurrency("https://www.amazon.de/dp/B0X") == ""

    def test_garbage_url_empty(self):
        assert _domain_for_concurrency("not a url") == ""

    def test_empty_string(self):
        assert _domain_for_concurrency("") == ""


class TestRetryBackoff:
    def test_idealo_long_backoff(self):
        bo = _retry_backoff_for_url("https://www.idealo.de/preisvergleich/foo")
        assert bo >= 5.0  # Idealo registered with 8s

    def test_geizhals_long_backoff(self):
        bo = _retry_backoff_for_url("https://geizhals.de/?fs=x")
        assert bo >= 5.0

    def test_unknown_domain_default(self):
        bo = _retry_backoff_for_url("https://www.shop.de/produkt/x", default=2.5)
        assert bo == 2.5

    def test_billiger_medium_backoff(self):
        bo = _retry_backoff_for_url("https://www.billiger.de/products/123")
        assert 2.5 <= bo < 8.0


class TestDomainSemaphore:
    """Semaphore semantics: per-domain concurrency must serialise."""

    def test_idealo_serialised_to_one(self):
        async def run():
            sem = _domain_semaphore("idealo.de")
            assert sem is not None
            assert _DOMAIN_CONCURRENCY["idealo.de"] == 1

            order: list[str] = []

            async def worker(name: str, hold: float) -> None:
                async with sem:
                    order.append(f"{name}-start")
                    await asyncio.sleep(hold)
                    order.append(f"{name}-end")

            # If sem caps at 1, workers must finish in entry order.
            await asyncio.gather(
                worker("a", 0.05),
                worker("b", 0.01),
                worker("c", 0.01),
            )
            return order

        order = asyncio.run(run())
        # Worker a holds first; b enters after a-end; c after b-end.
        assert order == [
            "a-start", "a-end",
            "b-start", "b-end",
            "c-start", "c-end",
        ]

    def test_billiger_allows_two_parallel(self):
        async def run():
            sem = _domain_semaphore("billiger.de")
            assert sem is not None
            assert _DOMAIN_CONCURRENCY["billiger.de"] == 2

            running = 0
            peak = 0

            async def worker():
                nonlocal running, peak
                async with sem:
                    running += 1
                    peak = max(peak, running)
                    await asyncio.sleep(0.05)
                    running -= 1

            await asyncio.gather(*[worker() for _ in range(4)])
            return peak

        peak = asyncio.run(run())
        # Cap=2 -> peak parallel must be exactly 2 (never 3 or 4)
        assert peak == 2

    def test_unknown_domain_returns_none(self):
        async def run():
            return _domain_semaphore("amazon.de")
        sem = asyncio.run(run())
        assert sem is None

    def test_empty_domain_returns_none(self):
        async def run():
            return _domain_semaphore("")
        sem = asyncio.run(run())
        assert sem is None

    def test_semaphore_rebuilt_per_loop(self):
        """Each fresh asyncio loop gets a fresh semaphore so cross-loop
        deadlocks (common in pytest with multiple async tests) cannot
        happen."""
        async def grab():
            return _domain_semaphore("idealo.de")
        sem1 = asyncio.run(grab())
        sem2 = asyncio.run(grab())
        # Different loops -> distinct semaphore objects
        assert sem1 is not None and sem2 is not None
        assert sem1 is not sem2


class TestRegistryContents:
    """Sanity checks: key registry entries exist with expected shapes."""

    def test_aggregators_registered_with_cap_1(self):
        for d in ("idealo.de", "geizhals.de"):
            assert d in _DOMAIN_CONCURRENCY
            assert _DOMAIN_CONCURRENCY[d] == 1

    def test_aggregators_registered_with_long_backoff(self):
        for d in ("idealo.de", "geizhals.de"):
            assert d in _DOMAIN_RETRY_BACKOFF_SECONDS
            assert _DOMAIN_RETRY_BACKOFF_SECONDS[d] >= 5.0
