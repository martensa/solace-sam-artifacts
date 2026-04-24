"""OpenFoodFacts enrichment provider (free, no auth).

Queries `world.openfoodfacts.org/api/v2/product/<barcode>.json` and
returns a compact `EnrichmentResult` with brand + product name +
quantity-hint. Covers food, beverages, drogerie / cosmetics worldwide.

Design:
  - Sync surface is async (httpx.AsyncClient) to fit the enrichment
    pipeline without blocking the event loop.
  - 2.5 s hard timeout; fail-silent on any error (returns None). This
    is a best-effort enricher -- downstream must work without it.
  - No caching here; the dispatcher is responsible for caching to
    avoid repeated calls for the same barcode.
  - We pull only the fields we need (`fields=` query parameter) to
    minimise payload size.

Public surface:
  class EnrichmentResult      (typed, frozen)
  async fetch(session, ean)   -> EnrichmentResult | None
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

OPENFOODFACTS_URL = "https://world.openfoodfacts.org/api/v2/product/{ean}.json"
OPENFOODFACTS_FIELDS = (
    "code,product_name,product_name_de,product_name_en,"
    "brands,brands_tags,quantity,categories_tags,"
    "generic_name,generic_name_de,generic_name_en"
)
DEFAULT_TIMEOUT_S = 2.5
USER_AGENT = "price-comparison-mcp/1.0.0 (+https://github.com/martensa)"


@dataclass(frozen=True)
class EnrichmentResult:
    """Enrichment result from a free product database."""

    source: str                 # "openfoodfacts" | "wikidata"
    ean: str
    brand: str = ""
    product_name: str = ""
    quantity: str = ""
    category_hint: str = ""     # our-internal category key if derivable

    def to_query_hint(self) -> str:
        """Render fields into an additive query string.

        Used to boost SearXNG search: original query + hint -> better
        SERP ranking when the barcode alone is uninformative.
        """
        parts: list[str] = []
        if self.brand:
            parts.append(self.brand)
        if self.product_name and self.product_name.lower() != self.brand.lower():
            parts.append(self.product_name)
        if self.quantity:
            parts.append(self.quantity)
        return " ".join(parts).strip()


def _pick(payload: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string for the given key chain."""
    for k in keys:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _category_hint_from_tags(tags: list[str] | None) -> str:
    """Map OpenFoodFacts category tags to our internal category key.

    Coarse-grained heuristic: we only resolve tags into two of the
    higher-confidence buckets (food_beverage, cosmetic_pharma). When
    nothing obvious matches, return "" and let the classifier decide.
    """
    if not tags:
        return ""
    beauty_markers = ("beauty", "cosmetics", "hair", "skin", "shampoo",
                      "deodorant", "fragrance")
    food_markers = ("en:foods", "en:beverages", "en:drinks", "en:milks",
                    "en:cheeses", "en:meats", "en:fruits", "en:vegetables")
    text = " ".join(tags).lower()
    if any(m in text for m in beauty_markers):
        return "cosmetic_pharma"
    if any(m in text for m in food_markers):
        return "food_beverage"
    return ""


async def fetch(
    session: httpx.AsyncClient | None,
    ean: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> EnrichmentResult | None:
    """Fetch product metadata from OpenFoodFacts.

    Parameters
    ----------
    session : httpx.AsyncClient | None
        Shared client for connection pooling. When None a local one is
        spun up for the single call (less efficient but still works).
    ean : str
        Normalised EAN-13 / GTIN-12 / GTIN-8. Must be pure digits.
    timeout : float
        Hard deadline in seconds. Default 2.5 s.

    Returns
    -------
    EnrichmentResult | None
        Never raises. Returns None when the barcode is unknown, the
        HTTP call fails, the response is malformed, or the product has
        no useful free-text fields.
    """
    if not ean or not ean.isdigit():
        return None

    url = OPENFOODFACTS_URL.format(ean=ean)
    params = {"fields": OPENFOODFACTS_FIELDS}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    owns_client = session is None
    client = session or httpx.AsyncClient(timeout=timeout, headers=headers)
    try:
        response = await asyncio.wait_for(
            client.get(url, params=params, headers=headers),
            timeout=timeout,
        )
    except (httpx.HTTPError, asyncio.TimeoutError, Exception) as exc:
        log.debug("openfoodfacts fetch failed for %s: %s", ean, exc)
        if owns_client:
            await client.aclose()
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

    # OFF returns status=0 for "product not found".
    if not isinstance(data, dict) or data.get("status") != 1:
        return None
    product = data.get("product")
    if not isinstance(product, dict):
        return None

    brand = _pick(product, "brands")
    # "brands" is sometimes a comma-joined list; take the first.
    if "," in brand:
        brand = brand.split(",")[0].strip()
    name = _pick(product, "product_name_de", "product_name_en",
                 "product_name", "generic_name_de", "generic_name_en",
                 "generic_name")
    quantity = _pick(product, "quantity")
    tags = product.get("categories_tags")
    if not isinstance(tags, list):
        tags = None
    category_hint = _category_hint_from_tags(tags)

    if not brand and not name:
        return None

    return EnrichmentResult(
        source="openfoodfacts",
        ean=ean,
        brand=brand,
        product_name=name,
        quantity=quantity,
        category_hint=category_hint,
    )
