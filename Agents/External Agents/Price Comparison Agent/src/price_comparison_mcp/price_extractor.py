"""Price extraction from HTML pages using layered strategies.

Priority order:
1. Site-specific CSS selectors for known domains
2. JSON-LD / Schema.org Product markup
3. OpenGraph / Microdata
4. Generic CSS heuristics
5. Regex on visible text
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.async_api import Page

logger = logging.getLogger("price-comparison-mcp.extractor")


# -- Data model ----------------------------------------------------------------

class ExtractedOffer:
    """A price offer extracted from a page.

    Fields beyond the core price/merchant capture procurement-relevant
    context: whether the price is net or gross, whether the page was
    login-gated (even partial), whether tier/bulk pricing is shown on
    the page, and the MOQ if stated. These are best-effort signals
    extracted via simple heuristics -- absence does not prove absence.
    """

    __slots__ = (
        "merchant", "price", "shipping_cost", "currency", "url",
        "availability", "vat_status", "login_required",
        "has_tier_pricing", "min_order_quantity", "tier_pricing",
        "ean", "price_source",
    )

    def __init__(
        self,
        merchant: str,
        price: float,
        shipping_cost: float = 0.0,
        currency: str = "EUR",
        url: str = "",
        availability: str = "",
        vat_status: str = "",
        login_required: bool = False,
        has_tier_pricing: bool = False,
        min_order_quantity: Optional[int] = None,
        tier_pricing: Optional[list[dict[str, Any]]] = None,
        ean: str = "",
        price_source: str = "",
    ) -> None:
        self.merchant = merchant
        self.price = price
        self.shipping_cost = shipping_cost
        self.currency = currency
        self.url = url
        # Normalized availability: "in_stock" / "out_of_stock" /
        # "on_request" / "backorder" / "" (unknown).
        self.availability = availability
        # "net" | "gross" | "unknown" | "" (not detected)
        self.vat_status = vat_status
        self.login_required = login_required
        self.has_tier_pricing = has_tier_pricing
        self.min_order_quantity = min_order_quantity
        # Structured tier pricing list: [{"min_qty": 50, "price": 9.80}, ...]
        self.tier_pricing = tier_pricing
        # EAN / GTIN-13 extracted from the page (exact SKU identifier).
        # When present, this is the strongest possible match signal.
        self.ean = ean
        # v1.0 price-source confidence: WHICH extraction path yielded
        # this price. Feeds the composite confidence score in the
        # pipeline. Values:
        #   "json_ld"     -- schema.org Product JSON-LD (most reliable)
        #   "microdata"   -- itemprop=price HTML attribute
        #   "css_site"    -- site-specific CSS selector (idealo, geizhals)
        #   "css_generic" -- generic [class*=price] fallback
        #   "regex"       -- regex over visible text (weakest)
        #   ""            -- unknown / not set
        self.price_source = price_source

    @property
    def total_price(self) -> float:
        return self.price + self.shipping_cost

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant": self.merchant,
            "price": self.price,
            "shipping_cost": self.shipping_cost,
            "total_price": self.total_price,
            "currency": self.currency,
            "url": self.url,
            "availability": self.availability,
            "vat_status": self.vat_status,
            "login_required": self.login_required,
            "has_tier_pricing": self.has_tier_pricing,
            "min_order_quantity": self.min_order_quantity,
            "tier_pricing": self.tier_pricing,
            "ean": self.ean,
            "price_source": self.price_source,
        }


# -- Price parsing (German locale) --------------------------------------------

def parse_price(text: str) -> Optional[float]:
    """Parse a price string to float. Handles German and English formats.

    Order of attempts is important: the German thousand-separated form
    must be tried BEFORE the simpler decimal regex, because "1.726,95"
    must not be misread as "1.72" (which would be the bug that caused
    Niedax WRL 200.400 F to report 1.72 EUR from a mercateo.com tier
    line "entspricht 1.726,95 EUR"). Similarly, the simple decimal
    regex uses a negative lookahead to refuse matches that would split
    inside a larger locale-formatted number.
    """
    if not text:
        return None
    text = text.replace("EUR", "").replace("Euro", "").strip()
    text = text.replace("\u20ac", "").strip()  # euro sign

    # German format with thousand separator: 1.234,56 or 12.345,67
    match = re.search(r"(\d{1,3}(?:\.\d{3})+,\d{2})", text)
    if match:
        return float(match.group(1).replace(".", "").replace(",", "."))

    # German/simple format with comma decimal: 123,56 (no thousand group)
    match = re.search(r"(?<![\d.])(\d{1,5}),(\d{2})(?!\d)", text)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")

    # English format with thousand separator: 1,234.56
    match = re.search(r"(\d{1,3}(?:,\d{3})+\.\d{2})", text)
    if match:
        return float(match.group(1).replace(",", ""))

    # English/simple format with period decimal: 1234.56 -- the negative
    # lookahead is critical. Without it, "1.726,95" would match "1.72".
    match = re.search(r"(?<![\d,])(\d{1,6})\.(\d{2})(?!\d)", text)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")

    # Integer only (last resort; only used if no decimal price was found)
    match = re.search(r"(?<!\d)(\d{2,6})(?!\d)", text)
    if match:
        val = float(match.group(1))
        if val > 0:
            return val

    return None


def parse_shipping(text: str) -> float:
    """Parse shipping cost text to float. Returns 0.0 for free shipping."""
    text_lower = text.lower()
    if any(w in text_lower for w in ("gratis", "kostenlos", "frei", "0,00", "0.00", "free")):
        return 0.0
    price = parse_price(text)
    return price if price is not None else 0.0


def _is_reasonable_price(price: float) -> bool:
    """Filter out implausible prices (article numbers, zip codes, etc.)."""
    return 0.50 <= price <= 99999.99


# URL patterns that identify specific product pages vs search/category
# listings on known aggregators. Extracting the per-product offer
# list (site-specific containers) only makes sense on a real product
# page; on search result / category / filter pages, those containers
# return offers for many DIFFERENT products, polluting our results
# (this is exactly the "FLUKE 1674FC SCH shows 46 EUR" bug).
_PRODUCT_URL_PATTERNS: dict[str, re.Pattern] = {
    "idealo.de": re.compile(r"/(preisvergleich/)?OffersOfProduct/\d+", re.IGNORECASE),
    # Geizhals product pages end in -v<id>.html or -a<id>.html
    "geizhals.de": re.compile(r"-[va]\d{5,}\.html", re.IGNORECASE),
    "geizhals.at": re.compile(r"-[va]\d{5,}\.html", re.IGNORECASE),
    # Billiger uses /products/<id>-<slug>
    "billiger.de": re.compile(r"/products/\d+", re.IGNORECASE),
    # Guenstiger product pages: /Produkt/<slug>/
    "guenstiger.de": re.compile(r"/Produkt/", re.IGNORECASE),
    # Amazon: /dp/<ASIN>
    "amazon.de": re.compile(r"/(dp|gp/product)/[A-Z0-9]{10}", re.IGNORECASE),
    # Otto product pages: /p/<product>
    "otto.de": re.compile(r"/p/", re.IGNORECASE),
    # Conrad: product paths contain /p/<id>
    "conrad.de": re.compile(r"/p/\d+", re.IGNORECASE),
}


def _is_product_page_url(url: str) -> bool:
    """Return True when the URL looks like a specific-product page
    (not a category/search/filter listing).

    For known aggregator domains we check against a pattern registry.
    For unknown domains we return True (assume product page) because
    we don't want to accidentally skip a valid shop page just because
    we haven't catalogued its URL structure.
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        return True

    # Strong veto: query strings that almost always mean "search" or
    # "filter" -- never a specific product page.
    search_q = parsed.query.lower()
    if any(marker in search_q for marker in ("?q=", "fs=", "xf=", "searchterm=", "text=")):
        return False
    # A single query param starting these names is also a search.
    first_param = search_q.split("&", 1)[0]
    if first_param.startswith(("q=", "fs=", "xf=", "searchterm=", "text=", "search=")):
        return False

    pattern = _PRODUCT_URL_PATTERNS.get(domain)
    if pattern is None:
        # Unknown domain -- default to True so we don't over-filter.
        return True
    return bool(pattern.search(url))


# Substrings in the surrounding DOM text that indicate the price is NOT
# the main product price (crossed-out old price, MSRP, "from X" teaser,
# shipping surcharge, etc.). If any of these markers sit in the same
# element (or its parent) as the price, the generic extractor skips the
# element.
_PRICE_NOISE_MARKERS = (
    "uvp",        # unverbindliche Preisempfehlung
    "statt ",     # "statt 199 EUR"
    "vorher",     # "vorher 199 EUR"
    "regul",      # regulärer Preis / regular price
    "rrp",        # recommended retail price
    "msrp",
    "listenpreis",
    "ersparnis",  # "Ersparnis 20 EUR"
    "sie sparen",
    "you save",
    "versand",    # shipping fee
    "lieferung",  # delivery fee
    "zzgl.",      # zuzüglich (plus shipping/VAT)
    "inkl. mwst", # usually OK, but check context; left in for display cleanliness
    "ab ",        # "ab 199 EUR" -- teaser price, not actual price
    "from ",
    "pro stueck", # per-piece teaser on bulk pages
    "pro stück",
    "pro meter",
    "/stück",
    "/stueck",
    "/meter",
    "/lfm",       # per linear meter
    "/m²",
    "/m2",
)


def _has_noise_marker(text: str) -> bool:
    """Return True if the text contains a marker indicating the price is
    a list price, old price, per-unit teaser, or shipping fee rather
    than the actual product price."""
    if not text:
        return False
    lower = text.lower()
    return any(m in lower for m in _PRICE_NOISE_MARKERS)


# -- B2B-specific signal detectors --------------------------------------------

# Textual markers that the page / the offer is behind a login wall.
# We match conservatively -- we don't want to flag a generic "Login"
# link in the header of a perfectly open shop. The markers below all
# imply that pricing specifically is gated.
_LOGIN_GATE_PATTERNS = re.compile(
    r"(preis\s+(nach|auf)\s+(anfrage|login|anmeldung)|"
    r"preis\s+nur\s+f(u|ue|\u00fc)r\s+(kunden|haendler|h(a|ae|\u00e4)ndler)|"
    r"(net|nett)o[-\s]*preis\s+(nach|auf)\s+login|"
    r"b2b[-\s]*preis(e)?(\s+(nach|auf)\s+login)?|"
    r"(bitte|jetzt)\s+(an|ein)melden(\s+\w+){0,3}\s+preis|"
    r"kundenkonto\s+(erforderlich|notwendig)|"
    r"nur\s+f(u|ue|\u00fc)r\s+gesch(a|ae|\u00e4)ftskunden|"
    r"price\s+on\s+(request|login)|"
    r"log\s*in\s+to\s+see\s+price|"
    r"login\s+required\s+for\s+price)",
    re.IGNORECASE,
)


def _detect_login_gate(text: str) -> bool:
    """Detect if the page content indicates price is behind a login.

    Best-effort heuristic. We scan the visible page text for common
    German / English phrases that specifically tie pricing to login.
    Generic "Anmelden" links in navigation are ignored.
    """
    if not text:
        return False
    return bool(_LOGIN_GATE_PATTERNS.search(text))


# VAT status markers. Word-boundary anchored so partial matches in
# words like "brutto" inside "Bruttoartikel" still work but we avoid
# false positives like "internet" containing "net".
_VAT_NET_PATTERNS = re.compile(
    r"(\bnett?o\b|"
    r"zzgl\.?\s*(mwst|ust|umsatzsteuer|vat)|"
    r"exkl\.?\s*(mwst|ust|vat)|"
    r"plus\s+(mwst|vat)|"
    r"excl(\.|uding)?\s*vat|"
    r"net\s+price|"
    r"preise\s+verstehen\s+sich\s+netto)",
    re.IGNORECASE,
)
_VAT_GROSS_PATTERNS = re.compile(
    r"(\bbrutto\b|"
    r"inkl\.?\s*(mwst|ust|umsatzsteuer|vat|\d{1,2}\s*%)|"
    r"incl(\.|uding)?\s*(vat|tax)|"
    r"gross\s+price|"
    r"preise\s+verstehen\s+sich\s+brutto|"
    r"endpreis\s+inkl)",
    re.IGNORECASE,
)


def _detect_vat_status(text: str) -> str:
    """Return "net", "gross", or "unknown" based on VAT markers in text.

    Prefers "gross" when both are present (common on consumer pages
    that mention "inkl. MwSt" while also footnoting "zzgl. Versand").
    """
    if not text:
        return "unknown"
    has_gross = bool(_VAT_GROSS_PATTERNS.search(text))
    has_net = bool(_VAT_NET_PATTERNS.search(text))
    if has_gross and not has_net:
        return "gross"
    if has_net and not has_gross:
        return "net"
    if has_gross and has_net:
        # Ambiguous -- prefer gross (consumer default in DE)
        return "gross"
    return "unknown"


# Tier-pricing / minimum-order-quantity patterns. Both are detected on
# the full page text, not per-offer, because they usually appear in a
# separate block next to (not inside) the price element.
_TIER_PRICING_PATTERN = re.compile(
    r"(ab\s+\d+\s*stk|"               # "ab 50 Stk."
    r"ab\s+\d+\s*st(u|ue)ck|"
    r"mengenstaffel|staffelpreis|"
    r"bulk\s+discount|quantity\s+(discount|pricing)|"
    r"tier\s+pricing|"
    r"\d+\s*\+\s*st(u|ue)ck|"          # "100+ Stück"
    r"ab\s+\d+\s+einheiten)",
    re.IGNORECASE,
)


def _detect_tier_pricing(text: str) -> bool:
    """Return True if the page mentions tier / bulk pricing."""
    if not text:
        return False
    return bool(_TIER_PRICING_PATTERN.search(text))


_MOQ_PATTERN = re.compile(
    r"(?:"
    r"mindestabnahme(?:menge)?\s*:?\s*(\d+)|"
    r"mindestbestell(?:menge|wert)?\s*:?\s*(\d+)|"
    r"moq\s*:?\s*(\d+)|"
    r"minimum\s+order\s+(?:quantity|qty)\s*:?\s*(\d+)|"
    r"mindestens\s+(\d+)\s+st(?:u|ue|\u00fc)ck"
    r")",
    re.IGNORECASE,
)


def _detect_min_order_quantity(text: str) -> Optional[int]:
    """Extract MOQ from page text if stated. Returns None if not found."""
    if not text:
        return None
    match = _MOQ_PATTERN.search(text)
    if not match:
        return None
    for group in match.groups():
        if group and group.isdigit():
            qty = int(group)
            if 1 <= qty <= 100000:  # sanity
                return qty
    return None


# Availability detection. Returns a normalized status string so the LLM
# can surface stock information consistently across shops. Order matters
# -- more specific patterns first.
_AVAIL_IN_STOCK_PATTERN = re.compile(
    r"\b(auf\s+lager|sofort\s+(lieferbar|verf(u|ue|\u00fc)gbar)|"
    r"am\s+lager|lagernd|"
    r"in\s+stock|available\s+now|ships\s+today|"
    r"verf(u|ue|\u00fc)gbar(?!\s+ab))",
    re.IGNORECASE,
)
_AVAIL_OUT_OF_STOCK_PATTERN = re.compile(
    r"(ausverkauft|nicht\s+(verf(u|ue|\u00fc)gbar|lieferbar|auf\s+lager)|"
    r"derzeit\s+nicht\s+(verf(u|ue|\u00fc)gbar|lieferbar)|"
    r"vergriffen|out\s+of\s+stock|sold\s+out|"
    r"unavailable|no\s+longer\s+available)",
    re.IGNORECASE,
)
_AVAIL_ON_REQUEST_PATTERN = re.compile(
    r"(auf\s+anfrage|lieferzeit\s+auf\s+anfrage|"
    r"on\s+request|contact\s+us\s+for\s+availability|"
    r"preis\s+und\s+verf(u|ue|\u00fc)gbarkeit\s+auf\s+anfrage)",
    re.IGNORECASE,
)
_AVAIL_BACKORDER_PATTERN = re.compile(
    r"(lieferzeit\s*:?\s*(\d+)\s*(?:tag|woche|werktag)|"
    r"verf(u|ue|\u00fc)gbar\s+(in|ab)\s+(\d+)\s*(tag|woche|werktag)|"
    r"lieferbar\s+in\s+(\d+)\s*(tag|woche|werktag)|"
    r"in\s+(\d+)\s+(day|week|business day)s?|"
    r"ships\s+in\s+(\d+)\s+(day|week))",
    re.IGNORECASE,
)


def _detect_availability(text: str) -> str:
    """Return a normalized availability status for the text.

    Returns one of:
      - "in_stock"     -- "auf lager", "verfuegbar", "in stock"
      - "out_of_stock" -- "ausverkauft", "nicht verfuegbar"
      - "on_request"   -- "auf anfrage", "contact us"
      - "backorder"    -- "lieferzeit: X Tage", ships in N days
      - ""             -- no signal found
    """
    if not text:
        return ""
    # Check out-of-stock / on-request FIRST -- they are often
    # accompanied by the word "verfuegbar" in a negated form.
    if _AVAIL_OUT_OF_STOCK_PATTERN.search(text):
        return "out_of_stock"
    if _AVAIL_ON_REQUEST_PATTERN.search(text):
        return "on_request"
    if _AVAIL_IN_STOCK_PATTERN.search(text):
        return "in_stock"
    if _AVAIL_BACKORDER_PATTERN.search(text):
        return "backorder"
    return ""


def normalize_json_ld_availability(raw: str) -> str:
    """Normalize a schema.org Availability value to our status vocab."""
    if not raw:
        return ""
    val = raw.strip().rstrip("/").rsplit("/", 1)[-1].lower()
    mapping = {
        "instock": "in_stock",
        "in_stock": "in_stock",
        "available": "in_stock",
        "onlineonly": "in_stock",
        "limitedavailability": "in_stock",
        "outofstock": "out_of_stock",
        "out_of_stock": "out_of_stock",
        "soldout": "out_of_stock",
        "discontinued": "out_of_stock",
        "preorder": "backorder",
        "backorder": "backorder",
        "presale": "backorder",
    }
    return mapping.get(val, "")


# -- Tier pricing structured extraction ----------------------------------------

# Matches German B2B tier price patterns:
#   "ab 50 Stk. 9,80 EUR"
#   "ab 100 Stück: 8.90 EUR"
#   "100+ Stk = 8,90 EUR"
# The price capture tolerates both comma and dot decimals. We keep the
# match intentionally tight (requires an explicit "EUR/Euro/€" marker
# after the price) to avoid harvesting random 2-decimal numbers.
_TIER_PRICE_CAPTURE = re.compile(
    # unit is any of: "Stk", "Stk.", "Stück", "Stueck", "Stuck", "pcs", "+"
    r"(?:ab|from)\s+(\d{1,5})\s*"
    r"(?:stk\.?|st(?:u|ue|\u00fc)ck?\.?|pcs?\.?|pieces?|\+)"
    r"[\s:=.\-]{0,10}"
    r"(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})"
    r"\s*(?:EUR|Euro|\u20ac)",
    re.IGNORECASE,
)


def _extract_tier_pricing(text: str) -> Optional[list[dict[str, Any]]]:
    """Extract a structured tier-pricing list from page text.

    Returns a list of {"min_qty": int, "price": float} entries sorted
    by min_qty ascending, or None if fewer than 2 tiers were found.
    A single "ab X Stk" occurrence is not a tier -- that's just an
    MOQ hint, already captured by min_order_quantity.
    """
    if not text:
        return None

    seen_qty: set[int] = set()
    tiers: list[dict[str, Any]] = []
    for match in _TIER_PRICE_CAPTURE.finditer(text):
        try:
            qty = int(match.group(1))
        except ValueError:
            continue
        if qty in seen_qty or qty < 1 or qty > 100000:
            continue
        raw_price = match.group(2)
        price = parse_price(raw_price)
        if price is None or not _is_reasonable_price(price):
            continue
        seen_qty.add(qty)
        tiers.append({"min_qty": qty, "price": round(price, 2)})

    if len(tiers) < 2:
        return None
    tiers.sort(key=lambda t: t["min_qty"])
    return tiers[:10]  # cap verbosity


# -- Site-specific extractors --------------------------------------------------

SITE_SELECTORS: dict[str, dict[str, Any]] = {
    # Idealo and Geizhals aggregators are handled by dedicated async
    # functions (_extract_idealo_multi / _extract_geizhals_multi). Their
    # current DOM does not map cleanly to a single CSS selector per
    # field -- idealo's shop name lives in an img `alt` attribute with
    # the format "shopname - Shop aus <city>", and marketplace offers
    # carry the real seller in a separate ...MarketPlaceMerchantName
    # element. Geizhals uses a parallel-locator model (one ".offer__
    # merchant" cell per offer, one ".offer__price" cell per offer, no
    # enclosing row element). Dedicated extractors handle both cleanly.
    "idealo.de": {"custom_extractor": "idealo"},
    "geizhals.de": {"custom_extractor": "geizhals"},
    "geizhals.at": {"custom_extractor": "geizhals"},
    "amazon.de": {
        "single_price": "#priceblock_ourprice, .a-price .a-offscreen, #corePrice_feature_div .a-offscreen, .a-price-whole",
        "merchant_static": "Amazon.de",
    },
    "otto.de": {
        "single_price": "[data-qa='productPrice'], .product__price, .prd-price__amount",
        "merchant_static": "OTTO",
    },
    "mediamarkt.de": {
        "single_price": "[data-test='branded-price-whole-value'], .price, .product-price",
        "merchant_static": "MediaMarkt",
    },
    "saturn.de": {
        "single_price": "[data-test='branded-price-whole-value'], .price, .product-price",
        "merchant_static": "Saturn",
    },
    "notebooksbilliger.de": {
        "single_price": ".product-price__regular, .price",
        "merchant_static": "notebooksbilliger.de",
    },
    "alternate.de": {
        "single_price": ".price .price, .productPriceContainer",
        "merchant_static": "Alternate",
    },
    "conrad.de": {
        "single_price": ".price__value, .price",
        "merchant_static": "Conrad",
    },
    "reichelt.de": {
        "single_price": ".productPrice, .price",
        "merchant_static": "Reichelt",
    },
    "voelkner.de": {
        "single_price": ".price__value, .price",
        "merchant_static": "Voelkner",
    },
    "elektro4000.de": {
        "single_price": ".price, .product-price",
        "merchant_static": "Elektro4000",
    },
    # =========================================================================
    # Phase J: Fashion + consumer-electronics deep-link selectors.
    #
    # All fashion retailers use heavy client-side rendering with strong
    # bot detection on detail pages. Each entry below is a DOM-stable
    # CSS chain harvested from a manual inspection (Apr 2026). The
    # `single_price` selector list is tried in order; first non-empty
    # text becomes the price candidate. JSON-LD path remains the
    # dominant fallback (these sites all publish Product+offers
    # structured data even when the visual DOM is React-rendered).
    # =========================================================================
    "zalando.de": {
        # Variant-specific size price (selected size attribute) wins;
        # fallback to the generic "from" price.
        "single_price": (
            "[data-testid='product-price'], "
            "[data-testid='price'], "
            ".price--current, "
            "._0xLoFW, ._7Cm1F9, "
            "span[class*='price']"
        ),
        "merchant_static": "Zalando",
    },
    "aboutyou.de": {
        "single_price": (
            "[data-testid='priceLine'] [data-testid='priceValue'], "
            "[data-testid='priceValue'], "
            ".PriceLine, "
            "[class*='Price__current']"
        ),
        "merchant_static": "AboutYou",
    },
    "snipes.com": {
        "single_price": (
            "[data-test='product-detail-price'], "
            ".product-tile__price, "
            ".price-sales, "
            "[class*='ProductPrice']"
        ),
        "merchant_static": "Snipes",
    },
    "nike.com": {
        # Nike DTC -- prefers JSON-LD but a CSS path is provided so
        # the extractor doesn't return early on the visible-DOM pass.
        "single_price": (
            "[data-test='product-price'], "
            "[data-testid='product-price'], "
            ".product-price, "
            ".css-b9fpep"
        ),
        "merchant_static": "Nike",
    },
    "adidas.de": {
        "single_price": (
            "[data-auto-id='product-price'], "
            ".gl-price-item, "
            ".gl-price__item--current"
        ),
        "merchant_static": "Adidas",
    },
    "footlocker.de": {
        "single_price": (
            ".ProductPrice, "
            "[data-test-id='product-price-current'], "
            "span[class*='Price']"
        ),
        "merchant_static": "Foot Locker",
    },
    "asos.com": {
        "single_price": (
            "[data-id='current-price'], "
            "[data-testid='current-price'], "
            ".product-price, "
            ".current-price"
        ),
        "merchant_static": "ASOS",
    },
    "hm.com": {
        "single_price": (
            ".product-item-price, "
            "[data-priceelement] span, "
            ".price-value"
        ),
        "merchant_static": "H&M",
    },
}


def _get_domain(url: str) -> str:
    """Extract domain from URL, stripping www. prefix."""
    try:
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _clean_idealo_shop_name(raw: str) -> str:
    """Turn 'voelkner.de - Shop aus Wernberg-Koeblitz' into 'voelkner.de'.

    The idealo logo `alt` attribute follows the format
    "<shop> - Shop aus <city>". We keep only the shop part. Fallback
    to the raw value if the pattern does not match.
    """
    if not raw:
        return ""
    m = re.match(r"^\s*(.+?)\s*-\s*Shop\s+aus\s+.*$", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return raw.strip()


async def _extract_idealo_multi(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract the offers listed on an idealo.de OffersOfProduct page.

    Modern idealo DOM (Nov 2025+):
      - Offer rows: `.productOffers-list > .productOffers-listItem`
        (also equals `li.productOffers-listItem`).
      - Shop name: img `alt` on `.productOffers-listItemOfferShopV2LogoImage`,
        formatted "shop - Shop aus <city>".
      - Marketplace real seller (if the listing is a marketplace entry
        like "Amazon Marketplace"): `.productOffers-listItemOfferShopV2MarketPlaceMerchantName`.
      - Price: `.productOffers-listItemOfferPrice`.
      - Shipping/delivery status: `.productOffers-listItemOfferShippingDetailsRightItem`
        or `.productOffers-listItemOfferDeliveryStatus` (text fragments).
      - Out-of-date offer rows may lack a price or show "Aktuell nicht
        verfuegbar"; those are skipped (parse_price returns None).

    Returns up to 20 offers.
    """
    offers: list[ExtractedOffer] = []
    try:
        rows = page.locator(".productOffers-list > .productOffers-listItem")
        cnt = await rows.count()
    except Exception as e:
        logger.debug("Idealo: cannot enumerate offer rows: %s", e)
        return []

    for i in range(min(25, cnt)):
        row = rows.nth(i)
        # Shop name: prefer marketplace seller name when present,
        # else the logo img alt stripped of trailing " - Shop aus <city>".
        shop_name = ""
        try:
            mp = row.locator(
                ".productOffers-listItemOfferShopV2MarketPlaceMerchantName"
            ).first
            if await mp.count():
                shop_name = (await mp.inner_text(timeout=1500)).strip()
        except Exception:
            pass
        if not shop_name:
            try:
                logo = row.locator(
                    ".productOffers-listItemOfferShopV2LogoImage"
                ).first
                if await logo.count():
                    alt = await logo.get_attribute("alt") or ""
                    shop_name = _clean_idealo_shop_name(alt)
            except Exception:
                pass
        if not shop_name:
            # Last resort: the shop link's title attribute
            try:
                link = row.locator(
                    ".productOffers-listItemOfferShopV2LogoLink"
                ).first
                if await link.count():
                    title_attr = await link.get_attribute("title") or ""
                    shop_name = _clean_idealo_shop_name(title_attr)
            except Exception:
                pass

        # Price
        price_text = ""
        try:
            price_el = row.locator(".productOffers-listItemOfferPrice").first
            if await price_el.count():
                price_text = (await price_el.inner_text(timeout=1500)).strip()
        except Exception:
            pass
        price = parse_price(price_text)
        if price is None or not _is_reasonable_price(price):
            continue

        # Shipping (best-effort)
        shipping_text = ""
        for sel in (
            ".productOffers-listItemOfferShippingDetailsRightItem",
            ".productOffers-listItemOfferDeliveryStatus",
        ):
            try:
                el = row.locator(sel).first
                if await el.count():
                    shipping_text = (await el.inner_text(timeout=1500)).strip()
                    if shipping_text:
                        break
            except Exception:
                continue

        # Availability (derived from the row text)
        availability = ""
        try:
            row_text = await row.inner_text(timeout=1500)
            availability = _detect_availability(row_text)
        except Exception:
            pass

        offers.append(ExtractedOffer(
            merchant=shop_name or "idealo.de",
            price=price,
            shipping_cost=parse_shipping(shipping_text),
            url=url,
            availability=availability,
            price_source="css_site",
        ))

    return offers


async def _extract_geizhals_multi(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract the offers listed on a geizhals.de product page.

    Modern geizhals DOM (Nov 2025+):
      - Each offer cell sits at top level (table-like flex grid). We
        iterate `.offer__merchant` and `.offer__price` in parallel --
        they are paired by index.
      - `.offer__merchant` inner text has the shop name on the first
        line (e.g. "voelkner.de", "Jacob Elektronik direkt",
        "bueromarkt-ag.de", "Amazon.de").
      - The first row is the header ("Anbieter"/"Preis exkl. Versand*")
        -- detected and skipped by price parse failure.
      - `.offer__delivery` carries availability / lead time text.
    """
    offers: list[ExtractedOffer] = []
    try:
        merchants = page.locator(".offer__merchant")
        prices = page.locator(".offer__price")
        deliveries = page.locator(".offer__delivery")
        m_cnt = await merchants.count()
        p_cnt = await prices.count()
    except Exception as e:
        logger.debug("Geizhals: cannot enumerate offer elements: %s", e)
        return []

    pair_count = min(m_cnt, p_cnt, 25)
    for i in range(pair_count):
        price_text = ""
        try:
            price_text = (await prices.nth(i).inner_text(timeout=1500)).strip().splitlines()[0].strip()
        except Exception:
            pass
        price = parse_price(price_text)
        if price is None or not _is_reasonable_price(price):
            # Header row, "Preis exkl. Versand*" label, or empty cell.
            continue

        shop = ""
        try:
            merchant_text = (await merchants.nth(i).inner_text(timeout=1500)).strip()
            # First non-empty line is the shop name.
            for line in merchant_text.splitlines():
                line = line.strip()
                if line and line.lower() not in ("anbieter", ""):
                    shop = line
                    break
        except Exception:
            pass

        # Delivery / availability
        delivery_text = ""
        try:
            if i < await deliveries.count():
                delivery_text = (await deliveries.nth(i).inner_text(timeout=1500)).strip().splitlines()[0].strip()
        except Exception:
            pass
        availability = _detect_availability(delivery_text)
        shipping = parse_shipping(delivery_text)

        offers.append(ExtractedOffer(
            merchant=shop or "geizhals.de",
            price=price,
            shipping_cost=shipping,
            url=url,
            availability=availability,
            price_source="css_site",
        ))

    return offers


_CUSTOM_EXTRACTORS = {
    "idealo": _extract_idealo_multi,
    "geizhals": _extract_geizhals_multi,
}


# =============================================================================
# Phase N: aggregator search-result (SERP) tile extractors.
#
# When SearXNG returns the aggregator's search URL itself (idealo's
# MainSearchProductCategory.html?q=... or geizhals' /?fs=...) instead of
# a deep product page, the SERP shows 8-12 tiles -- each tile is a real
# product card with a "ab EUR X" minimum price and a link to the full
# product page. Pre-Phase-N we returned [] for these URLs (treating them
# as no-info SERPs); now we parse the tiles and emit one offer per tile,
# tagged with merchant=domain so the title-gate / part-number-gate can
# still veto wrong-SKU rows downstream.
#
# Each tile becomes an ExtractedOffer with:
#   merchant: the aggregator domain ("idealo.de" / "geizhals.de") --
#             accurate because the price is the aggregator's lowest
#             observed across its merchants
#   price:    the "ab EUR X" minimum
#   url:      the deep product-page URL from the tile (so the operator
#             can click through for the full offer list)
#   price_source: "css_site"
# =============================================================================


async def _extract_idealo_search_tiles(
    page: "Page", url: str,
) -> list[ExtractedOffer]:
    """Parse idealo MainSearchProductCategory.html tile listings.

    Idealo's SERP DOM (Nov 2025): each tile is `.offerList-item` with
    nested `.offerList-item-priceMin` (the "ab" minimum), a product
    title in `.offerList-item-description-title` and the deep URL on
    the wrapping anchor `.offerList-item-link`. Multiple selector
    fallbacks because idealo A/B-tests the result-page DOM.
    """
    offers: list[ExtractedOffer] = []

    # Try several known tile-container patterns
    container_selectors = (
        ".offerList-item",
        ".search-result-tile",
        ".sr-resultList__item",
        "[data-testid='resultItem']",
    )
    rows = None
    for sel in container_selectors:
        try:
            r = page.locator(sel)
            cnt = await r.count()
            if cnt > 0:
                rows = r
                break
        except Exception:
            continue
    if rows is None:
        return []

    try:
        cnt = await rows.count()
    except Exception:
        return []

    for i in range(min(15, cnt)):
        row = rows.nth(i)

        # Price (ab EUR X)
        price_text = ""
        for sel in (
            ".offerList-item-priceMin",
            ".offerList-item-price",
            "[data-testid='price']",
            ".price",
        ):
            try:
                el = row.locator(sel).first
                if await el.count():
                    price_text = (await el.inner_text(timeout=1200)).strip()
                    if price_text:
                        break
            except Exception:
                continue
        price = parse_price(price_text)
        if price is None or not _is_reasonable_price(price):
            continue

        # Deep product URL (so the operator can drill in)
        product_url = url
        for sel in (
            ".offerList-item-link",
            "a.offerList-item-description-link",
            "a",
        ):
            try:
                a = row.locator(sel).first
                if await a.count():
                    href = await a.get_attribute("href")
                    if href:
                        product_url = (
                            href if href.startswith("http")
                            else f"https://www.idealo.de{href}"
                        )
                        break
            except Exception:
                continue

        offers.append(ExtractedOffer(
            merchant="idealo.de",
            price=price,
            shipping_cost=None,
            url=product_url,
            availability="",
            price_source="css_site",
        ))

    return offers


async def _extract_geizhals_search_tiles(
    page: "Page", url: str,
) -> list[ExtractedOffer]:
    """Parse geizhals.de/?fs= search-result rows.

    Geizhals' SERP shows a denser table of product rows. Each row has a
    product title link, an offer count ("8 Angebote"), and the lowest
    observed price ("ab EUR 89,99"). We grab title-link + lowest price.
    """
    offers: list[ExtractedOffer] = []

    # Multiple candidate row selectors for forward-compat across DOM
    # tweaks. Listing-cells are flex containers in the modern DOM.
    row_selectors = (
        ".cell.cell--listing",
        ".listview__row",
        "tr.cell",
        ".gh_table_row",
    )
    rows = None
    for sel in row_selectors:
        try:
            r = page.locator(sel)
            cnt = await r.count()
            if cnt > 0:
                rows = r
                break
        except Exception:
            continue
    if rows is None:
        return []

    try:
        cnt = await rows.count()
    except Exception:
        return []

    for i in range(min(15, cnt)):
        row = rows.nth(i)

        # Lowest price ("ab EUR 89,99")
        price_text = ""
        for sel in (
            ".gh_price",
            ".cell__price",
            ".listview__price",
            ".price",
        ):
            try:
                el = row.locator(sel).first
                if await el.count():
                    price_text = (await el.inner_text(timeout=1200)).strip()
                    if price_text:
                        break
            except Exception:
                continue
        price = parse_price(price_text)
        if price is None or not _is_reasonable_price(price):
            continue

        # Product page link
        product_url = url
        for sel in (
            ".cell__title a",
            ".listview__title a",
            ".gh_title a",
            "a",
        ):
            try:
                a = row.locator(sel).first
                if await a.count():
                    href = await a.get_attribute("href")
                    if href:
                        product_url = (
                            href if href.startswith("http")
                            else f"https://geizhals.de{href}"
                        )
                        break
            except Exception:
                continue

        offers.append(ExtractedOffer(
            merchant="geizhals.de",
            price=price,
            shipping_cost=None,
            url=product_url,
            availability="",
            price_source="css_site",
        ))

    return offers


async def _extract_site_specific(page: Page, url: str) -> list[ExtractedOffer]:
    """Try site-specific extraction for known domains.

    Dispatch order:
      1. Custom async extractor if `custom_extractor` is set in config
         (Idealo, Geizhals -- shops that changed DOM to something CSS
         selectors alone can't express).
      2. Multi-offer CSS container extraction (legacy path, now unused
         for Idealo/Geizhals but kept for potential future per-domain
         aggregators).
      3. Single-price selector extraction (Amazon, Otto, etc.).

    IMPORTANT: multi-offer extraction only runs when the URL pattern
    confirms we are on a specific-product page (see _is_product_page_url).
    Listing pages would pollute results with unrelated SKUs.
    """
    domain = _get_domain(url)
    config = None
    for site_domain, site_config in SITE_SELECTORS.items():
        if site_domain in domain:
            config = site_config
            break

    if config is None:
        return []

    offers: list[ExtractedOffer] = []

    # 1. Custom async extractors
    custom_name = config.get("custom_extractor")
    if custom_name:
        if not _is_product_page_url(url):
            logger.info(
                "Site-specific: skipping listing/search URL for %s (not a product page)",
                domain,
            )
            return []
        extractor = _CUSTOM_EXTRACTORS.get(custom_name)
        if extractor is not None:
            try:
                offers = await extractor(page, url)
            except Exception as e:
                logger.debug("Custom %s extractor failed for %s: %s", custom_name, domain, e)
                offers = []
            if offers:
                logger.info(
                    "Site-specific (%s): extracted %d offers from %s",
                    custom_name, len(offers), domain,
                )
            return offers

    # 2. Legacy multi-offer CSS extraction
    container_sel = config.get("offer_container")
    if container_sel and not _is_product_page_url(url):
        logger.info(
            "Site-specific: skipping listing/search URL for %s (not a product page)",
            domain,
        )
        return []

    if container_sel:
        try:
            containers = await page.query_selector_all(container_sel)
            for container in containers[:20]:
                merchant_sel = config.get("merchant", "")
                price_sel = config.get("price", "")
                shipping_sel = config.get("shipping", "")

                merchant_text = ""
                if merchant_sel:
                    el = await container.query_selector(merchant_sel)
                    if el:
                        merchant_text = (await el.inner_text()).strip()

                price_text = ""
                if price_sel:
                    el = await container.query_selector(price_sel)
                    if el:
                        price_text = (await el.inner_text()).strip()

                shipping_text = ""
                if shipping_sel:
                    el = await container.query_selector(shipping_sel)
                    if el:
                        shipping_text = (await el.inner_text()).strip()

                price = parse_price(price_text)
                if price and _is_reasonable_price(price):
                    offers.append(ExtractedOffer(
                        merchant=merchant_text or domain,
                        price=price,
                        shipping_cost=parse_shipping(shipping_text),
                        url=url,
                        price_source="css_site",
                    ))
        except Exception as e:
            logger.debug("Site-specific extraction failed for %s: %s", domain, e)

    # 3. Single-price pages (Amazon, Otto, etc.)
    single_sel = config.get("single_price")
    if single_sel and not offers:
        try:
            el = await page.query_selector(single_sel)
            if el:
                price_text = (await el.inner_text()).strip()
                price = parse_price(price_text)
                if price and _is_reasonable_price(price):
                    merchant = config.get("merchant_static", domain)
                    offers.append(ExtractedOffer(
                        merchant=merchant,
                        price=price,
                        url=url,
                        price_source="css_site",
                    ))
        except Exception as e:
            logger.debug("Single-price extraction failed for %s: %s", domain, e)

    if offers:
        logger.info("Site-specific: extracted %d offers from %s", len(offers), domain)
    return offers


# -- JSON-LD / Schema.org extraction -------------------------------------------

async def _extract_json_ld(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract prices from JSON-LD Product markup."""
    offers: list[ExtractedOffer] = []

    try:
        scripts = await page.query_selector_all('script[type="application/ld+json"]')
        for script in scripts:
            text = await script.inner_text()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            # Handle arrays of LD+JSON objects
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                if item_type not in ("Product", "IndividualProduct"):
                    continue

                product_offers = item.get("offers", {})
                if isinstance(product_offers, dict):
                    product_offers = [product_offers]
                elif not isinstance(product_offers, list):
                    continue

                for offer_data in product_offers:
                    if not isinstance(offer_data, dict):
                        continue

                    # AggregateOffer
                    if offer_data.get("@type") == "AggregateOffer":
                        low = offer_data.get("lowPrice")
                        if low:
                            price = float(low) if isinstance(low, (int, float)) else parse_price(str(low))
                            if price and _is_reasonable_price(price):
                                offers.append(ExtractedOffer(
                                    merchant=_get_domain(url),
                                    price=price,
                                    currency=offer_data.get("priceCurrency", "EUR"),
                                    url=url,
                                    availability=normalize_json_ld_availability(
                                        offer_data.get("availability", "")
                                    ),
                                    price_source="json_ld",
                                ))
                        continue

                    # Individual Offer
                    price_val = offer_data.get("price")
                    if price_val is None:
                        continue
                    price = float(price_val) if isinstance(price_val, (int, float)) else parse_price(str(price_val))
                    if price and _is_reasonable_price(price):
                        seller = offer_data.get("seller", {})
                        merchant = ""
                        if isinstance(seller, dict):
                            merchant = seller.get("name", "")
                        offers.append(ExtractedOffer(
                            merchant=merchant or _get_domain(url),
                            price=price,
                            currency=offer_data.get("priceCurrency", "EUR"),
                            url=offer_data.get("url", url),
                            availability=normalize_json_ld_availability(
                                offer_data.get("availability", "")
                            ),
                            price_source="json_ld",
                        ))
    except Exception as e:
        logger.debug("JSON-LD extraction failed for %s: %s", url, e)

    if offers:
        logger.info("JSON-LD: extracted %d offers from %s", len(offers), _get_domain(url))
    return offers


# -- OpenGraph / Microdata extraction ------------------------------------------

async def _extract_microdata(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract prices from OpenGraph meta tags and microdata."""
    offers: list[ExtractedOffer] = []

    try:
        # OpenGraph product price
        og_price = await page.query_selector('meta[property="product:price:amount"]')
        if og_price:
            price_val = await og_price.get_attribute("content")
            price = parse_price(price_val or "")
            if price and _is_reasonable_price(price):
                offers.append(ExtractedOffer(
                    merchant=_get_domain(url),
                    price=price,
                    url=url,
                    price_source="microdata",
                ))

        # Microdata itemprop="price"
        if not offers:
            price_el = await page.query_selector('[itemprop="price"]')
            if price_el:
                price_val = await price_el.get_attribute("content") or await price_el.inner_text()
                price = parse_price(price_val)
                if price and _is_reasonable_price(price):
                    offers.append(ExtractedOffer(
                        merchant=_get_domain(url),
                        price=price,
                        url=url,
                        price_source="microdata",
                    ))
    except Exception as e:
        logger.debug("Microdata extraction failed for %s: %s", url, e)

    if offers:
        logger.info("Microdata: extracted %d offers from %s", len(offers), _get_domain(url))
    return offers


# -- Generic CSS heuristic extraction ------------------------------------------

async def _extract_generic_css(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract the primary price using generic CSS selectors.

    Returns AT MOST ONE offer -- the first valid price found in DOM order,
    which is almost always the main product price at the top of the page.
    Generic extraction is only used as a fallback for sites without a
    site-specific rule or JSON-LD markup; those sites are almost always
    single-product shop pages. Collecting multiple prices from them tends
    to pick up unrelated "from X EUR" fragments, related-product blocks,
    or shipping labels -- noisier than useful.
    """
    selectors = [
        # Attribute-based selectors first -- most likely the primary price
        "[data-price]",
        '[itemprop="price"]',
        # Then class-based selectors, ordered from specific to broad
        ".product-price",
        ".current-price",
        ".price",
        "[class*='price']",
        "[class*='Price']",
    ]

    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements[:10]:
                # Prefer attribute value over inner text
                data_price = await el.get_attribute("data-price")
                content_attr = await el.get_attribute("content")
                inner_text = ""
                if data_price:
                    price = parse_price(data_price)
                    # Cents-detection: a bare integer in data-price is a
                    # common e-commerce convention (billiger.de and others
                    # store 18979 to mean 189.79 EUR). Treat pure-integer
                    # values >= 1000 as cents and divide by 100.
                    if (
                        price is not None
                        and data_price.strip().isdigit()
                        and price >= 1000
                    ):
                        price = price / 100
                elif content_attr:
                    price = parse_price(content_attr)
                else:
                    inner_text = (await el.inner_text()).strip()
                    price = parse_price(inner_text)

                if not (price and _is_reasonable_price(price)):
                    continue

                # Noise filtering -- skip elements that look like list
                # prices, teasers, shipping fees, etc.
                if _has_noise_marker(inner_text):
                    logger.debug(
                        "Generic CSS: skipping noisy price %.2f ('%s') from %s",
                        price, inner_text[:50], _get_domain(url),
                    )
                    continue

                # Also check class attribute for old-price / struck-
                # through styling which indicates pre-discount listings.
                class_attr = (await el.get_attribute("class") or "").lower()
                if any(
                    marker in class_attr
                    for marker in ("strike", "old", "original", "was", "rrp", "uvp")
                ):
                    logger.debug(
                        "Generic CSS: skipping crossed-out price %.2f from %s",
                        price, _get_domain(url),
                    )
                    continue

                logger.info(
                    "Generic CSS: extracted primary price %.2f from %s (selector=%s)",
                    price, _get_domain(url), selector,
                )
                return [ExtractedOffer(
                    merchant=_get_domain(url),
                    price=price,
                    url=url,
                    price_source="css_generic",
                )]
        except Exception:
            continue

    return []


# -- Regex fallback on visible text --------------------------------------------

# Price patterns that require a currency marker to avoid false positives.
# The currency can appear BEFORE or AFTER the number (German sites use
# both orderings: "195,00 EUR" and "EUR 195,00"). The German patterns
# must be tried first -- "1.726,95 EUR" must never be misparsed as
# "1.72 EUR" by a too-greedy English regex.
_PRICE_RE_DE_POST = re.compile(
    r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*(?:EUR|Euro|\u20ac)",
    re.IGNORECASE,
)
_PRICE_RE_DE_PRE = re.compile(
    r"(?:EUR|Euro|\u20ac)\s*(\d{1,3}(?:\.\d{3})*,\d{2})(?!\d)",
    re.IGNORECASE,
)
# English decimal form uses a negative lookahead so we never match the
# "1.72" prefix of a German "1.726,95" number.
_PRICE_RE_EN_POST = re.compile(
    r"(\d{1,3}(?:,\d{3})*\.\d{2})\s*(?:EUR|Euro|\u20ac)",
    re.IGNORECASE,
)
_PRICE_RE_EN_PRE = re.compile(
    r"(?:EUR|Euro|\u20ac)\s*(\d{1,6}\.\d{2})(?!\d)",
    re.IGNORECASE,
)


async def _extract_regex(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract the primary price from visible text using regex.

    Returns AT MOST ONE offer -- the first EUR price encountered in DOM
    text order, which is almost always the main product price above the
    fold. Same rationale as in _extract_generic_css: last-resort fallback
    on single-product pages; multi-price extraction here just introduces
    noise (related products, shipping fees, list prices, etc.).
    """
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if not text:
            return []

        # Order matters: German regexes (both orderings) must run before
        # English ones, so "1.726,95 EUR" is NEVER misread as "1.72 EUR".
        # We collect ALL reasonable matches across all patterns in text
        # order and pick the first one above the sanity floor. That
        # avoids dead-ends when the first regex match happens to be
        # a shipping line like "Versand: EUR 0,00".
        candidates: list[tuple[re.Pattern[str], str]] = [
            (_PRICE_RE_DE_POST, "de-post"),
            (_PRICE_RE_DE_PRE, "de-pre"),
            (_PRICE_RE_EN_POST, "en-post"),
            (_PRICE_RE_EN_PRE, "en-pre"),
        ]
        for pat, label in candidates:
            for m in pat.finditer(text):
                raw = m.group(1)
                # Locale-aware float parsing based on which pattern matched.
                if label.startswith("de"):
                    price_str = raw.replace(".", "").replace(",", ".")
                else:
                    price_str = raw.replace(",", "")
                try:
                    price = float(price_str)
                except ValueError:
                    continue
                if not (price and _is_reasonable_price(price)):
                    continue
                # Skip obvious non-product prices that sit near the top of
                # many pages (shipping, MwSt surcharges, fixed fees).
                # The 40-char window before the number is enough to catch
                # labels like "Versand", "Porto", "MwSt", "shipping".
                ctx_start = max(0, m.start() - 40)
                ctx = text[ctx_start:m.start()].lower()
                if any(kw in ctx for kw in (
                    "versand", "porto", "shipping", "lieferung",
                    "mwst.", "mehrwertsteuer", "gebuehr", "gebühr",
                    "grundpreis",
                )):
                    continue
                logger.info(
                    "Regex: extracted primary price %.2f (%s) from %s",
                    price, label, _get_domain(url),
                )
                return [ExtractedOffer(
                    merchant=_get_domain(url),
                    price=price,
                    url=url,
                    price_source="regex",
                )]

    except Exception as e:
        logger.debug("Regex extraction failed for %s: %s", url, e)

    return []


# -- Page-level signal extraction --------------------------------------------

# EAN / GTIN-13 is a globally unique product identifier. When we can
# extract it from a page we know we have the exact SKU the merchant is
# selling -- the strongest possible match signal. We look in (1) JSON-LD
# offers, (2) microdata / OpenGraph meta tags, (3) visible text.
_EAN_JSON_LD_SCRIPT = """
() => {
  const ranks = {gtin13: 3, gtin: 3, gtin14: 2, gtin12: 2, gtin8: 1, mpn: 0};
  const scripts = document.querySelectorAll('script[type="application/ld+json"]');
  let best = null, bestRank = -1;
  for (const s of scripts) {
    try {
      const data = JSON.parse(s.textContent);
      const items = Array.isArray(data) ? data : [data];
      const stack = [...items];
      while (stack.length) {
        const it = stack.pop();
        if (!it || typeof it !== 'object') continue;
        for (const k of Object.keys(ranks)) {
          if (it[k] && typeof it[k] === 'string' && it[k].match(/^\\d{8,14}$/)) {
            if (ranks[k] > bestRank) { best = it[k]; bestRank = ranks[k]; }
          }
        }
        if (it.offers) stack.push(...(Array.isArray(it.offers) ? it.offers : [it.offers]));
      }
    } catch (e) {}
  }
  return best;
}
"""

_EAN_META_SELECTORS = [
    'meta[itemprop="gtin13"]',
    'meta[itemprop="gtin"]',
    'meta[itemprop="gtin14"]',
    'meta[itemprop="gtin12"]',
    'meta[itemprop="gtin8"]',
    'meta[property="product:ean"]',
    'meta[property="product:gtin"]',
]

_EAN_TEXT_PATTERNS = [
    re.compile(r"\bEAN[:\s\-]*(\d{13})\b", re.IGNORECASE),
    re.compile(r"\bGTIN[-]?13?[:\s\-]*(\d{13})\b", re.IGNORECASE),
    re.compile(r"\bGTIN[:\s\-]*(\d{13})\b", re.IGNORECASE),
]


def _is_valid_ean13(s: str) -> bool:
    """Basic checksum validation for EAN-13 / GTIN-13."""
    if not s or len(s) != 13 or not s.isdigit():
        return False
    digits = [int(c) for c in s]
    check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12])) % 10) % 10
    return check == digits[12]


async def extract_ean(page: Page) -> str:
    """Extract EAN/GTIN from a product page. Returns "" if not found.

    Tries three strategies, returning the first match:
      1. JSON-LD offers (most reliable, structured)
      2. OpenGraph / microdata meta tags
      3. Visible text ("EAN: 1234567890123")

    EAN-13 checksums are validated. 8/12/14-digit variants are also
    accepted (without checksum validation).
    """
    # Strategy 1: JSON-LD
    try:
        value = await page.evaluate(_EAN_JSON_LD_SCRIPT)
        if value and isinstance(value, str) and value.isdigit():
            if len(value) == 13 and _is_valid_ean13(value):
                return value
            if len(value) in (8, 12, 14):
                return value
    except Exception:
        pass

    # Strategy 2: meta tags
    for sel in _EAN_META_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                content = await el.get_attribute("content")
                if content and content.isdigit():
                    if len(content) == 13 and _is_valid_ean13(content):
                        return content
                    if len(content) in (8, 12, 14):
                        return content
        except Exception:
            continue

    # Strategy 3: visible text regex (restricted to first 10000 chars for speed)
    try:
        body_text = await page.evaluate(
            "() => document.body ? document.body.innerText.substring(0, 10000) : ''"
        )
        if body_text:
            for pat in _EAN_TEXT_PATTERNS:
                m = pat.search(body_text)
                if m:
                    candidate = m.group(1)
                    if len(candidate) == 13 and _is_valid_ean13(candidate):
                        return candidate
    except Exception:
        pass

    return ""


async def extract_page_signals(page: Page) -> dict[str, Any]:
    """Extract page-level B2B signals independent of specific offers.

    Signals returned:
      - login_gate: bool
      - vat_status: "net" | "gross" | "unknown"
      - has_tier_pricing: bool
      - min_order_quantity: int | None
      - availability: "" | "in_stock" | "out_of_stock" | "on_request" | "backorder"
      - tier_pricing: list[{min_qty, price}] | None (structured extraction)

    Runs once per fetched page and is merged onto each extracted offer
    by the caller. Cheaper than per-element detection.
    """
    try:
        text = await page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        )
    except Exception:
        text = ""

    if not text:
        return {
            "login_gate": False,
            "vat_status": "unknown",
            "has_tier_pricing": False,
            "min_order_quantity": None,
            "availability": "",
            "tier_pricing": None,
        }

    # Limit text length to avoid very expensive regex on huge pages
    scan_text = text[:20000]

    # Try structured tier extraction first. If it yields tiers, the
    # has_tier_pricing flag is implicitly true. Otherwise fall back to
    # the boolean presence check.
    tier_pricing = _extract_tier_pricing(scan_text)
    has_tier = bool(tier_pricing) or _detect_tier_pricing(scan_text)

    return {
        "login_gate": _detect_login_gate(scan_text),
        "vat_status": _detect_vat_status(scan_text),
        "has_tier_pricing": has_tier,
        "min_order_quantity": _detect_min_order_quantity(scan_text),
        "availability": _detect_availability(scan_text),
        "tier_pricing": tier_pricing,
    }


# -- Main extraction function --------------------------------------------------

# URL query parameters that indicate a SERP / search-result page rather
# than a concrete product page. We inject these ourselves via the
# _AGGREGATOR_SEARCH_TEMPLATES in search_prices.py -- they are useful
# for URL discovery but extracting "prices" from them yields whatever
# listing the aggregator shows on top, which is usually unrelated to
# the exact SKU we asked for.
_SEARCH_URL_PARAMS = {"fs", "q", "query", "search", "keyword", "keywords", "s"}


def _is_aggregator_search_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        if not parsed.query:
            return False
        params = parse_qs(parsed.query)
        return any(key in _SEARCH_URL_PARAMS for key in params)
    except Exception:
        return False


def _query_title_overlap(query: str, title: str) -> float:
    """Return token-overlap ratio in [0.0, 1.0] between query and title.

    Used by the title-gate to reject product pages that clearly show
    a different SKU than the one we queried for. Case-insensitive,
    stop-words removed, 3+ char tokens only.
    """
    if not query or not title:
        return 1.0  # no info -> don't block

    stop = {"und", "oder", "fuer", "mit", "ohne", "the", "and", "for",
            "with", "shop", "online", "kaufen", "preis", "bestellen",
            "von", "der", "die", "das", "auf"}

    def _tok(s: str) -> set[str]:
        raw = re.findall(r"[\w.\-]+", s.lower(), flags=re.UNICODE)
        return {t for t in raw if len(t) >= 3 and t not in stop}

    q, t = _tok(query), _tok(title)
    if not q:
        return 1.0
    return len(q & t) / len(q)


def _extract_digit_anchors(text: str, min_len: int = 3) -> list[str]:
    """Extract digit-sequences of length >= min_len from text.

    Model-number queries like "FLUKE 1674FC SCH" or "Niedax WRL 200.400 F"
    carry most of their discriminating signal in digit runs ("1674",
    "200", "400"). Catalogs format these differently across sites
    ("1674FC" vs "1674 FC"; "200.400" vs "200/400"; "200.400" vs
    "200x400") but the raw digit runs are stable.
    """
    return [s for s in re.findall(r"\d+", text) if len(s) >= min_len]


def _title_matches_query(query: str, title: str) -> bool:
    """Return True if the title plausibly refers to the queried product.

    Policy (in order):
      1. Extract the brand = first alpha-token of length >= 3 (FLUKE,
         Niedax, Bosch, Dell, ...). If query has no such token, skip
         the brand check.
      2. Extract digit anchors = digit-runs of length >= 3 ("1674",
         "200", "400"). Those are the stable core of model numbers
         and survive catalog formatting churn ("200.400" vs "200/400").
      3. If digit anchors are present, require >= 80% of them in the
         title (as substrings of an alphanumeric-normalised form).
         Additionally require brand or high token-overlap.
      4. If no digit anchors, require brand present AND word-token
         overlap >= 0.5 (otherwise descriptive queries like "Dell
         Monitor 27 Zoll" would accept a HP monitor page).
    """
    if not query or not title:
        return True  # no info -> don't block

    t_low = title.lower()
    alpha_tokens = re.findall(r"[A-Za-z]{3,}", query)
    brand = alpha_tokens[0].lower() if alpha_tokens else None
    anchors = _extract_digit_anchors(query, min_len=3)

    # Case A: Query carries digit anchors (model numbers).
    if anchors:
        # Normalise title: collapse non-alphanumerics so "200/400" and
        # "200.400" both match "200400".
        t_digits = re.sub(r"[^0-9a-z]+", "", t_low)
        missing = [a for a in anchors if a not in t_digits]
        coverage = 1.0 - (len(missing) / len(anchors))
        if coverage < 0.8:
            return False
        # Model numbers can repeat across brands (e.g. "200.400" is
        # a cable-tray designation shared by several manufacturers).
        # Require brand in title OR enough word-token overlap.
        if brand and brand not in t_low:
            if _query_title_overlap(query, title) < _TITLE_GATE_MIN_OVERLAP:
                return False
        return True

    # Case B: Descriptive query (no digit anchors). Require brand +
    # overlap >= 0.5.
    if brand and brand not in t_low:
        return False
    return _query_title_overlap(query, title) >= _TITLE_GATE_MIN_OVERLAP


# Domains where the product H1 / page title is reliable and should be
# cross-checked against the query. On these sites a title-mismatch is
# a strong signal of SKU confusion (e.g. Geizhals returning the FTT
# variant for a FLUKE 1674FC SCH query).
_TITLE_GATE_DOMAINS = {
    "geizhals.de", "geizhals.at",
    "idealo.de",
    "billiger.de",
}
_TITLE_GATE_MIN_OVERLAP = 0.5


async def _title_gate_rejects(page: Page, url: str, query: str) -> bool:
    """Return True if the page's H1/title disagrees too strongly with query.

    Only active for domains in `_TITLE_GATE_DOMAINS`. Uses a digit-
    anchor-aware matcher (`_title_matches_query`) so variant suffixes
    that differ between query and catalogue ("FLUKE 1674FC SCH" vs
    "Fluke 1674 FC FTT Installationstester") still accept the page
    as long as the numeric model core is present.
    """
    if not query:
        return False
    domain = _get_domain(url)
    if not any(d in domain for d in _TITLE_GATE_DOMAINS):
        return False
    try:
        # Prefer product H1, fall back to document title
        h1_text = ""
        for sel in ("h1.productinfo__title", "h1.product-title",
                    "h1[data-id='product-name']", "h1"):
            try:
                el = await page.query_selector(sel)
                if el:
                    h1_text = (await el.inner_text()).strip()
                    if h1_text:
                        break
            except Exception:
                continue
        if not h1_text:
            try:
                h1_text = (await page.title()) or ""
            except Exception:
                h1_text = ""
        if not h1_text:
            return False
        if _title_matches_query(query, h1_text):
            logger.debug(
                "Title-gate ACCEPTED %s: title=%r query=%r",
                domain, h1_text[:80], query[:60],
            )
            return False
        logger.info(
            "Title-gate REJECTED %s: title=%r query=%r (digit-anchor or token overlap mismatch)",
            domain, h1_text[:80], query[:60],
        )
        return True
    except Exception as e:
        logger.debug("Title-gate check failed for %s: %s", domain, e)
        return False


async def extract_prices(
    page: Page,
    url: str,
    query: str = "",
) -> list[ExtractedOffer]:
    """Extract prices from a page using all available strategies.

    Tries strategies in priority order and returns the first non-empty result.
    """
    # Phase N: aggregator SERP tile-parser. When the URL is a
    # search-result page on idealo or geizhals, parse the visible
    # tiles and emit one offer per tile. Without this we returned
    # an empty list -- which is what Testlauf 3 saw for Pos 1, 4, 11.
    # Each tile carries the aggregator's lowest "ab EUR" price + a
    # deep product-page URL the operator can click through.
    # Title-gate / part-number-gate downstream catch wrong-SKU rows.
    if _is_aggregator_search_url(url):
        try:
            from urllib.parse import urlparse as _urlparse
            host = _urlparse(url).netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            if host == "idealo.de" or host.endswith(".idealo.de"):
                tiles = await _extract_idealo_search_tiles(page, url)
                if tiles:
                    logger.info(
                        "Idealo SERP tiles: %d offers extracted from %s",
                        len(tiles), url[:80],
                    )
                    return tiles
            elif host == "geizhals.de" or host.endswith(".geizhals.de") \
                    or host == "geizhals.at" or host == "geizhals.eu":
                tiles = await _extract_geizhals_search_tiles(page, url)
                if tiles:
                    logger.info(
                        "Geizhals SERP tiles: %d offers extracted from %s",
                        len(tiles), url[:80],
                    )
                    return tiles
        except Exception as e:
            logger.debug("SERP tile extraction failed: %s", e)
        # No tiles parseable -> behave like pre-Phase-N (empty)
        return []

    # Title-gate: on major aggregators (Geizhals/Idealo/billiger) reject
    # the whole page if the product H1/title disagrees with the query.
    # Catches SKU confusion the URL pattern cannot (e.g. FTT vs SCH).
    if await _title_gate_rejects(page, url, query):
        return []

    # 1. Site-specific selectors
    offers = await _extract_site_specific(page, url)
    if offers:
        return offers

    # 2. JSON-LD / Schema.org
    offers = await _extract_json_ld(page, url)
    if offers:
        return offers

    # 3. OpenGraph / Microdata
    offers = await _extract_microdata(page, url)
    if offers:
        return offers

    # 4. Generic CSS heuristics
    offers = await _extract_generic_css(page, url)
    if offers:
        return offers

    # 5. Regex fallback
    offers = await _extract_regex(page, url)
    return offers
