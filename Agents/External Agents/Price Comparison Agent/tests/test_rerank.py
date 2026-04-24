"""Tests for the LLM reranker with mocked LiteLLM."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from price_comparison_mcp.discovery.rerank import (
    Candidate,
    RerankerConfig,
    rerank,
)


def _cfg(enabled: bool = True, top_k: int = 10) -> RerankerConfig:
    return RerankerConfig(
        endpoint="http://mock-litellm/v1",
        api_key="mock-key",
        model="openai/claude-sonnet-4-6",
        timeout_seconds=3.0,
        top_k_keep=top_k,
        enabled=enabled,
    )


def _mk_candidates(n: int) -> list[Candidate]:
    return [
        Candidate(
            url=f"https://example.de/page-{i}",
            title=f"Page {i}",
            snippet=f"snippet {i}",
            score=100 - i,
        )
        for i in range(n)
    ]


def _llm_response(indices: list[int]) -> dict:
    return {
        "choices": [{
            "message": {
                "content": json.dumps({"top_k": indices}),
            },
        }],
    }


@pytest.mark.asyncio
class TestRerankBehaviour:
    async def test_disabled_returns_score_sorted(self):
        cands = _mk_candidates(30)
        out = await rerank("query", cands, _cfg(enabled=False))
        assert len(out) == 10
        # Score-sorted: candidate 0 has highest score
        assert out[0].url.endswith("page-0")
        assert out[-1].url.endswith("page-9")

    async def test_few_candidates_no_llm_call(self):
        async with respx.mock(assert_all_called=False) as m:
            route = m.post("http://mock-litellm/v1/chat/completions")
            route.mock(return_value=httpx.Response(200, json=_llm_response([])))
            cands = _mk_candidates(5)
            out = await rerank("query", cands, _cfg())
            # <10 candidates: skip LLM, return all
            assert not route.called
            assert len(out) == 5

    async def test_llm_reorders(self):
        """LLM picks indices [29, 28, ..., 20] -- tail in reverse."""
        async with respx.mock() as m:
            m.post("http://mock-litellm/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json=_llm_response([29, 28, 27, 26, 25, 24, 23, 22, 21, 20]),
                )
            )
            cands = _mk_candidates(30)
            out = await rerank("query", cands, _cfg())
            assert [c.url for c in out] == [
                f"https://example.de/page-{i}" for i in range(29, 19, -1)
            ]

    async def test_llm_timeout_falls_back_to_score_order(self):
        async with respx.mock() as m:
            m.post("http://mock-litellm/v1/chat/completions").mock(
                side_effect=httpx.TimeoutException("timed out")
            )
            cands = _mk_candidates(30)
            out = await rerank("query", cands, _cfg())
            # Fallback: score-sorted top-10 (pages 0..9)
            assert [c.url for c in out] == [
                f"https://example.de/page-{i}" for i in range(10)
            ]

    async def test_llm_bad_json_falls_back(self):
        async with respx.mock() as m:
            m.post("http://mock-litellm/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={
                    "choices": [{"message": {"content": "not-json"}}],
                })
            )
            cands = _mk_candidates(30)
            out = await rerank("query", cands, _cfg())
            assert len(out) == 10
            assert out[0].url.endswith("page-0")

    async def test_llm_partial_output_padded(self):
        """LLM returns only 5 indices -- rerank tops up with score_sorted."""
        async with respx.mock() as m:
            m.post("http://mock-litellm/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200, json=_llm_response([25, 26, 27, 28, 29])
                )
            )
            cands = _mk_candidates(30)
            out = await rerank("query", cands, _cfg())
            assert len(out) == 10
            # First 5 are the LLM's picks
            assert out[0].url.endswith("page-25")
            # Then pad with highest-score non-picked (page-0, 1, 2, 3, 4)
            pad_urls = [c.url for c in out[5:]]
            assert all("page-" in u for u in pad_urls)


@pytest.mark.asyncio
class TestConfigFromEnv:
    async def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("PRICE_ENABLE_LLM_RERANKER", raising=False)
        cfg = RerankerConfig.from_env()
        assert cfg.enabled is False

    async def test_enable_via_env(self, monkeypatch):
        monkeypatch.setenv("PRICE_ENABLE_LLM_RERANKER", "true")
        monkeypatch.setenv("PRICE_RERANKER_TOP_K", "15")
        cfg = RerankerConfig.from_env()
        assert cfg.enabled is True
        assert cfg.top_k_keep == 15
