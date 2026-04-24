"""Wikidata SPARQL enrichment provider (free, no auth).

Resolves ISBNs and EANs/GTINs via the public SPARQL endpoint at
`query.wikidata.org/sparql`. Strong for books, electronics,
automotive parts -- anything curators have catalogued.

Design:
  - Single SPARQL query per call, 2.5 s timeout, fail-silent.
  - JSON format (`Accept: application/sparql-results+json`).
  - ISBN: prefers label + author + publisher for book identification.
  - EAN/GTIN: prefers label + manufacturer.
  - Returns the same `EnrichmentResult` shape as OpenFoodFacts so
    downstream code can treat the two providers uniformly.

Public surface:
  async fetch_isbn(session, isbn)    -> EnrichmentResult | None
  async fetch_ean(session, ean)      -> EnrichmentResult | None
  async fetch(session, code)         -> EnrichmentResult | None
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .openfoodfacts import EnrichmentResult

log = logging.getLogger(__name__)

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
DEFAULT_TIMEOUT_S = 2.5
USER_AGENT = "price-comparison-mcp/1.0.0 (+https://github.com/martensa)"


# SPARQL templates. Using `OPTIONAL` for rarely-populated fields so a
# partial match still returns a result.

_ISBN_QUERY = """
SELECT ?item ?itemLabel ?authorLabel ?publisherLabel WHERE {{
  {{ ?item wdt:P212 "{isbn13}" . }}
  UNION
  {{ ?item wdt:P957 "{isbn10}" . }}
  OPTIONAL {{ ?item wdt:P50 ?author . }}
  OPTIONAL {{ ?item wdt:P123 ?publisher . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en" . }}
}}
LIMIT 1
"""

# P3962 = GTIN-14, P3161 = EAN-13 (the two properties Wikidata uses)
_EAN_QUERY = """
SELECT ?item ?itemLabel ?manufacturerLabel WHERE {{
  {{ ?item wdt:P3162 "{ean}" . }}
  UNION
  {{ ?item wdt:P3161 "{ean}" . }}
  OPTIONAL {{ ?item wdt:P176 ?manufacturer . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en" . }}
}}
LIMIT 1
"""


def _extract_binding(binding: dict[str, Any], key: str) -> str:
    obj = binding.get(key)
    if isinstance(obj, dict):
        val = obj.get("value", "")
        if isinstance(val, str):
            return val.strip()
    return ""


async def _run_sparql(
    session: httpx.AsyncClient | None,
    query: str,
    *,
    timeout: float,
) -> list[dict[str, Any]] | None:
    """Execute a SPARQL SELECT, return bindings list or None."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/sparql-results+json",
    }
    params = {"query": query, "format": "json"}

    owns_client = session is None
    client = session or httpx.AsyncClient(timeout=timeout, headers=headers)
    try:
        response = await asyncio.wait_for(
            client.get(WIKIDATA_ENDPOINT, params=params, headers=headers),
            timeout=timeout,
        )
    except (httpx.HTTPError, asyncio.TimeoutError, Exception) as exc:
        log.debug("wikidata sparql failed: %s", exc)
        if owns_client:
            try:
                await client.aclose()
            except Exception:
                pass
        return None

    if owns_client:
        try:
            await client.aclose()
        except Exception:
            pass

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except Exception:
        return None

    results = data.get("results")
    if not isinstance(results, dict):
        return None
    bindings = results.get("bindings")
    if not isinstance(bindings, list):
        return None
    return bindings


async def fetch_isbn(
    session: httpx.AsyncClient | None,
    isbn: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> EnrichmentResult | None:
    """Lookup a book by ISBN-13 (and its -10 form) on Wikidata."""
    if not isbn:
        return None
    digits = "".join(c for c in isbn if c.isdigit() or c.upper() == "X")
    if len(digits) not in (10, 13):
        return None

    isbn13 = digits if len(digits) == 13 else ""
    isbn10 = digits if len(digits) == 10 else ""
    query = _ISBN_QUERY.format(isbn13=isbn13, isbn10=isbn10)

    bindings = await _run_sparql(session, query, timeout=timeout)
    if not bindings:
        return None
    b = bindings[0]
    title = _extract_binding(b, "itemLabel")
    author = _extract_binding(b, "authorLabel")
    publisher = _extract_binding(b, "publisherLabel")
    if not title:
        return None

    # Compact product_name: "Title - Author (Publisher)"
    parts = [title]
    if author:
        parts.append(f"- {author}")
    if publisher:
        parts.append(f"({publisher})")
    product_name = " ".join(parts)

    return EnrichmentResult(
        source="wikidata",
        ean=digits,
        brand=publisher,
        product_name=product_name,
        quantity="",
        category_hint="book_media",
    )


async def fetch_ean(
    session: httpx.AsyncClient | None,
    ean: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> EnrichmentResult | None:
    """Lookup a product by EAN-13 / GTIN-14 on Wikidata."""
    if not ean or not ean.isdigit():
        return None
    if len(ean) not in (12, 13, 14):
        return None

    query = _EAN_QUERY.format(ean=ean)
    bindings = await _run_sparql(session, query, timeout=timeout)
    if not bindings:
        return None
    b = bindings[0]
    name = _extract_binding(b, "itemLabel")
    manufacturer = _extract_binding(b, "manufacturerLabel")
    if not name:
        return None

    return EnrichmentResult(
        source="wikidata",
        ean=ean,
        brand=manufacturer,
        product_name=name,
        quantity="",
        category_hint="",
    )


async def fetch(
    session: httpx.AsyncClient | None,
    code: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> EnrichmentResult | None:
    """Dispatch on code length: ISBN-10/13 vs. EAN/GTIN."""
    if not code:
        return None
    digits = "".join(c for c in code if c.isdigit() or c.upper() == "X")
    if len(digits) == 10:
        return await fetch_isbn(session, digits, timeout=timeout)
    if len(digits) == 13 and digits.startswith(("978", "979")):
        # ISBN-13 form: try ISBN query first, then EAN query as fallback
        r = await fetch_isbn(session, digits, timeout=timeout)
        if r is not None:
            return r
        return await fetch_ean(session, digits, timeout=timeout)
    if len(digits) in (12, 13, 14):
        return await fetch_ean(session, digits, timeout=timeout)
    return None
