"""Dynamic domain discovery + LLM reranker (v1.0 foundation).

Two complementary mechanisms for widening coverage beyond the 72
hardcoded `_PRICE_SITE_SCORES` entries:

  - `domain_stats` -- SQLite-backed running-average of domains that
    returned high-confidence offers, with exponential decay. After a
    domain hits 3+ successful high-mc offers within 90 days (half-life
    62d), it gets promoted +50 to the scoring overlay. Keeps the agent
    learning new distributors without code pushes.

  - `rerank` -- optional LLM cross-encoder that reranks the top 30
    SearXNG candidates before Playwright fetch. Shadow-mode first,
    then gated behind PRICE_ENABLE_LLM_RERANKER=true.

Both are opt-in via env; disabled by default during v1.0-alpha, flipped
to shadow in beta, live in final.
"""
from __future__ import annotations

__all__: list[str] = []
