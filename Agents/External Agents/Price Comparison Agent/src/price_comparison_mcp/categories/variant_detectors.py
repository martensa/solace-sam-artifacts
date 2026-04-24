"""Category-specific variant-mismatch detectors.

The base `_foreign_model_qualifier_penalty` covers Rules A/B/C that work
across all product domains (letter-digit compounds, short compounds near
query words, alpha variant suffixes near digit anchors). Those rules
catch "GBH 18V-26 F" vs "GBH 2-26 F" or "FTT" vs "SCH".

Some product domains have their OWN variant axes that the base rules
don't see:

  - Fashion   -- size (S/M/L/XL/42/44/46) and color mismatches
  - Wine      -- vintage year (2015 vs 2018)
  - Book      -- ISBN must match exactly (edition/format variants)
  - Automotive-- vehicle generation codes (E46 vs E90, W204 vs W205)

Each detector is a `VariantDetector` callable returning a penalty (int).
Zero means "no mismatch detected for this axis". Non-zero means "URL
describes a DIFFERENT variant than the query" -> apply penalty.

Detectors are activated per-category via `CategoryProfile`. Default
profile has none; `fashion_apparel` gets size+color; `food_beverage`
gets vintage; `book_media` gets ISBN; `automotive` gets OEM.

The penalty is combined with the base foreign-qualifier penalty in
_score_url and `_resolve_aggregator_search_to_product`.
"""
from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlparse


# Signature: (query, url, context) -> penalty magnitude (0 = quiet).
# `context` is an optional extra string (page title, SearXNG snippet,
# breadcrumb trail, ...) that some detectors scan alongside the URL
# path -- Zalando/aboutyou don't encode size/color in the URL, so
# relying only on the path misses real mismatches.
VariantDetector = Callable[..., int]


# -----------------------------------------------------------------------------
# Fashion: size + color disambiguation
# -----------------------------------------------------------------------------

# Numeric sizes (EU shoe/pants sizes)
_NUMERIC_SIZE = re.compile(r"\b(\d{2}(?:[./]\d)?)\b")     # 42, 44, 42.5, 42/43
# Letter sizes with optional X-prefix
_LETTER_SIZE = re.compile(r"\b(XXS|XS|S|M|L|XL|XXL|XXXL|3XL|4XL)\b", re.IGNORECASE)

# Color vocabulary (multi-language). Missing combinations default to no detection.
_COLOR_SET_DE = {
    "schwarz", "weiss", "weiß", "grau", "blau", "rot", "gruen", "grün",
    "gelb", "braun", "beige", "rosa", "pink", "violett", "orange",
    "silber", "gold", "marine", "navy", "tuerkis", "türkis",
}
_COLOR_SET_EN = {
    "black", "white", "grey", "gray", "blue", "red", "green", "yellow",
    "brown", "beige", "pink", "purple", "orange", "silver", "gold",
    "navy", "turquoise",
}
# Synonym groups -- if one is in query and a SYNONYM (different language)
# is in URL, they're the same color, not a mismatch.
_COLOR_SYNONYMS: list[set[str]] = [
    {"schwarz", "black", "noir"},
    {"weiss", "weiß", "white", "blanc"},
    {"grau", "gray", "grey"},
    {"blau", "blue"},
    {"rot", "red"},
    {"gruen", "grün", "green"},
    {"gelb", "yellow"},
    {"braun", "brown"},
    {"rosa", "pink"},
    {"violett", "purple"},
    {"silber", "silver"},
    {"gold"},
    {"navy", "marine"},
    {"tuerkis", "türkis", "turquoise"},
]


def _canonical_color(word: str) -> str | None:
    """Return a representative token for a color synonym group."""
    w = word.lower()
    for group in _COLOR_SYNONYMS:
        if w in group:
            return next(iter(sorted(group)))  # deterministic representative
    return None


def _extract_colors(text: str) -> set[str]:
    """Return canonical color tokens present in `text`."""
    text_low = text.lower()
    tokens = re.findall(r"[a-zäöüß]+", text_low)
    seen = set()
    for tok in tokens:
        c = _canonical_color(tok)
        if c:
            seen.add(c)
    return seen


def _extract_sizes(text: str) -> set[str]:
    """Return {size-strings} in `text` -- numeric + letter variants."""
    sizes: set[str] = set()
    for m in _NUMERIC_SIZE.finditer(text):
        s = m.group(1)
        # Heuristic filter: fashion sizes are 2-chars 30-50 or letter sizes.
        # Numeric year / product-ID collisions: reject if > 60 (not a size).
        try:
            val = float(s.replace("/", "."))
            if 30 <= val <= 60:
                sizes.add(s)
        except ValueError:
            pass
    for m in _LETTER_SIZE.finditer(text):
        sizes.add(m.group(1).upper())
    return sizes


FASHION_SIZE_PENALTY = 25
FASHION_COLOR_PENALTY = 20


def fashion_size_detector(query: str, url: str, context: str = "") -> int:
    """Penalty when query has a size and URL/context has a DIFFERENT size.

    Fashion retailers like Zalando, AboutYou and P&C often render size
    selectors via JavaScript and do NOT include the size in the URL
    path. The extra `context` parameter (page title / SearXNG snippet /
    breadcrumb) lets the detector spot mismatches that URL-only scanning
    would miss (e.g. page title "Nike Air Force 1 Triple White Size 44"
    when the query asked for 42).

    Backward-compat: existing callers that pass only (query, url) get
    the old URL-only behaviour because context defaults to "".
    """
    q_sizes = _extract_sizes(query)
    if not q_sizes:
        return 0
    try:
        path = urlparse(url).path
    except Exception:
        return 0
    # Combine URL path + context (title/breadcrumb). context may be empty.
    haystack = path + " " + (context or "")
    u_sizes = _extract_sizes(haystack)
    if not u_sizes:
        return 0
    # Only flag when haystack has a size NOT in query (a mismatch).
    if not (q_sizes & u_sizes) and u_sizes:
        return FASHION_SIZE_PENALTY
    return 0


def fashion_color_detector(query: str, url: str, context: str = "") -> int:
    """Penalty when query specifies a color and URL/context encodes another.

    Same title/breadcrumb-aware pattern as fashion_size_detector --
    many fashion URLs omit the color in the path but the page title
    carries it ("Nike AF1 Schwarz" vs query "weiss").
    """
    q_colors = _extract_colors(query)
    if not q_colors:
        return 0
    try:
        path = urlparse(url).path
    except Exception:
        return 0
    haystack = path + " " + (context or "")
    u_colors = _extract_colors(haystack)
    if not u_colors:
        return 0
    if not (q_colors & u_colors):
        return FASHION_COLOR_PENALTY
    return 0


# -----------------------------------------------------------------------------
# Wine / vintage: year mismatch
# -----------------------------------------------------------------------------

_YEAR = re.compile(r"\b(19|20)\d{2}\b")

WINE_VINTAGE_PENALTY = 25


def wine_vintage_detector(query: str, url: str) -> int:
    """Penalty when query specifies a vintage year and URL has a different one.

    Only triggers for years in the plausible wine window (1950-current+1).
    Defensive on SKU-number false positives: the URL year must appear in
    the path (not only the query string / host).
    """
    q_years = {int(m.group(0)) for m in _YEAR.finditer(query)}
    q_years = {y for y in q_years if 1950 <= y <= 2030}
    if not q_years:
        return 0
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return 0
    u_years = {int(m.group(0)) for m in _YEAR.finditer(path)}
    u_years = {y for y in u_years if 1950 <= y <= 2030}
    if not u_years:
        return 0
    if not (q_years & u_years):
        return WINE_VINTAGE_PENALTY
    return 0


# -----------------------------------------------------------------------------
# Book-edition: ISBN must match
# -----------------------------------------------------------------------------

_ISBN13_RE = re.compile(r"\b(97[89]\d{10})\b")
_ISBN10_RE = re.compile(r"\b(\d{9}[\dX])\b")

BOOK_EDITION_PENALTY = 25


def book_edition_detector(query: str, url: str) -> int:
    """Penalty when query contains an ISBN and URL does not include it.

    Canonical ISBN matching: if query has ISBN-13 or ISBN-10, the URL
    must contain that exact sequence (dashes removed) somewhere. URL
    without any ISBN passes (a bookshop link without ISBN in URL is
    normal -- cannot prove mismatch). URL with a DIFFERENT ISBN triggers.
    """
    def _extract_isbn(s: str) -> set[str]:
        stripped = re.sub(r"[^0-9X]", "", s.upper())
        found: set[str] = set()
        for m in _ISBN13_RE.finditer(stripped):
            found.add(m.group(1))
        for m in _ISBN10_RE.finditer(stripped):
            v = m.group(1)
            # skip if it's part of an already-found ISBN-13
            if not any(v in isbn13 for isbn13 in found):
                found.add(v)
        return found

    q_isbns = _extract_isbn(query)
    if not q_isbns:
        return 0
    u_isbns = _extract_isbn(url)
    if not u_isbns:
        # URL carries no ISBN at all: can't prove mismatch -- return 0
        return 0
    if not (q_isbns & u_isbns):
        return BOOK_EDITION_PENALTY
    return 0


# -----------------------------------------------------------------------------
# Automotive: vehicle-generation code disambiguation
# -----------------------------------------------------------------------------

# BMW E-Codes and F-Codes: E30, E36, E46, E90, E91, F30, G20, ...
# Mercedes W-Codes: W201, W202, W204, W205, W206, ...
# Audi 8L, 8P, 8V, 8Y
# VW 1J, 1K, 5G, 3C
# Each is a 2-3 char letter-digit token.
_VEHICLE_CODE = re.compile(
    r"\b([EFGWRS]\d{2,3}|[0-9][A-Z]|[A-Z]\d[A-Z])\b"
)

AUTOMOTIVE_OEM_PENALTY = 25


def automotive_oem_detector(query: str, url: str) -> int:
    """Penalty when query has a vehicle code and URL shows a different one.

    Only triggers if BOTH query and URL contain a code pattern AND they
    differ. Empty URL codes pass (no evidence of mismatch).
    """
    q_codes = {m.group(1).upper() for m in _VEHICLE_CODE.finditer(query.upper())}
    if not q_codes:
        return 0
    try:
        path = urlparse(url).path.upper()
    except Exception:
        return 0
    u_codes = {m.group(1).upper() for m in _VEHICLE_CODE.finditer(path)}
    if not u_codes:
        return 0
    if not (q_codes & u_codes):
        return AUTOMOTIVE_OEM_PENALTY
    return 0


# -----------------------------------------------------------------------------
# Detector registry -- categories name their detectors in YAML.
# Unknown names are silently dropped (forward-compat for YAML-declared
# detectors that a given binary doesn't ship yet).
# -----------------------------------------------------------------------------

DETECTOR_REGISTRY: dict[str, VariantDetector] = {
    "fashion_size": fashion_size_detector,
    "fashion_color": fashion_color_detector,
    "wine_vintage": wine_vintage_detector,
    "book_edition": book_edition_detector,
    "automotive_oem": automotive_oem_detector,
}


def run_category_detectors(
    query: str,
    url: str,
    detector_names: tuple[str, ...] = (),
    context: str = "",
) -> int:
    """Dispatch each named detector, return the FIRST non-zero penalty.

    Short-circuits on first match -- stacking multiple penalties would
    distort the score. The detectors are ordered in the profile by
    specificity (most decisive first).

    `context` is passed through to every detector that accepts it
    (currently fashion_size / fashion_color). Title/breadcrumb text
    that the caller has access to (SearXNG snippet, page title after
    fetch) dramatically improves accuracy for retailers that don't
    encode the variant in the URL path.
    """
    for name in detector_names:
        det = DETECTOR_REGISTRY.get(name)
        if det is None:
            continue
        # Try context-aware signature first, fall back to legacy.
        try:
            pen = det(query, url, context)
        except TypeError:
            pen = det(query, url)
        if pen:
            return pen
    return 0
