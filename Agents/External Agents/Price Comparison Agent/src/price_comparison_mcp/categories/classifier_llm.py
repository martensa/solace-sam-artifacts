"""Stage-3 LLM classifier -- cascaded fallback when heuristics are uncertain.

Kicks in only when:
  - Stage-1 heuristics returned confidence < `min_confidence` (default 0.5)
  - Query contains >=3 alpha tokens (LLM is pointless on bare EANs)
  - LiteLLM endpoint is reachable (3s timeout, fail-open to default)

The LLM is asked to pick ONE category from the registry with a short
justification. JSON-schema enforced via `response_format={"type":"json_object"}`.

Cost control:
  - LRU in-memory cache (10k entries)
  - SQLite persistence (`/app/data/classifier.db`) with TTL 30 days,
    cross-pod via S3 snapshot (not wired until beta1)

Latency budget: max 3s. On timeout / API error, returns the Stage-1
result unchanged so the pipeline never blocks on classification.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass

import httpx

from .classifier import ClassificationResult, classify as classify_heuristic
from .registry import CategoryRegistry


logger = logging.getLogger("price-comparison-mcp.classifier_llm")


# -----------------------------------------------------------------------------
# In-memory LRU cache. SQLite persistence lands in beta1.
# -----------------------------------------------------------------------------


class _LRUCache:
    """Minimal LRU (no deps). 10k entries * 80B ~= <1MB memory."""

    def __init__(self, maxsize: int = 10000) -> None:
        self.maxsize = maxsize
        self._data: dict[str, tuple[ClassificationResult, float]] = {}

    def get(self, key: str, ttl: float) -> ClassificationResult | None:
        entry = self._data.get(key)
        if not entry:
            return None
        result, stored_at = entry
        if time.time() - stored_at > ttl:
            self._data.pop(key, None)
            return None
        # Move to end (LRU touch)
        self._data.pop(key)
        self._data[key] = entry
        return result

    def set(self, key: str, value: ClassificationResult) -> None:
        self._data[key] = (value, time.time())
        # Evict oldest entries
        while len(self._data) > self.maxsize:
            self._data.pop(next(iter(self._data)))


_CACHE = _LRUCache()
_CACHE_TTL_SECONDS = 30 * 86400  # 30 days


def _cache_key(query: str) -> str:
    return hashlib.sha1(query.lower().strip().encode()).hexdigest()


# -----------------------------------------------------------------------------
# Prompt assembly
# -----------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """You are a product-category classifier for a B2B procurement agent.

Given a product query (name, brand, or barcode), pick the single BEST
category from this list:

{categories}

Rules:
  - Answer in strict JSON: {{"category": "<key>", "confidence": <0.0-1.0>, "reasoning": "<one short sentence>"}}
  - `category` MUST be one of the keys above.
  - `confidence` is your own subjective certainty. Use <= 0.5 when the
    query is generic / ambiguous. Use >= 0.8 when the brand or vocabulary
    is unambiguously domain-specific.
  - `reasoning` is at most 20 words.
  - Do NOT return any text outside the JSON object.
"""


def _build_system_prompt() -> str:
    CategoryRegistry.reset()
    reg = CategoryRegistry.instance()
    keys = sorted(reg.keys())
    bullets = "\n".join(f"  - {k}: {reg.get(k).display_name}" for k in keys)
    return _SYSTEM_PROMPT_TEMPLATE.format(categories=bullets)


# -----------------------------------------------------------------------------
# LLM call
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMClassifierConfig:
    endpoint: str = ""
    api_key: str = ""
    model: str = "openai/claude-sonnet-4-6"
    timeout_seconds: float = 3.0
    min_confidence_threshold: float = 0.5
    min_alpha_tokens: int = 3
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "LLMClassifierConfig":
        return cls(
            endpoint=os.environ.get("LLM_SERVICE_ENDPOINT", ""),
            api_key=os.environ.get("LLM_SERVICE_API_KEY", ""),
            model=os.environ.get("LLM_SERVICE_GENERAL_MODEL_NAME", "openai/claude-sonnet-4-6"),
            timeout_seconds=float(os.environ.get("PRICE_CLASSIFIER_LLM_MAX_LATENCY_MS", "3000")) / 1000.0,
            min_confidence_threshold=float(os.environ.get("PRICE_CLASSIFIER_LLM_MIN_CONFIDENCE", "0.5")),
            enabled=os.environ.get("PRICE_ENABLE_CLASSIFIER_LLM", "true").lower() == "true",
        )


async def _call_litellm(
    query: str,
    cfg: LLMClassifierConfig,
) -> ClassificationResult | None:
    """Single LLM roundtrip, JSON-only response parsed to ClassificationResult.

    Returns None on any failure (timeout, HTTP error, bad JSON, unknown
    category). Callers fall back to the heuristic result.
    """
    if not cfg.endpoint or not cfg.api_key:
        logger.debug("LLM classifier skipped: endpoint/key missing")
        return None

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": query},
        ],
        "max_tokens": 120,
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
        logger.warning("LLM classifier timed out after %.1fs for %r",
                       cfg.timeout_seconds, query[:60])
        return None
    except Exception as e:
        logger.warning("LLM classifier failed: %s", e)
        return None

    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        category = parsed.get("category", "default")
        confidence = float(parsed.get("confidence", 0.0))
        reasoning = parsed.get("reasoning", "")
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("LLM classifier returned unparseable body: %s", e)
        return None

    # Validate category is in the registry -- otherwise fall back
    reg = CategoryRegistry.instance()
    if category not in reg.keys():
        logger.warning("LLM returned unknown category %r, ignoring", category)
        return None

    return ClassificationResult(
        category=category,
        confidence=max(0.0, min(1.0, confidence)),
        source="llm",
        details=reasoning[:120],
    )


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def _should_use_llm(
    heuristic: ClassificationResult,
    query: str,
    cfg: LLMClassifierConfig,
) -> bool:
    """Gate decisions:
      - Feature flag on
      - Heuristic confidence below threshold
      - Query has enough alpha tokens for LLM to be useful
    """
    if not cfg.enabled:
        return False
    if heuristic.confidence >= cfg.min_confidence_threshold:
        return False
    # Count alpha tokens of length >=3 (barcodes skip the LLM)
    alpha_tokens = [t for t in query.split() if any(c.isalpha() for c in t) and len(t) >= 3]
    return len(alpha_tokens) >= cfg.min_alpha_tokens


async def classify_cascaded(
    query: str,
    cfg: LLMClassifierConfig | None = None,
) -> ClassificationResult:
    """Run the full cascade: heuristic -> cache -> LLM -> fallback.

    Always returns a valid ClassificationResult; never raises. The
    pipeline is safe to call on every query even when LiteLLM is down.
    """
    if cfg is None:
        cfg = LLMClassifierConfig.from_env()

    # Stage 1: heuristic (instant)
    heuristic = classify_heuristic(query)

    if not _should_use_llm(heuristic, query, cfg):
        return heuristic

    # Stage 1b: cache lookup
    key = _cache_key(query)
    cached = _CACHE.get(key, _CACHE_TTL_SECONDS)
    if cached is not None:
        logger.debug("LLM classifier cache hit for %r -> %s", query[:60], cached.category)
        return cached

    # Stage 3: LLM roundtrip
    llm_result = await _call_litellm(query, cfg)
    if llm_result is None:
        return heuristic

    # Only override if LLM is more confident than the heuristic
    if llm_result.confidence > heuristic.confidence:
        _CACHE.set(key, llm_result)
        logger.info(
            "[classifier_llm] query=%r heuristic=%s(%.2f) -> LLM=%s(%.2f) :: %s",
            query[:60], heuristic.category, heuristic.confidence,
            llm_result.category, llm_result.confidence, llm_result.details[:60],
        )
        return llm_result

    return heuristic
