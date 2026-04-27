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
# Manufacturer part-number gate (Phase I)
# -----------------------------------------------------------------------------
#
# Industrial / electrical / sanitary queries usually carry a manufacturer
# SKU (KSA-S40, ASM-C6A, MEG6921-0001, 5SV1316-6KK16, ...). Three failure
# modes were observed in Testlauf 3:
#
#   Pos 3   query "OBO Bettermann ASM-C6A G ..."   hit "DTS-2C-RW1"
#   Pos 4   query "OBO Bettermann KSA-S40 ..."     hit "1594-22-G"
#   Pos 14  query "MEPA ellipse ..."               hit "MEPAorbit"
#
# In all three the brand was correct but the model token was different.
# The base digit-anchor logic (>=3 consecutive digits) misses these:
# KSA-S40 has only "40", ASM-C6A has only "6", and "ellipse" carries no
# digits at all. We need a TOKEN-level gate that tests whether the query
# committed to a specific part number / model line and the candidate
# carries it.
#
# The detector is intentionally conservative: it ONLY fires when at least
# one part-number-shaped token is present in the query AND zero such
# tokens appear in the candidate haystack. Queries without a clear SKU
# (descriptive product names like "Sony WH-1000XM5 Bluetooth Kopfhoerer"
# vs the generic "Bluetooth Kopfhoerer") get either a strong match or
# a quiet 0 -- never a noisy false positive.
#
# Public for tests: _extract_part_number_tokens, _normalize_for_match.

# Tokens that look like part numbers: alphanumeric mix with optional
# internal hyphens / dots / slashes / underscores, length >= 4.
# After matching we additionally require BOTH a letter and a digit
# (drops pure model-line names like "ellipse" -- those need a different
# axis -- and pure product descriptors like "Akku-Schrauber").
_PART_NUMBER_RE = re.compile(
    r"\b[A-Za-z0-9]+(?:[-/.\\_][A-Za-z0-9]+)*\b"
)

# Reject tokens that are clearly NOT part numbers.
#  - Pure 4-digit year (1900-2099) -- handled by wine_vintage_detector
#  - Pure decimals like "5.0" or "10.5"
#  - Common technical fillers handled by Rule C (CAT 6A, IP65, RJ45) --
#    those re-appear in the haystack so the detector wouldn't flag,
#    but listing them keeps the token set semantically clean.
_PART_NUMBER_REJECT_RE = re.compile(
    r"^(?:"
    r"(?:19|20)\d{2}"            # 1950-2099 years
    r"|\d+[.,]\d+"                # decimals 5.0, 10,5
    r"|cat-?\d{1,2}[a-z]?"        # CAT 6A, CAT-7
    r"|ip-?\d{2,3}"               # IP65, IP-67
    r"|rj-?\d{1,3}"               # RJ45, RJ-11
    r")$",
    re.IGNORECASE,
)

# Stripped during normalization so "KSA-S40" and "KSA S40" and "KSAS40"
# all reduce to the same comparable form. Backslash + underscore are
# included for catalog entries that use them.
_PART_NUMBER_PUNCT_RE = re.compile(r"[\s\-/.\\_]+")

# Penalty magnitude. Higher than fashion (25) because a SKU mismatch is
# a stronger negative signal in B2B than a size/color difference.
PART_NUMBER_PENALTY = 35

# Minimum normalized-token length for a credible match. Short fragments
# like "S40" alone could substring-match unrelated SKUs ("S400", "BS40").
# Tokens shorter than this are still EXTRACTED (so detector sees the
# query has a SKU expectation) but matched only as a whole-token via
# the punctuation-tolerant rule.
_MIN_NORMALIZED_LEN = 4


def _normalize_for_match(text: str) -> str:
    """Lowercase + strip part-number punctuation. Empty in -> empty out."""
    if not text:
        return ""
    return _PART_NUMBER_PUNCT_RE.sub("", text).lower()


def _extract_part_number_tokens(query: str) -> list[str]:
    """Return part-number-like tokens from `query`.

    Each token contains at least one letter AND at least one digit,
    has length >= 4, and is not a year / decimal / CAT-class filler.
    Order is preserved (callers may use the FIRST token as the most
    decisive identifier when multiple are present).
    """
    if not query:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _PART_NUMBER_RE.finditer(query):
        tok = m.group(0)
        if len(tok) < 4:
            continue
        if not (any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok)):
            continue
        if _PART_NUMBER_REJECT_RE.match(tok):
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def _haystack_contains_token(haystack_norm: str, token: str) -> bool:
    """True when `token` (in any punctuation flavour) is in `haystack_norm`.

    `haystack_norm` is already normalized via _normalize_for_match.
    The token itself is normalized inline. Tokens whose normalized form
    is shorter than _MIN_NORMALIZED_LEN are matched as whole-words only
    against the original (non-normalized) haystack -- avoids "S40" --> "S400"
    false matches.
    """
    norm = _normalize_for_match(token)
    if not norm:
        return False
    if len(norm) >= _MIN_NORMALIZED_LEN:
        return norm in haystack_norm
    # Short tokens: only credit a match when normalization preserves a
    # word boundary (caller passes original haystack as second positional
    # to enable this, but we don't here -- so short tokens never match).
    return False


def manufacturer_part_number_detector(
    query: str, url: str, context: str = ""
) -> int:
    """Penalty when the query carries a part-number-shaped token that is
    absent from URL path AND context.

    Activated for industrial / sanitary / chemicals categories where the
    typical query style is `Brand Part-Number Description` and a wrong
    SKU is much costlier than missing a marginal hit.
    """
    tokens = _extract_part_number_tokens(query)
    if not tokens:
        return 0
    try:
        path = urlparse(url).path
    except Exception:
        return 0
    haystack_raw = (path or "") + " " + (context or "")
    if not haystack_raw.strip():
        return 0
    haystack_norm = _normalize_for_match(haystack_raw)
    for tok in tokens:
        if _haystack_contains_token(haystack_norm, tok):
            return 0
    return PART_NUMBER_PENALTY


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
    "manufacturer_part_number": manufacturer_part_number_detector,
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
