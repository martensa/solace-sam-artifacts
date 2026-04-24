"""Dispatcher that cascades free enrichment providers.

Called at pipeline ingress when a barcode-like query is detected.
Tries providers in order of their best hit-rate-per-latency and
returns the first non-null result.

Order:
  1. OpenFoodFacts  -- food / beverage / cosmetics (very fast)
  2. Wikidata       -- books / electronics / automotive / general
  3. (future) ean-search.org when a paid token is available

Each provider is capped at its own hard timeout; the dispatcher has
a total budget of `total_timeout`. Fail-silent: all errors return
None and the caller must work without enrichment.

Public surface:
  async enrich(client, code, total_timeout=5.0)  -> EnrichmentResult | None
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from . import openfoodfacts, wikidata
from .openfoodfacts import EnrichmentResult

log = logging.getLogger(__name__)

DEFAULT_TOTAL_TIMEOUT_S = 5.0


async def enrich(
    client: httpx.AsyncClient | None,
    code: str,
    *,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT_S,
) -> EnrichmentResult | None:
    """Cascade providers; return the first hit.

    Both providers are attempted even if the first returns None --
    OpenFoodFacts covers food/cosmetics, Wikidata covers everything
    else, so they are largely complementary.
    """
    if not code:
        return None

    digits = "".join(c for c in code if c.isdigit() or c.upper() == "X")
    if len(digits) not in (8, 10, 12, 13, 14):
        return None

    # ISBN-10 is Wikidata-only (OFF does not index books reliably).
    if len(digits) == 10:
        try:
            return await asyncio.wait_for(
                wikidata.fetch(client, digits),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            return None

    # Run OFF first with half the budget, fall through to Wikidata.
    half = total_timeout / 2.0
    result: EnrichmentResult | None = None
    try:
        result = await asyncio.wait_for(
            openfoodfacts.fetch(client, digits, timeout=half),
            timeout=half,
        )
    except asyncio.TimeoutError:
        pass
    except Exception as exc:
        log.debug("openfoodfacts error: %s", exc)

    if result is not None:
        return result

    try:
        return await asyncio.wait_for(
            wikidata.fetch(client, digits, timeout=half),
            timeout=half,
        )
    except asyncio.TimeoutError:
        return None
    except Exception as exc:
        log.debug("wikidata error: %s", exc)
        return None
