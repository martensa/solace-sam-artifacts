"""Locale detection + per-locale query templating (v1.0 foundation).

Agent input can arrive in any language; the scoring and query-expansion
heuristics are DE-first today. This module adds:

  - `detector` -- heuristic language detection (umlaut / sz / char-set)
    with langdetect as a second-opinion fallback. Returns ISO 2-letter
    codes (de / en / fr / es / ...).

  - `templates` -- per-(category, locale) query-expansion templates.
    Falls back category -> default and locale -> en when a specific
    combination is unregistered. Keeps the existing DE behaviour as
    the `(default, de)` entry for zero-regression.

The detector output also routes aggregator URLs to the right TLD
(amazon.de vs amazon.fr vs amazon.com) and sets SearXNG's `language=`
parameter per request.
"""
from __future__ import annotations

__all__: list[str] = []
