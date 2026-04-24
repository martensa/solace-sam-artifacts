"""LLM cross-encoder reranker for SearXNG discovery results (v1.0).

After Phase 1 of the pipeline we have ~30 candidate URLs from SearXNG
+ optional shopping backends. The score-based ranking (`_score_url`)
uses domain tier + profile overlay + blacklist, but does NOT look at
each candidate's TITLE or SNIPPET.

The reranker feeds (query, [candidate]) into an LLM and asks for the
top-N most-likely-relevant. That's a classic RAG cross-encoder task.

Cost:
  - 1 LLM call per query (IF reranker enabled + >=10 candidates)
  - Prompt size grows with candidates; we cap at top 30 to keep < 2KB
  - Timeout 3s default; on timeout, falls back to the score-based order.

Feature-flag-gated (`PRICE_ENABLE_LLM_RERANKER`) -- default OFF. Enable
in production once A/B metrics justify the latency.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx


logger = logging.getLogger("price-comparison-mcp.rerank")


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str = ""
    snippet: str = ""
    score: int = 0  # pre-existing _score_url value


@dataclass(frozen=True)
class RerankerConfig:
    endpoint: str = ""
    api_key: str = ""
    model: str = "openai/claude-sonnet-4-6"
    timeout_seconds: float = 3.0
    top_k_keep: int = 10
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "RerankerConfig":
        return cls(
            endpoint=os.environ.get("LLM_SERVICE_ENDPOINT", ""),
            api_key=os.environ.get("LLM_SERVICE_API_KEY", ""),
            model=os.environ.get("LLM_SERVICE_GENERAL_MODEL_NAME", "openai/claude-sonnet-4-6"),
            timeout_seconds=float(os.environ.get("PRICE_RERANKER_TIMEOUT_MS", "3000")) / 1000.0,
            top_k_keep=int(os.environ.get("PRICE_RERANKER_TOP_K", "10")),
            enabled=os.environ.get("PRICE_ENABLE_LLM_RERANKER", "false").lower() == "true",
        )


_SYSTEM_PROMPT = """You are a relevance ranker for a procurement price agent.

Given a product QUERY and up to 30 SearXNG candidates (URL + title +
snippet), return the INDICES of the top-K most likely to be PRODUCT
PAGES for the exact product the query asks about.

Rules:
  - Answer with strict JSON: {"top_k": [<idx>, <idx>, ...]}
  - Include the best candidate first. Length must equal K.
  - Prefer direct product pages (known retailers, SKU in URL) over
    category / search / blog / forum URLs.
  - Exclude URLs that obviously describe a DIFFERENT product variant
    (e.g. query says "Bosch GBH 2-26 F" and URL says "gbh-18v-26-f").
  - Exclude login-wall or news/press pages.
  - Do NOT return anything outside the JSON object.
"""


def _format_candidates(candidates: list[Candidate]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        line = f"[{i}] {c.title or '(no title)'}"
        if c.snippet:
            s = c.snippet.replace("\n", " ").strip()
            line += f" -- {s[:120]}"
        line += f"\n     {c.url[:160]}"
        lines.append(line)
    return "\n".join(lines)


async def _call_llm_rerank(
    query: str,
    candidates: list[Candidate],
    cfg: RerankerConfig,
) -> list[int] | None:
    """Returns a list of candidate INDICES in descending relevance, or None."""
    if not cfg.endpoint or not cfg.api_key:
        logger.debug("rerank: endpoint/key missing, skipping")
        return None
    if len(candidates) <= cfg.top_k_keep:
        # Nothing to prune
        return list(range(len(candidates)))

    user_content = (
        f"QUERY: {query}\n\n"
        f"CANDIDATES (K={cfg.top_k_keep}):\n{_format_candidates(candidates)}"
    )

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 200,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    url = cfg.endpoint.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            body = r.json()
    except (httpx.TimeoutException, asyncio.TimeoutError):
        logger.warning("rerank: timed out after %.1fs", cfg.timeout_seconds)
        return None
    except Exception as e:
        logger.warning("rerank: failed: %s", e)
        return None

    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        indices = parsed.get("top_k", [])
        # Filter / clamp indices to the valid range
        valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(candidates)]
        if not valid:
            return None
        return valid[: cfg.top_k_keep]
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("rerank: unparseable body: %s", e)
        return None


async def rerank(
    query: str,
    candidates: list[Candidate],
    cfg: RerankerConfig | None = None,
) -> list[Candidate]:
    """Rerank candidates using the LLM cross-encoder. Fail-open.

    When disabled or failed: returns `candidates[:top_k_keep]` sorted
    by the incoming `.score` (stable, order-preserving).
    """
    if cfg is None:
        cfg = RerankerConfig.from_env()

    # Fail-open defaults (score-based top-K)
    score_sorted = sorted(candidates, key=lambda c: -c.score)
    fallback = score_sorted[: cfg.top_k_keep]

    if not cfg.enabled:
        return fallback
    if len(candidates) < 10:
        # Too few to warrant an LLM call -- pruning would be arbitrary
        return fallback

    indices = await _call_llm_rerank(query, candidates, cfg)
    if indices is None:
        return fallback

    # Map indices back to Candidate objects, preserving LLM order
    reranked = [candidates[i] for i in indices]
    # If LLM returned fewer than top_k, pad with top score_sorted items
    # that weren't already picked.
    if len(reranked) < cfg.top_k_keep:
        picked_urls = {c.url for c in reranked}
        for c in score_sorted:
            if c.url not in picked_urls:
                reranked.append(c)
                if len(reranked) >= cfg.top_k_keep:
                    break
    logger.info(
        "[rerank] query=%r reordered %d -> %d candidates",
        query[:60], len(candidates), len(reranked),
    )
    return reranked
