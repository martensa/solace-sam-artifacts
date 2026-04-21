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
    """A price offer extracted from a page."""

    __slots__ = ("merchant", "price", "shipping_cost", "currency", "url", "availability")

    def __init__(
        self,
        merchant: str,
        price: float,
        shipping_cost: float = 0.0,
        currency: str = "EUR",
        url: str = "",
        availability: str = "",
    ) -> None:
        self.merchant = merchant
        self.price = price
        self.shipping_cost = shipping_cost
        self.currency = currency
        self.url = url
        self.availability = availability

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
        }


# -- Price parsing (German locale) --------------------------------------------

def parse_price(text: str) -> Optional[float]:
    """Parse a price string to float. Handles German and English formats."""
    if not text:
        return None
    text = text.replace("EUR", "").replace("Euro", "").strip()
    text = text.replace("\u20ac", "").strip()  # euro sign

    # German format: 1.234,56
    match = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", text)
    if match:
        return float(match.group(1).replace(".", "").replace(",", "."))

    # English/simple format: 1234.56 or 1234,56
    match = re.search(r"(\d+)[.,](\d{2})\b", text)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")

    # Integer only
    match = re.search(r"(\d+)", text)
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


# -- Site-specific extractors --------------------------------------------------

SITE_SELECTORS: dict[str, dict[str, Any]] = {
    "idealo.de": {
        "offer_container": ".productOffers-listItem",
        "merchant": ".productOffers-listItemOfferShopName, .shop-name",
        "price": ".productOffers-listItemOfferPrice, .price",
        "shipping": ".productOffers-listItemDeliveryCost, .delivery-cost",
    },
    "geizhals.de": {
        "offer_container": ".offer, .offerlist__offer",
        "merchant": ".offer__shop-name, .merchant__name",
        "price": ".offer__price, .productlist__price",
        "shipping": ".offer__shipping, .productlist__shipping",
    },
    "geizhals.at": {
        "offer_container": ".offer, .offerlist__offer",
        "merchant": ".offer__shop-name, .merchant__name",
        "price": ".offer__price, .productlist__price",
        "shipping": ".offer__shipping, .productlist__shipping",
    },
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


async def _extract_site_specific(page: Page, url: str) -> list[ExtractedOffer]:
    """Try site-specific CSS selectors for known domains."""
    domain = _get_domain(url)
    config = None
    for site_domain, site_config in SITE_SELECTORS.items():
        if site_domain in domain:
            config = site_config
            break

    if config is None:
        return []

    offers: list[ExtractedOffer] = []

    # Multi-offer pages (Idealo, Geizhals)
    container_sel = config.get("offer_container")
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
                    ))
        except Exception as e:
            logger.debug("Site-specific extraction failed for %s: %s", domain, e)

    # Single-price pages (Amazon, Otto, etc.)
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
                                    availability=offer_data.get("availability", ""),
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
                            availability=offer_data.get("availability", ""),
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
                    ))
    except Exception as e:
        logger.debug("Microdata extraction failed for %s: %s", url, e)

    if offers:
        logger.info("Microdata: extracted %d offers from %s", len(offers), _get_domain(url))
    return offers


# -- Generic CSS heuristic extraction ------------------------------------------

async def _extract_generic_css(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract prices using generic CSS selectors common across e-commerce sites."""
    selectors = [
        ".price",
        "[data-price]",
        "[class*='price']",
        "[class*='Price']",
        ".product-price",
        ".current-price",
    ]

    offers: list[ExtractedOffer] = []
    seen_prices: set[float] = set()

    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements[:10]:
                # Try data-price attribute first
                data_price = await el.get_attribute("data-price")
                if data_price:
                    price = parse_price(data_price)
                else:
                    text = (await el.inner_text()).strip()
                    price = parse_price(text)

                if price and _is_reasonable_price(price) and price not in seen_prices:
                    seen_prices.add(price)
                    offers.append(ExtractedOffer(
                        merchant=_get_domain(url),
                        price=price,
                        url=url,
                    ))
        except Exception:
            continue

        if offers:
            break  # Found prices with this selector, no need to try others

    if offers:
        logger.info("Generic CSS: extracted %d offers from %s", len(offers), _get_domain(url))
    return offers


# -- Regex fallback on visible text --------------------------------------------

# Price patterns that require a currency marker to avoid false positives
_PRICE_RE_DE = re.compile(
    r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*(?:EUR|Euro|\u20ac)",
    re.IGNORECASE,
)
_PRICE_RE_EN = re.compile(
    r"(?:EUR|Euro|\u20ac)\s*(\d{1,6}\.\d{2})",
    re.IGNORECASE,
)


async def _extract_regex(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract prices from visible page text using regex."""
    offers: list[ExtractedOffer] = []
    seen_prices: set[float] = set()

    try:
        # Get visible text content
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if not text:
            return []

        # Search for German-format prices with EUR marker
        for match in _PRICE_RE_DE.finditer(text):
            price_str = match.group(1).replace(".", "").replace(",", ".")
            try:
                price = float(price_str)
            except ValueError:
                continue
            if _is_reasonable_price(price) and price not in seen_prices:
                seen_prices.add(price)
                offers.append(ExtractedOffer(
                    merchant=_get_domain(url),
                    price=price,
                    url=url,
                ))

        # Search for English-format prices
        for match in _PRICE_RE_EN.finditer(text):
            try:
                price = float(match.group(1))
            except ValueError:
                continue
            if _is_reasonable_price(price) and price not in seen_prices:
                seen_prices.add(price)
                offers.append(ExtractedOffer(
                    merchant=_get_domain(url),
                    price=price,
                    url=url,
                ))

    except Exception as e:
        logger.debug("Regex extraction failed for %s: %s", url, e)

    # Limit to top 10 (sorted by price)
    offers.sort(key=lambda o: o.price)
    offers = offers[:10]

    if offers:
        logger.info("Regex: extracted %d offers from %s", len(offers), _get_domain(url))
    return offers


# -- Main extraction function --------------------------------------------------

async def extract_prices(page: Page, url: str) -> list[ExtractedOffer]:
    """Extract prices from a page using all available strategies.

    Tries strategies in priority order and returns the first non-empty result.
    """
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
