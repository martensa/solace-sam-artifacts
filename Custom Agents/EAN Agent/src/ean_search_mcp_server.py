#!/usr/bin/env python3
"""
EAN Search MCP Server -- unified procurement-focused EAN/barcode lookup.

Supports two database backends (selectable via EAN_DATABASE_BACKEND env var):

  - ean_search : ean-search.org API (1B+ barcodes, requires API token)
  - upcitemdb  : UPCitemdb API (687M+ products, free tier: 100 req/day)

Key features:
  - Product name search with automatic pagination (collects ALL results)
  - Fuzzy / similar product search (ean_search backend only)
  - Category-scoped and brand-filtered search
  - Barcode prefix search (ean_search backend only)
  - Single EAN lookup with full product details
  - EAN checksum validation (local, no API call)
  - Issuing country lookup (local, no API call)
  - Configurable max pages to balance completeness vs. API quota
  - Compact JSON output to minimise LLM token usage
  - Robust error handling with actionable hints

Protocol: MCP JSON-RPC 2.0 over stdio
"""

import json
import logging
import os
import sys
import time

import requests

# -- logging (never stdout -- MCP uses it) ------------------------------------
LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
LOG_FILE = os.environ.get("MCP_LOG_FILE", "ean_search_mcp.log")
log = logging.getLogger("ean-search-mcp")
log.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_fh)
log.propagate = False


# -- helpers ------------------------------------------------------------------
def _env(key, default):
    """Get env var, treating empty string same as unset (returns default)."""
    val = os.environ.get(key, "")
    return val if val else default


def _env_int(key, default):
    return int(_env(key, str(default)))


def _env_float(key, default):
    return float(_env(key, str(default)))


# -- Backend selection --------------------------------------------------------
BACKEND = _env("EAN_DATABASE_BACKEND", "upcitemdb").lower().strip()
if BACKEND not in ("ean_search", "upcitemdb"):
    log.error(
        "EAN_DATABASE_BACKEND must be 'ean_search' or 'upcitemdb', got '%s'",
        BACKEND,
    )
    sys.exit(1)

# -- Common config ------------------------------------------------------------
MAX_PAGES = _env_int("EAN_SEARCH_MAX_PAGES", 10 if BACKEND == "ean_search" else 5)
MAX_CHARS = _env_int("MCP_MAX_RESPONSE_CHARS", 25000)
REQ_TIMEOUT = _env_int("EAN_SEARCH_TIMEOUT", 30)
MIN_REQUEST_INTERVAL = _env_float(
    "EAN_SEARCH_MIN_INTERVAL", 0.5 if BACKEND == "ean_search" else 1.0
)

_last_request_time = 0.0


def _throttle():
    """Simple rate limiter to avoid hammering the API."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


MAX_RETRIES = _env_int("EAN_SEARCH_MAX_RETRIES", 1)
RETRY_BACKOFF = _env_float("EAN_SEARCH_RETRY_BACKOFF", 2.0)


def _is_retryable(exc):
    """Check if an HTTP error is a transient 5xx worth retrying."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    return False


# =============================================================================
# EAN-SEARCH.ORG BACKEND
# =============================================================================
ES_API_BASE = _env("EAN_SEARCH_API_BASE", "https://api.ean-search.org/api")
ES_API_TOKEN = os.environ.get("EAN_SEARCH_API_TOKEN", "")
ES_LANGUAGE = _env("EAN_SEARCH_LANGUAGE", "1")  # 1=English, 99=any


def _es_api_call(op, params=None):
    """Make a single API call to ean-search.org and return parsed JSON.

    Retries up to MAX_RETRIES times on transient 5xx / connection errors.
    """
    query = {"token": ES_API_TOKEN, "op": op, "format": "json"}
    if params:
        query.update(params)

    log.info(
        "API call: op=%s params=%s",
        op,
        {k: v for k, v in query.items() if k != "token"},
    )

    last_exc = None
    for attempt in range(1 + MAX_RETRIES):
        _throttle()
        try:
            r = requests.get(ES_API_BASE, params=query, timeout=REQ_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "error" in data:
                log.warning("API error: %s", data["error"])
                return {"error": data["error"]}
            return data
        except requests.HTTPError as e:
            last_exc = e
            if _is_retryable(e) and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (attempt + 1)
                log.warning("Retryable HTTP %d, retry %d after %.1fs",
                            e.response.status_code, attempt + 1, wait)
                time.sleep(wait)
                continue
            status = e.response.status_code if e.response is not None else 0
            detail = e.response.text[:500] if e.response is not None else str(e)
            log.error("HTTP %d: %s", status, detail)
            return {"error": "HTTP %d" % status, "detail": detail}
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if _is_retryable(e) and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (attempt + 1)
                log.warning("Retryable %s, retry %d after %.1fs",
                            type(e).__name__, attempt + 1, wait)
                time.sleep(wait)
                continue
            if isinstance(e, requests.Timeout):
                log.error("Request timed out after %ds", REQ_TIMEOUT)
                return {"error": "Request timed out", "hint": "Try a more specific search term."}
            log.error("Request failed: %s", e)
            return {"error": str(e)}
        except requests.RequestException as e:
            log.error("Request failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            log.error("Invalid JSON response")
            return {"error": "Invalid JSON response from API"}

    log.error("All retries exhausted: %s", last_exc)
    return {"error": "Request failed after %d retries" % (MAX_RETRIES + 1)}


def _es_product_search(name, page=0, language=None):
    params = {"name": name, "page": str(page)}
    if language:
        params["language"] = str(language)
    return _es_api_call("product-search", params)


def _es_similar_product_search(name, page=0, language=None):
    params = {"name": name, "page": str(page)}
    if language:
        params["language"] = str(language)
    return _es_api_call("similar-product-search", params)


def _es_category_search(category, name, page=0):
    params = {"category": str(category), "name": name, "page": str(page)}
    return _es_api_call("category-search", params)


def _es_barcode_lookup(ean, language=None):
    params = {"ean": str(ean)}
    if language:
        params["language"] = str(language)
    return _es_api_call("barcode-lookup", params)


def _es_barcode_prefix_search(prefix):
    return _es_api_call("barcode-prefix-search", {"prefix": str(prefix)})


def _es_paginated_search(search_fn, max_pages=None, **kwargs):
    """Auto-paginate ean-search.org to collect ALL results. Deduplicates by EAN."""
    if max_pages is None:
        max_pages = MAX_PAGES
    all_results = []
    seen_eans = set()
    page = 0

    while page < max_pages:
        result = search_fn(page=page, **kwargs)

        if isinstance(result, dict) and "error" in result:
            if page == 0:
                return result
            break

        if isinstance(result, list):
            if not result:
                break
            new_count = 0
            for item in result:
                ean = item.get("ean", "")
                if ean and ean not in seen_eans:
                    seen_eans.add(ean)
                    all_results.append(item)
                    new_count += 1
            if new_count == 0:
                break
            page += 1
        else:
            if page == 0:
                return result
            break

    return all_results


# =============================================================================
# UPCitemdb BACKEND
# =============================================================================
UPC_API_BASE = _env("UPCITEMDB_API_BASE", "https://api.upcitemdb.com/prod/trial")
UPC_API_KEY = os.environ.get("UPCITEMDB_API_KEY", "")

# Fields to keep from UPCitemdb responses (strip noise to save LLM tokens)
_KEEP_FIELDS = {
    "ean", "title", "brand", "category", "description", "model",
    "color", "size", "weight", "lowest_recorded_price",
    "highest_recorded_price", "upc", "asin", "elid",
}


def _upc_trim_item(item):
    """Strip noisy fields (images, offers, URLs) to save LLM tokens."""
    return {k: v for k, v in item.items() if k in _KEEP_FIELDS and v}


def _upc_api_get(endpoint, params=None):
    """Make a GET request to UPCitemdb and return parsed JSON.

    Retries up to MAX_RETRIES times on transient 5xx / connection errors.
    """
    url = "%s/%s" % (UPC_API_BASE, endpoint)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip,deflate",
        "User-Agent": "SolaceEANSearchAgent/1.0.0",
    }
    if UPC_API_KEY:
        headers["user_key"] = UPC_API_KEY
        headers["key_type"] = "3scale"

    log.info("API GET %s params=%s", url, params)

    last_exc = None
    for attempt in range(1 + MAX_RETRIES):
        _throttle()
        try:
            r = requests.get(url, params=params, headers=headers, timeout=REQ_TIMEOUT)

            remaining = r.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                log.info("Rate limit remaining: %s", remaining)
                try:
                    if int(remaining) <= 5:
                        log.warning("Rate limit nearly exhausted: %s remaining", remaining)
                except (ValueError, TypeError):
                    pass

            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("code") not in ("OK", None):
                log.warning("API error: %s", data.get("code"))
                return {"error": data.get("code"), "message": data.get("message", "")}
            return data
        except requests.HTTPError as e:
            last_exc = e
            status = e.response.status_code if e.response is not None else 0
            if _is_retryable(e) and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (attempt + 1)
                log.warning("Retryable HTTP %d, retry %d after %.1fs",
                            status, attempt + 1, wait)
                time.sleep(wait)
                continue
            detail = e.response.text[:500] if e.response is not None else str(e)
            log.error("HTTP %d: %s", status, detail)
            if status == 429:
                return {
                    "error": "Rate limit exceeded",
                    "hint": "Free tier allows 100 requests/day. Wait until tomorrow or use a paid plan.",
                }
            return {"error": "HTTP %d" % status, "detail": detail}
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if _is_retryable(e) and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * (attempt + 1)
                log.warning("Retryable %s, retry %d after %.1fs",
                            type(e).__name__, attempt + 1, wait)
                time.sleep(wait)
                continue
            if isinstance(e, requests.Timeout):
                log.error("Request timed out after %ds", REQ_TIMEOUT)
                return {"error": "Request timed out", "hint": "Try a more specific search term."}
            log.error("Request failed: %s", e)
            return {"error": str(e)}
        except requests.RequestException as e:
            log.error("Request failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            log.error("Invalid JSON response")
            return {"error": "Invalid JSON response from API"}

    log.error("All retries exhausted: %s", last_exc)
    return {"error": "Request failed after %d retries" % (MAX_RETRIES + 1)}


def _upc_search(query, brand=None, category=None, offset=0):
    params = {"s": query, "offset": str(offset)}
    if brand:
        params["brand"] = brand
    if category:
        params["category"] = category
    return _upc_api_get("search", params)


def _upc_lookup(upc):
    return _upc_api_get("lookup", {"upc": str(upc)})


def _upc_paginated_search(query, brand=None, category=None, max_pages=None):
    """Auto-paginate UPCitemdb search. Deduplicates by EAN."""
    if max_pages is None:
        max_pages = MAX_PAGES
    all_items = []
    seen_eans = set()
    offset = 0

    for page_num in range(max_pages):
        result = _upc_search(query, brand=brand, category=category, offset=offset)

        if isinstance(result, dict) and "error" in result:
            if page_num == 0:
                return result
            break

        items = result.get("items", [])
        total = result.get("total", 0)

        if not items:
            break

        for item in items:
            ean = item.get("ean", "")
            if ean and ean not in seen_eans:
                seen_eans.add(ean)
                all_items.append(_upc_trim_item(item))

        offset += len(items)
        if offset >= total or offset >= 1000:
            break

    return all_items


# =============================================================================
# LOCAL TOOLS (shared by both backends)
# =============================================================================
def _validate_ean(ean_str):
    """Validate EAN/UPC/GTIN check digit locally (GS1 standard)."""
    ean_str = ean_str.strip()
    if not ean_str.isdigit():
        return {"valid": False, "ean": ean_str, "error": "EAN must contain only digits."}
    if len(ean_str) not in (8, 12, 13, 14):
        return {
            "valid": False,
            "ean": ean_str,
            "error": "EAN must be 8, 12, 13, or 14 digits (got %d)." % len(ean_str),
        }

    digits = [int(d) for d in ean_str]
    check = digits[-1]
    total = 0
    for i, d in enumerate(digits[:-1]):
        weight = 1 if (len(digits) - 1 - i) % 2 == 0 else 3
        total += d * weight
    expected = (10 - (total % 10)) % 10

    return {
        "valid": check == expected,
        "ean": ean_str,
        "length": len(ean_str),
        "type": {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}.get(
            len(ean_str)
        ),
        "check_digit": check,
        "expected_check_digit": expected,
    }


_GS1_PREFIXES = [
    ("000", "019", "United States"), ("020", "029", "Restricted distribution"),
    ("030", "039", "United States"), ("040", "049", "Restricted distribution"),
    ("050", "059", "Coupons"), ("060", "099", "United States / Canada"),
    ("100", "139", "United States"), ("200", "299", "Restricted distribution"),
    ("300", "379", "France / Monaco"), ("380", "380", "Bulgaria"),
    ("383", "383", "Slovenia"), ("385", "385", "Croatia"),
    ("387", "387", "Bosnia and Herzegovina"), ("389", "389", "Montenegro"),
    ("390", "390", "Kosovo"), ("400", "440", "Germany"),
    ("450", "459", "Japan"), ("460", "469", "Russia"),
    ("470", "470", "Kyrgyzstan"), ("471", "471", "Taiwan"),
    ("474", "474", "Estonia"), ("475", "475", "Latvia"),
    ("476", "476", "Azerbaijan"), ("477", "477", "Lithuania"),
    ("478", "478", "Uzbekistan"), ("479", "479", "Sri Lanka"),
    ("480", "480", "Philippines"), ("481", "481", "Belarus"),
    ("482", "482", "Ukraine"), ("484", "484", "Moldova"),
    ("485", "485", "Armenia"), ("486", "486", "Georgia"),
    ("487", "487", "Kazakhstan"), ("488", "488", "Tajikistan"),
    ("489", "489", "Hong Kong"), ("490", "499", "Japan"),
    ("500", "509", "United Kingdom"), ("520", "521", "Greece"),
    ("528", "528", "Lebanon"), ("529", "529", "Cyprus"),
    ("530", "530", "Albania"), ("531", "531", "North Macedonia"),
    ("535", "535", "Malta"), ("539", "539", "Ireland"),
    ("540", "549", "Belgium / Luxembourg"), ("560", "560", "Portugal"),
    ("569", "569", "Iceland"), ("570", "579", "Denmark / Greenland"),
    ("590", "590", "Poland"), ("594", "594", "Romania"),
    ("599", "599", "Hungary"), ("600", "601", "South Africa"),
    ("603", "603", "Ghana"), ("604", "604", "Senegal"),
    ("608", "608", "Bahrain"), ("609", "609", "Mauritius"),
    ("611", "611", "Morocco"), ("613", "613", "Algeria"),
    ("615", "615", "Nigeria"), ("616", "616", "Kenya"),
    ("618", "618", "Ivory Coast"), ("619", "619", "Tunisia"),
    ("620", "620", "Tanzania"), ("621", "621", "Syria"),
    ("622", "622", "Egypt"), ("623", "623", "Brunei"),
    ("624", "624", "Libya"), ("625", "625", "Jordan"),
    ("626", "626", "Iran"), ("627", "627", "Kuwait"),
    ("628", "628", "Saudi Arabia"), ("629", "629", "United Arab Emirates"),
    ("640", "649", "Finland"), ("690", "699", "China"),
    ("700", "709", "Norway"), ("729", "729", "Israel"),
    ("730", "739", "Sweden"), ("740", "740", "Guatemala"),
    ("741", "741", "El Salvador"), ("742", "742", "Honduras"),
    ("743", "743", "Nicaragua"), ("744", "744", "Costa Rica"),
    ("745", "745", "Panama"), ("746", "746", "Dominican Republic"),
    ("750", "750", "Mexico"), ("754", "755", "Canada"),
    ("759", "759", "Venezuela"), ("760", "769", "Switzerland / Liechtenstein"),
    ("770", "771", "Colombia"), ("773", "773", "Uruguay"),
    ("775", "775", "Peru"), ("777", "777", "Bolivia"),
    ("778", "779", "Argentina"), ("780", "780", "Chile"),
    ("784", "784", "Paraguay"), ("786", "786", "Ecuador"),
    ("789", "790", "Brazil"), ("800", "839", "Italy / San Marino / Vatican"),
    ("840", "849", "Spain / Andorra"), ("850", "850", "Cuba"),
    ("858", "858", "Slovakia"), ("859", "859", "Czech Republic"),
    ("860", "860", "Serbia"), ("865", "865", "Mongolia"),
    ("867", "867", "North Korea"), ("868", "869", "Turkey"),
    ("870", "879", "Netherlands"), ("880", "880", "South Korea"),
    ("884", "884", "Cambodia"), ("885", "885", "Thailand"),
    ("888", "888", "Singapore"), ("890", "890", "India"),
    ("893", "893", "Vietnam"), ("896", "896", "Pakistan"),
    ("899", "899", "Indonesia"), ("900", "919", "Austria"),
    ("930", "939", "Australia"), ("940", "949", "New Zealand"),
    ("950", "950", "GS1 Global Office"), ("951", "951", "EPCglobal"),
    ("955", "955", "Malaysia"), ("958", "958", "Macau"),
    ("960", "969", "GS1 Global Office (GTIN-8)"),
    ("977", "977", "Serial publications (ISSN)"),
    ("978", "979", "Bookland (ISBN)"),
    ("980", "980", "Refund receipts"), ("981", "984", "Common Currency Coupons"),
    ("990", "999", "Coupons"),
]


def _local_issuing_country(ean_str):
    """Look up issuing country from EAN prefix (GS1 standard)."""
    ean_str = ean_str.strip()
    if not ean_str.isdigit() or len(ean_str) < 3:
        return {"ean": ean_str, "country": "Unknown", "error": "Invalid EAN format."}

    prefix = ean_str[:3]
    for lo, hi, country in _GS1_PREFIXES:
        if lo <= prefix <= hi:
            return {"ean": ean_str, "prefix": prefix, "country": country}

    return {"ean": ean_str, "prefix": prefix, "country": "Unknown / Unassigned"}


# =============================================================================
# RESPONSE FORMATTING
# =============================================================================
def _cap(obj):
    """Serialize to compact JSON, trimming product lists to stay within MAX_CHARS.

    Instead of slicing a JSON string (which breaks JSON syntax), this function
    removes products from the end of the list until the serialized output fits.
    """
    text = json.dumps(obj, separators=(",", ":"))
    if len(text) <= MAX_CHARS:
        return text

    # Try trimming the product list if present
    if isinstance(obj, dict) and "products" in obj and isinstance(obj["products"], list):
        products = obj["products"]
        total = len(products)
        # Add metadata fields BEFORE the binary search so their size
        # is included in the budget (prevents overshooting MAX_CHARS).
        obj["truncated"] = True
        obj["showing"] = 0
        obj["total_results"] = total
        obj["note"] = (
            "Showing %d of %d results. "
            "Use more specific search terms or filters to see others." % (total, total)
        )
        lo, hi = 0, total
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            obj["products"] = products[:mid]
            obj["showing"] = mid
            obj["note"] = (
                "Showing %d of %d results. "
                "Use more specific search terms or filters to see others."
                % (mid, total)
            )
            candidate = json.dumps(obj, separators=(",", ":"))
            if len(candidate) <= MAX_CHARS:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        obj["products"] = products[:best]
        obj["showing"] = best
        obj["note"] = (
            "Showing %d of %d results. "
            "Use more specific search terms or filters to see others." % (best, total)
        )
        return json.dumps(obj, separators=(",", ":"))

    # Fallback: raw truncation (non-list responses are typically small)
    return text[:MAX_CHARS] + (
        "\n...TRUNCATED (%d total chars). "
        "Use more specific search terms or filters to narrow results." % len(text)
    )


def _format_results(results, query_info=""):
    """Format search results with summary metadata."""
    if isinstance(results, dict) and "error" in results:
        return results

    if isinstance(results, list):
        return {
            "total_results": len(results),
            "query": query_info,
            "database": "ean-search.org" if BACKEND == "ean_search" else "UPCitemdb",
            "products": results,
        }

    return results


# =============================================================================
# MCP TOOL DEFINITIONS -- dynamically built per backend
# =============================================================================
def _build_tools():
    """Build the tool list based on the active backend."""
    tools = []

    if BACKEND == "ean_search":
        tools.append({
            "name": "ean_product_search",
            "description": (
                "[SEARCH] Search for products by name and return all matching "
                "EAN barcodes. Auto-paginates to collect all results. Primary "
                "tool for finding EANs from a product name or description. "
                "Returns EAN, product name, category, and issuing country. "
                "Database: ean-search.org (1B+ barcodes)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Product name or description to search for "
                            "(e.g. 'Coca Cola 330ml', 'A4 printer paper')."
                        ),
                    },
                    "language": {
                        "type": "string",
                        "description": (
                            "Language filter: 1=English, 2=French, 3=German, "
                            "4=Spanish, 5=Portuguese, 6=Italian, 7=Dutch, "
                            "8=Polish, 9=Swedish, 10=Turkish, 99=any. Default: 1."
                        ),
                        "default": "1",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Max pages to retrieve (10 results each). Default: 10.",
                        "default": 10, "minimum": 1, "maximum": 50,
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        })
        tools.append({
            "name": "ean_similar_product_search",
            "description": (
                "[FUZZY SEARCH] Search for products with names similar to the "
                "query. Uses fuzzy matching -- works well when the exact product "
                "name is unknown or misspelled. Auto-paginates all results."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Approximate product name or description.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Language filter: 1=English, 99=any. Default: 1.",
                        "default": "1",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Max pages to retrieve. Default: 10.",
                        "default": 10, "minimum": 1, "maximum": 50,
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        })
        tools.append({
            "name": "ean_category_search",
            "description": (
                "[CATEGORY SEARCH] Search for products within a specific "
                "product category. Combines category filtering with name "
                "search. Auto-paginates all results."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category ID (numeric, Google product taxonomy).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Product name or keyword within the category.",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Max pages to retrieve. Default: 10.",
                        "default": 10, "minimum": 1, "maximum": 50,
                    },
                },
                "required": ["category", "name"],
                "additionalProperties": False,
            },
        })
        tools.append({
            "name": "ean_barcode_prefix_search",
            "description": (
                "[PREFIX SEARCH] Search for products by EAN barcode prefix. "
                "Finds all products from a specific manufacturer. Supports "
                "wildcards, e.g. '0885909*' for Apple products."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": (
                            "EAN barcode prefix (e.g. '0885909' for Apple). "
                            "Can include * wildcard."
                        ),
                    },
                },
                "required": ["prefix"],
                "additionalProperties": False,
            },
        })
    else:  # upcitemdb
        tools.append({
            "name": "ean_product_search",
            "description": (
                "[SEARCH] Search for products by name and return all matching "
                "EAN/UPC barcodes. Auto-paginates to collect all results. "
                "Primary tool for finding EANs from a product name or "
                "description. Returns EAN, product name, brand, category, and "
                "price range. Database: UPCitemdb (687M+ products)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Product name or description to search for "
                            "(e.g. 'Coca Cola 330ml', 'Sony TV')."
                        ),
                    },
                    "brand": {
                        "type": "string",
                        "description": (
                            "Optional brand name to filter results "
                            "(e.g. 'Sony', 'Apple'). Leave empty for all brands."
                        ),
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Max pages to retrieve (10 results each). Default: 5.",
                        "default": 5, "minimum": 1, "maximum": 20,
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        })
        tools.append({
            "name": "ean_category_search",
            "description": (
                "[CATEGORY SEARCH] Search for products by name filtered to a "
                "specific category. Use when you know the product category. "
                "Auto-paginates all results."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Product name or keyword to search for.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category name (e.g. 'Electronics', 'Food').",
                    },
                    "brand": {
                        "type": "string",
                        "description": "Optional brand name to further filter.",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Max pages to retrieve. Default: 5.",
                        "default": 5, "minimum": 1, "maximum": 20,
                    },
                },
                "required": ["name", "category"],
                "additionalProperties": False,
            },
        })

    # -- Common tools for both backends --
    barcode_lookup_props = {
        "ean": {
            "type": "string",
            "description": "The EAN, UPC, or GTIN barcode number (8, 12, 13, or 14 digits).",
        },
    }
    if BACKEND == "ean_search":
        barcode_lookup_props["language"] = {
            "type": "string",
            "description": "Language filter: 1=English, 99=any. Default: 1.",
            "default": "1",
        }

    tools.append({
        "name": "ean_barcode_lookup",
        "description": (
            "[LOOKUP] Look up a specific EAN/UPC/GTIN barcode to get product "
            "name, category, and details. Use when you already have a barcode "
            "number and need to identify the product."
        ),
        "inputSchema": {
            "type": "object",
            "properties": barcode_lookup_props,
            "required": ["ean"],
            "additionalProperties": False,
        },
    })
    tools.append({
        "name": "ean_verify_checksum",
        "description": (
            "[VALIDATE] Verify the check digit of an EAN/UPC/GTIN barcode. "
            "Returns whether the barcode is valid and its type (EAN-8, UPC-A, "
            "EAN-13, GTIN-14). Local check -- no API call made."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ean": {
                    "type": "string",
                    "description": "The EAN/UPC/GTIN barcode number to validate.",
                },
            },
            "required": ["ean"],
            "additionalProperties": False,
        },
    })
    tools.append({
        "name": "ean_issuing_country",
        "description": (
            "[COUNTRY] Look up the GS1 registration country for an EAN "
            "barcode (where the barcode was registered, not necessarily "
            "where the product was manufactured). Local lookup -- no API call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ean": {
                    "type": "string",
                    "description": "The EAN barcode number to look up.",
                },
            },
            "required": ["ean"],
            "additionalProperties": False,
        },
    })

    return tools


TOOLS = _build_tools()


# =============================================================================
# MCP TOOL HANDLERS
# =============================================================================

# --- ean_search backend handlers ---

def _es_handle_product_search(args):
    name = args.get("name", "")
    language = args.get("language", ES_LANGUAGE)
    max_pages = args.get("max_pages", MAX_PAGES)
    results = _es_paginated_search(
        _es_product_search, max_pages=max_pages, name=name, language=language
    )
    return _format_results(results, query_info="product search: %s" % name)


def _es_handle_similar_search(args):
    name = args.get("name", "")
    language = args.get("language", ES_LANGUAGE)
    max_pages = args.get("max_pages", MAX_PAGES)
    results = _es_paginated_search(
        _es_similar_product_search, max_pages=max_pages, name=name, language=language
    )
    return _format_results(results, query_info="similar product search: %s" % name)


def _es_handle_category_search(args):
    category = args.get("category", "")
    name = args.get("name", "")
    max_pages = args.get("max_pages", MAX_PAGES)
    results = _es_paginated_search(
        _es_category_search, max_pages=max_pages, category=category, name=name
    )
    return _format_results(
        results,
        query_info="category search: category=%s, name=%s" % (category, name),
    )


def _es_handle_barcode_lookup(args):
    ean = args.get("ean", "")
    language = args.get("language", ES_LANGUAGE)
    return _es_barcode_lookup(ean, language=language)


def _es_handle_prefix_search(args):
    prefix = args.get("prefix", "")
    result = _es_barcode_prefix_search(prefix)
    return _format_results(result, query_info="prefix search: %s" % prefix)


# --- upcitemdb backend handlers ---

def _upc_handle_product_search(args):
    name = args.get("name", "")
    brand = args.get("brand")
    max_pages = args.get("max_pages", MAX_PAGES)
    results = _upc_paginated_search(name, brand=brand, max_pages=max_pages)
    qi = "product search: %s" % name
    if brand:
        qi += " (brand: %s)" % brand
    return _format_results(results, query_info=qi)


def _upc_handle_category_search(args):
    name = args.get("name", "")
    category = args.get("category", "")
    brand = args.get("brand")
    max_pages = args.get("max_pages", MAX_PAGES)
    results = _upc_paginated_search(
        name, brand=brand, category=category, max_pages=max_pages
    )
    qi = "category search: %s, category=%s" % (name, category)
    if brand:
        qi += ", brand=%s" % brand
    return _format_results(results, query_info=qi)


def _upc_handle_barcode_lookup(args):
    ean = args.get("ean", "")
    result = _upc_lookup(ean)
    if isinstance(result, dict) and "error" in result:
        return result
    items = result.get("items", [])
    if items:
        return {
            "code": result.get("code"),
            "total": result.get("total"),
            "products": [_upc_trim_item(item) for item in items],
        }
    return {
        "code": result.get("code"),
        "total": 0,
        "products": [],
        "hint": "No product found for this barcode.",
    }


# --- shared local handlers ---

def _handle_verify_checksum(args):
    return _validate_ean(args.get("ean", ""))


def _handle_issuing_country(args):
    return _local_issuing_country(args.get("ean", ""))


# --- dispatcher ---

def _build_handlers():
    """Build handler map based on active backend."""
    handlers = {}
    if BACKEND == "ean_search":
        handlers["ean_product_search"] = _es_handle_product_search
        handlers["ean_similar_product_search"] = _es_handle_similar_search
        handlers["ean_category_search"] = _es_handle_category_search
        handlers["ean_barcode_lookup"] = _es_handle_barcode_lookup
        handlers["ean_barcode_prefix_search"] = _es_handle_prefix_search
    else:
        handlers["ean_product_search"] = _upc_handle_product_search
        handlers["ean_category_search"] = _upc_handle_category_search
        handlers["ean_barcode_lookup"] = _upc_handle_barcode_lookup

    handlers["ean_verify_checksum"] = _handle_verify_checksum
    handlers["ean_issuing_country"] = _handle_issuing_country
    return handlers


_TOOL_HANDLERS = _build_handlers()


# =============================================================================
# MCP PROTOCOL HANDLERS
# =============================================================================
def _init(rid, _p):
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "ean-search-mcp", "version": "1.0.0"},
            "capabilities": {"tools": {}},
        },
    }


def _list(rid, _p):
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "result": {
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"],
                }
                for t in TOOLS
            ]
        },
    }


def _call_tool(rid, p):
    name = p.get("name", "")
    args = dict(p.get("arguments", {}))
    handler = _TOOL_HANDLERS.get(name)

    if not handler:
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32602, "message": "Unknown tool: %s" % name},
        }

    try:
        result = handler(args)

        if isinstance(result, dict) and "error" in result:
            hint = result.get("hint", "")
            if not hint:
                err_msg = str(result.get("error", ""))
                if BACKEND == "ean_search":
                    if "quota" in err_msg.lower() or "limit" in err_msg.lower():
                        hint = "API quota may be exceeded. Try again later."
                    elif "token" in err_msg.lower() or "auth" in err_msg.lower():
                        hint = "API token may be invalid. Check EAN_SEARCH_API_TOKEN."
                    else:
                        hint = "Check search parameters and try more specific terms."
                else:
                    if "rate" in err_msg.lower() or "limit" in err_msg.lower():
                        hint = "Daily API quota (100 requests) may be exceeded."
                    else:
                        hint = "Check search parameters and try more specific terms."
                result["hint"] = hint

            text = _cap(result)
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": True,
                },
            }

        text = _cap(result)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    except Exception as e:
        log.exception("Tool %s failed", name)
        err = {"error": str(e), "hint": "Unexpected error. Check logs."}
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(err, separators=(",", ":"))}
                ],
                "isError": True,
            },
        }


_H = {
    "initialize": _init,
    "tools/list": _list,
    "tools/call": _call_tool,
    "mcp.list_tools": _list,
    "mcp.call_tool": _call_tool,
}


# =============================================================================
# MAIN
# =============================================================================
def main():
    if BACKEND == "ean_search" and not ES_API_TOKEN:
        log.error(
            "EAN_SEARCH_API_TOKEN not set. "
            "Get a token from https://www.ean-search.org/ean-database-api.html"
        )
        sys.exit(1)

    db_label = "ean-search.org" if BACKEND == "ean_search" else "UPCitemdb"
    log.info(
        "EAN Search MCP v1.0.0 ready: backend=%s (%s), %d tools, max_pages=%d",
        BACKEND, db_label, len(TOOLS), MAX_PAGES,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        if rid is None:
            continue
        h = _H.get(req.get("method", ""))
        if h:
            r = h(rid, req.get("params", {}))
        else:
            r = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "Method not found"},
            }
        sys.stdout.write(json.dumps(r, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
