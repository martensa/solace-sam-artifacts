"""Tests for enrichment/agent_delegation.py (Phase F).

All HTTP is mocked via respx. Tests do NOT touch the real gateway.
Feature flag toggled via monkeypatch.setenv so tests are hermetic.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from price_comparison_mcp.enrichment import agent_delegation
from price_comparison_mcp.enrichment.agent_delegation import (
    _parse_response,
    fetch,
    is_enabled,
)


# =============================================================================
# Feature-flag gating
# =============================================================================


class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("PRICE_ENABLE_AGENT_DELEGATION", raising=False)
        monkeypatch.delenv("PRICE_SAM_GATEWAY_URL", raising=False)
        assert is_enabled() is False

    def test_flag_only_without_gateway_still_disabled(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", "true")
        monkeypatch.delenv("PRICE_SAM_GATEWAY_URL", raising=False)
        assert is_enabled() is False

    def test_gateway_only_without_flag_still_disabled(self, monkeypatch):
        monkeypatch.delenv("PRICE_ENABLE_AGENT_DELEGATION", raising=False)
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw")
        assert is_enabled() is False

    def test_both_set_enabled(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", "true")
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw")
        assert is_enabled() is True

    def test_accepts_alternative_truthy_values(self, monkeypatch):
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw")
        for v in ("true", "True", "1", "yes", "on"):
            monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", v)
            assert is_enabled() is True, f"expected True for {v!r}"

    def test_rejects_falsy_values(self, monkeypatch):
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw")
        for v in ("false", "0", "no", "off", "", "bogus"):
            monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", v)
            assert is_enabled() is False, f"expected False for {v!r}"


# =============================================================================
# Response parser
# =============================================================================


class TestParser:
    def test_structured_brand_and_name(self):
        text = "Brand: Bosch\nProduct: Akku-Schrauber IXO 3.6V\n"
        r = _parse_response(text, "4013960362794")
        assert r is not None
        assert r.brand == "Bosch"
        assert r.product_name == "Akku-Schrauber IXO 3.6V"
        assert r.source == "ean_search_agent"
        assert r.ean == "4013960362794"

    def test_german_labels(self):
        text = "Hersteller: Siemens\nProduktname: 5SV1316-6KK16 FI/LS"
        r = _parse_response(text, "4011209000000")
        assert r is not None
        assert r.brand == "Siemens"
        assert "5SV1316-6KK16" in r.product_name

    def test_strip_quotes_and_backticks(self):
        r = _parse_response('Brand: `Bosch`\nName: "IXO"', "1" * 13)
        assert r is not None
        assert r.brand == "Bosch"
        assert r.product_name == "IXO"

    def test_freeform_falls_back_to_first_line(self):
        text = "Bosch IXO 3.6V Akku-Schrauber\nAnother line below"
        r = _parse_response(text, "1" * 13)
        assert r is not None
        assert r.product_name == "Bosch IXO 3.6V Akku-Schrauber"

    def test_empty_returns_none(self):
        assert _parse_response("", "1" * 13) is None
        assert _parse_response("   \n\n  ", "1" * 13) is None

    def test_ignores_absurdly_long_freeform(self):
        # 500-char first line -> ignored; no brand/name parsed either
        r = _parse_response("X" * 500, "1" * 13)
        assert r is None


# =============================================================================
# fetch() gating behaviour (no HTTP required)
# =============================================================================


class TestFetchGating:
    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.delenv("PRICE_ENABLE_AGENT_DELEGATION", raising=False)
        monkeypatch.delenv("PRICE_SAM_GATEWAY_URL", raising=False)
        result = await fetch(None, "4013960362794")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_ean_returns_none_even_when_enabled(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", "true")
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw")
        assert await fetch(None, "") is None
        assert await fetch(None, "abc") is None
        assert await fetch(None, "1" * 7) is None   # too short
        assert await fetch(None, "1" * 15) is None  # too long


# =============================================================================
# fetch() full path with mocked gateway
# =============================================================================


class TestFetchIntegration:
    @pytest.mark.asyncio
    @respx.mock
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", "true")
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw.local")

        # Gateway send -> return task_id
        respx.post("http://gw.local/api/v1/message:send").mock(
            return_value=httpx.Response(
                200, json={"result": {"task_id": "task-abc"}},
            )
        )
        # Gateway poll -> return completed task with structured text
        respx.get("http://gw.local/api/v1/tasks/task-abc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "invocation_flow": [
                        {
                            "direction": "response",
                            "payload": {
                                "result": {
                                    "status": {
                                        "state": "completed",
                                        "message": {
                                            "parts": [
                                                {"text": "Brand: Bosch\nProduct: IXO 3.6V"},
                                            ],
                                        },
                                    },
                                },
                            },
                        },
                    ],
                },
            )
        )
        result = await fetch(None, "4013960362794", timeout=3.0, poll_interval=0.01)
        assert result is not None
        assert result.brand == "Bosch"
        assert "IXO" in result.product_name
        assert result.source == "ean_search_agent"

    @pytest.mark.asyncio
    @respx.mock
    async def test_send_non_200_returns_none(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", "true")
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw.local")
        respx.post("http://gw.local/api/v1/message:send").mock(
            return_value=httpx.Response(500, text="oops")
        )
        assert await fetch(None, "4013960362794", timeout=2.0) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_send_without_task_id_returns_none(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", "true")
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw.local")
        respx.post("http://gw.local/api/v1/message:send").mock(
            return_value=httpx.Response(200, json={"result": {}})
        )
        assert await fetch(None, "4013960362794", timeout=2.0) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_poll_failed_status_returns_none(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_AGENT_DELEGATION", "true")
        monkeypatch.setenv("PRICE_SAM_GATEWAY_URL", "http://gw.local")
        respx.post("http://gw.local/api/v1/message:send").mock(
            return_value=httpx.Response(200, json={"task_id": "t1"})
        )
        respx.get("http://gw.local/api/v1/tasks/t1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "invocation_flow": [
                        {
                            "direction": "response",
                            "payload": {
                                "result": {"status": {"state": "failed"}},
                            },
                        },
                    ],
                },
            )
        )
        assert await fetch(None, "4013960362794",
                           timeout=2.0, poll_interval=0.01) is None


# =============================================================================
# Dispatcher integration: tier 3 does NOT fire when disabled.
# =============================================================================


class TestDispatcherIntegration:
    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatcher_skips_delegation_when_disabled(self, monkeypatch):
        """When both tier-1 and tier-2 miss AND delegation is disabled,
        the cascade must NOT hit the gateway. We verify by asserting
        no gateway URL was mocked yet the call completes with None."""
        monkeypatch.delenv("PRICE_ENABLE_AGENT_DELEGATION", raising=False)
        monkeypatch.delenv("PRICE_SAM_GATEWAY_URL", raising=False)

        # OpenFoodFacts + Wikidata both miss (404 / empty).
        respx.get(host="world.openfoodfacts.org").mock(
            return_value=httpx.Response(404)
        )
        respx.get(host="query.wikidata.org").mock(
            return_value=httpx.Response(200, json={"results": {"bindings": []}})
        )
        from price_comparison_mcp.enrichment.dispatcher import enrich
        result = await enrich(None, "4013960362794", total_timeout=2.0)
        assert result is None
        # Gateway URL is unset; if the dispatcher had tried to call it,
        # httpx would have raised (respx intercepts all traffic and
        # un-mocked hosts trigger an AllMockedAssertionError under strict
        # mode). Hitting this assertion means the gate held.
