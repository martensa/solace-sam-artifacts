"""EAN-reverse lookup on open B2B shops (httpx parallel, no Playwright).

When the input query is a valid barcode AND the SearXNG discovery
round returned few or no offers, we directly probe a curated list of
open-pricing B2B shops by feeding the EAN into their internal search
endpoints. Each shop's response is scraped for JSON-LD Product offers,
microdata, or a simple SERP-row pattern.

Design:
  - All 8 probes run in parallel via asyncio.gather.
  - 2 s per-shop timeout, 4 s total wall-clock budget.
  - Per-shop extractor lives as a small dataclass entry; adding a new
    shop is one entry + one extractor callable.
  - Returns a list of lightweight offer dicts compatible with the
    main pipeline's `_build_offer_dict` output shape.

Public surface:
  async lookup(ean)            -> list[dict]     top-level entry
  SUPPORTED_SHOPS              -> list[str]       diagnostics
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus

import httpx

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 2.0
TOTAL_BUDGET_S = 4.0
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


# JSON-LD Product extractor -- common across multi-tenant platforms.
# Matches the first `"price":<number>` inside the first JSON-LD block
# with `"@type":"Product"` (or `"Offer"`).
_JSONLD_BLOCK = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(?P<body>.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
_JSONLD_PRICE = re.compile(
    r'"price"\s*:\s*"?(?P<price>\d+(?:[.,]\d+)?)"?',
)
_JSONLD_CURRENCY = re.compile(
    r'"priceCurrency"\s*:\s*"(?P<cur>[A-Z]{3})"',
)

# Generic DE-locale price regex: "1.234,56 EUR" or "1234,56 EUR"
_DE_PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*(?:EUR|Euro|€)", re.IGNORECASE)
_EN_PRICE = re.compile(r"(?:EUR|€)\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:EUR|€)", re.IGNORECASE)


def _parse_price_de(text: str) -> float | None:
    """Parse German-format price: '1.234,56' -> 1234.56."""
    try:
        return float(text.replace(".", "").replace(",", "."))
    except Exception:
        return None


def _parse_price_en(text: str) -> float | None:
    try:
        # Already dot-decimal or comma-decimal -- heuristic:
        if "," in text and "." in text:
            # German format "1.234,56"
            return float(text.replace(".", "").replace(",", "."))
        if "," in text and "." not in text:
            # German without thousands: "1234,56"
            return float(text.replace(",", "."))
        return float(text)
    except Exception:
        return None


def _extract_jsonld_price(html: str) -> tuple[float | None, str]:
    """Extract the first JSON-LD Product/Offer price + currency."""
    for m in _JSONLD_BLOCK.finditer(html):
        body = m.group("body")
        # Only consider JSON-LD blocks that actually reference a product
        if '"Product"' not in body and '"Offer"' not in body:
            continue
        price_m = _JSONLD_PRICE.search(body)
        if not price_m:
            continue
        price = _parse_price_en(price_m.group("price"))
        if price is None:
            continue
        cur_m = _JSONLD_CURRENCY.search(body)
        currency = cur_m.group("cur") if cur_m else "EUR"
        return price, currency
    return None, "EUR"


def _extract_text_price_de(html: str) -> float | None:
    """Fallback: first German-format price in visible HTML."""
    m = _DE_PRICE.search(html)
    if not m:
        return None
    return _parse_price_de(m.group(1))


def _build_offer(
    shop: str,
    merchant: str,
    price: float,
    currency: str,
    url: str,
    source: str,
) -> dict[str, Any]:
    """Shape-compatible with the main pipeline's offer dict."""
    return {
        "merchant": merchant,
        "price": price,
        "shipping_cost": 0.0,
        "total_price": price,
        "currency": currency,
        "url": url,
        "source": source,
        "match_confidence": "medium",        # EAN match is strong but not
                                             # title-verified; mark medium
        "availability": "",
        "price_source": "json_ld" if source.endswith(":jsonld") else "regex",
    }


# -----------------------------------------------------------------------------
# Per-shop extractors
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ShopProbe:
    """A single shop's EAN-reverse probe."""

    name: str                                   # stable shop id for logs
    merchant: str                               # display name
    url_template: str                           # with "{ean}" placeholder
    extract: Callable[[str, str], float | None]  # (html, ean) -> price

    def url(self, ean: str) -> str:
        return self.url_template.format(ean=quote_plus(ean))


def _jsonld_first(html: str, _ean: str) -> float | None:
    price, _ = _extract_jsonld_price(html)
    return price


def _text_price_de(html: str, _ean: str) -> float | None:
    return _extract_text_price_de(html)


def _jsonld_or_text(html: str, _ean: str) -> float | None:
    price, _ = _extract_jsonld_price(html)
    if price is not None:
        return price
    return _extract_text_price_de(html)


# Curated list: each shop has open-pricing EAN search endpoints and
# stable URL contracts. All 8 are verified to accept direct ?q=<EAN>
# or equivalent search-parameter queries.
SHOPS: list[ShopProbe] = [
    ShopProbe(
        name="conrad",
        merchant="Conrad.de",
        url_template="https://www.conrad.de/de/search.html?search={ean}",
        extract=_jsonld_or_text,
    ),
    ShopProbe(
        name="reichelt",
        merchant="Reichelt.de",
        url_template="https://www.reichelt.de/index.html?ACTION=446&LA=3&nbc=1&q={ean}",
        extract=_text_price_de,
    ),
    ShopProbe(
        name="voelkner",
        merchant="Voelkner.de",
        url_template="https://www.voelkner.de/search/search.html?q={ean}",
        extract=_jsonld_or_text,
    ),
    ShopProbe(
        name="distrelec",
        merchant="Distrelec.de",
        url_template="https://www.distrelec.de/de/search/?q={ean}",
        extract=_jsonld_or_text,
    ),
    ShopProbe(
        name="automation24",
        merchant="Automation24.de",
        url_template="https://www.automation24.de/catalogsearch/result/?q={ean}",
        extract=_jsonld_or_text,
    ),
    ShopProbe(
        name="pollin",
        merchant="Pollin.de",
        url_template="https://www.pollin.de/search?query={ean}",
        extract=_jsonld_or_text,
    ),
    ShopProbe(
        name="rs-online",
        merchant="RS-Online.com",
        url_template="https://de.rs-online.com/web/c/?searchTerm={ean}",
        extract=_jsonld_or_text,
    ),
    ShopProbe(
        name="buerklin",
        merchant="Buerklin.com",
        url_template="https://www.buerklin.com/de/catalogsearch/result/?q={ean}",
        extract=_jsonld_or_text,
    ),
]

SUPPORTED_SHOPS = [s.name for s in SHOPS]


# -----------------------------------------------------------------------------
# Parallel probe driver
# -----------------------------------------------------------------------------


async def _probe(
    client: httpx.AsyncClient,
    shop: ShopProbe,
    ean: str,
    *,
    timeout: float,
) -> dict[str, Any] | None:
    """Fetch a shop's EAN search page; return an offer dict on hit."""
    url = shop.url(ean)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }
    try:
        resp = await asyncio.wait_for(
            client.get(url, headers=headers, follow_redirects=True),
            timeout=timeout,
        )
    except (httpx.HTTPError, asyncio.TimeoutError, Exception) as exc:
        log.debug("shop_ean_lookup probe %s failed: %s", shop.name, exc)
        return None

    if resp.status_code != 200:
        return None

    html = resp.text
    if not html or len(html) < 200:
        return None

    try:
        price = shop.extract(html, ean)
    except Exception as exc:
        log.debug("shop_ean_lookup extractor %s error: %s", shop.name, exc)
        return None
    if price is None or price <= 0:
        return None

    # Prefer the final URL after redirects -- often the product detail
    # page rather than the search SERP.
    final_url = str(resp.url) if resp.url else url

    # Heuristic source label: jsonld vs regex
    _jp, _ = _extract_jsonld_price(html)
    source_kind = "jsonld" if _jp is not None else "regex"

    return _build_offer(
        shop=shop.name,
        merchant=shop.merchant,
        price=price,
        currency="EUR",
        url=final_url,
        source=f"shop_ean:{source_kind}",
    )


async def lookup(
    ean: str,
    *,
    total_budget: float = TOTAL_BUDGET_S,
    per_shop_timeout: float = DEFAULT_TIMEOUT_S,
    shops: list[ShopProbe] | None = None,
) -> list[dict[str, Any]]:
    """Parallel EAN lookup on all supported shops.

    Parameters
    ----------
    ean : str
        A normalised GTIN-8 / UPC-12 / EAN-13 / GTIN-14 string. Must
        be pure digits.
    total_budget : float
        Hard cap for the whole batch. Probes that don't finish within
        this window are cancelled and their slot returns None.
    per_shop_timeout : float
        Per-shop timeout. Kept tight so one slow shop can't drag the
        others down.

    Returns
    -------
    list[dict]
        Zero to len(shops) offer dicts with shape-compatible keys for
        the main pipeline. Empty list on any wholesale failure.
    """
    if not ean or not ean.isdigit() or len(ean) not in (8, 12, 13, 14):
        return []

    probe_shops = shops if shops is not None else SHOPS
    async with httpx.AsyncClient(
        timeout=per_shop_timeout,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        tasks = [
            _probe(client, s, ean, timeout=per_shop_timeout)
            for s in probe_shops
        ]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=total_budget,
            )
        except asyncio.TimeoutError:
            log.debug("shop_ean_lookup total budget exceeded (ean=%s)", ean)
            return []

    offers: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, dict):
            offers.append(r)
    return offers
