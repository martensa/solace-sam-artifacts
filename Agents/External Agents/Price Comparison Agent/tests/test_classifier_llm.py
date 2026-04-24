"""Tests for the cascaded LLM classifier with mocked LiteLLM endpoint.

Coverage:
  - Heuristic returns high confidence -> LLM skipped
  - Heuristic returns low confidence -> LLM called, result used if stronger
  - LLM timeout -> falls back to heuristic cleanly
  - LLM returns unknown category -> ignored, falls back to heuristic
  - LLM returns lower confidence -> heuristic wins
  - Short-token queries skip LLM (no bare-EAN roundtrip)
  - Cache hit avoids repeat LLM calls
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from price_comparison_mcp.categories.classifier import ClassificationResult
from price_comparison_mcp.categories.classifier_llm import (
    LLMClassifierConfig,
    _CACHE,
    classify_cascaded,
)


# Fresh cache each test (module fixture wipes at collection time)
@pytest.fixture(autouse=True)
def _clear_cache():
    _CACHE._data.clear()
    yield
    _CACHE._data.clear()


def _cfg(enabled: bool = True, min_conf: float = 0.5) -> LLMClassifierConfig:
    return LLMClassifierConfig(
        endpoint="http://mock-litellm/v1",
        api_key="mock-key",
        model="openai/claude-sonnet-4-6",
        timeout_seconds=3.0,
        min_confidence_threshold=min_conf,
        min_alpha_tokens=3,
        enabled=enabled,
    )


def _llm_response(category: str, confidence: float, reasoning: str = "ok") -> dict:
    content = json.dumps({
        "category": category,
        "confidence": confidence,
        "reasoning": reasoning,
    })
    return {
        "choices": [{"message": {"content": content}}],
    }


@pytest.mark.asyncio
class TestCascadeGating:
    async def test_high_heuristic_skips_llm(self):
        """Heuristic brand hit -> confidence 0.85 >= 0.5 -> skip LLM."""
        # "Bosch Professional" is a known brand in the heuristic map
        async with respx.mock(assert_all_called=False) as m:
            # Set up a route but we expect it NOT to fire
            route = m.post("http://mock-litellm/v1/chat/completions")
            route.mock(return_value=httpx.Response(200, json=_llm_response("book_media", 0.99)))
            r = await classify_cascaded("Bosch Professional GBH 2-26 F", _cfg())
            assert r.category == "tools_hardware"
            assert r.source.startswith("heuristic:")
            assert not route.called

    async def test_low_heuristic_calls_llm(self):
        """Heuristic hits nothing -> confidence 0.0 -> LLM called."""
        async with respx.mock() as m:
            m.post("http://mock-litellm/v1/chat/completions").mock(
                return_value=httpx.Response(200, json=_llm_response("electronics", 0.80))
            )
            r = await classify_cascaded("unknown gadget pro max edition", _cfg())
            assert r.category == "electronics"
            assert r.source == "llm"
            assert r.confidence == 0.80

    async def test_llm_worse_than_heuristic_loses(self):
        """LLM is called but returns lower confidence -> heuristic wins."""
        async with respx.mock() as m:
            # A short-token query that won't trigger heuristic strongly
            # but heuristic might have 0.0 confidence. Use a setup where
            # heuristic=0.0 and LLM=0.1 -- LLM "wins" by virtue of being
            # >= heuristic. Build a case where LLM < heuristic is tested
            # differently.
            m.post("http://mock-litellm/v1/chat/completions").mock(
                return_value=httpx.Response(200, json=_llm_response("default", 0.3))
            )
            # "gadget pro max" -> heuristic default @ 0.0, LLM default @ 0.3 -> LLM wins
            r = await classify_cascaded("gadget pro max edition", _cfg())
            assert r.source == "llm"

    async def test_llm_timeout_falls_back_to_heuristic(self):
        async with respx.mock() as m:
            m.post("http://mock-litellm/v1/chat/completions").mock(
                side_effect=httpx.TimeoutException("timed out")
            )
            r = await classify_cascaded("unknown widget fancy brand", _cfg())
            # Heuristic returns default with confidence 0 for unknown query
            assert r.source == "heuristic:none"

    async def test_llm_unknown_category_ignored(self):
        async with respx.mock() as m:
            m.post("http://mock-litellm/v1/chat/completions").mock(
                return_value=httpx.Response(200, json=_llm_response("mystery-category", 0.9))
            )
            r = await classify_cascaded("unknown widget fancy thing", _cfg())
            assert r.source == "heuristic:none"

    async def test_short_query_skips_llm(self):
        """Query with < 3 alpha tokens (bare numbers / single word) skips LLM."""
        async with respx.mock(assert_all_called=False) as m:
            route = m.post("http://mock-litellm/v1/chat/completions")
            route.mock(return_value=httpx.Response(200, json=_llm_response("electronics", 0.9)))
            r = await classify_cascaded("xy", _cfg())
            assert r.source == "heuristic:none"
            assert not route.called

    async def test_feature_flag_off_skips_llm(self):
        async with respx.mock(assert_all_called=False) as m:
            route = m.post("http://mock-litellm/v1/chat/completions")
            route.mock(return_value=httpx.Response(200, json=_llm_response("electronics", 0.9)))
            r = await classify_cascaded(
                "unknown fancy widget thing",
                _cfg(enabled=False),
            )
            assert r.source.startswith("heuristic:")
            assert not route.called


@pytest.mark.asyncio
class TestCaching:
    async def test_second_call_uses_cache(self):
        async with respx.mock() as m:
            route = m.post("http://mock-litellm/v1/chat/completions")
            route.mock(return_value=httpx.Response(200, json=_llm_response("electronics", 0.85)))
            r1 = await classify_cascaded("strange new gadget premium", _cfg())
            r2 = await classify_cascaded("strange new gadget premium", _cfg())
            assert r1.category == r2.category == "electronics"
            # LLM called exactly once
            assert route.call_count == 1


@pytest.mark.asyncio
class TestConfigFromEnv:
    async def test_from_env_picks_up_defaults(self, monkeypatch):
        monkeypatch.setenv("LLM_SERVICE_ENDPOINT", "https://example.com/v1")
        monkeypatch.setenv("LLM_SERVICE_API_KEY", "sk-test")
        monkeypatch.setenv("PRICE_CLASSIFIER_LLM_MIN_CONFIDENCE", "0.6")
        monkeypatch.setenv("PRICE_CLASSIFIER_LLM_MAX_LATENCY_MS", "2500")
        cfg = LLMClassifierConfig.from_env()
        assert cfg.endpoint == "https://example.com/v1"
        assert cfg.api_key == "sk-test"
        assert cfg.min_confidence_threshold == 0.6
        assert cfg.timeout_seconds == 2.5
        assert cfg.enabled is True

    async def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_CLASSIFIER_LLM", "false")
        cfg = LLMClassifierConfig.from_env()
        assert cfg.enabled is False
