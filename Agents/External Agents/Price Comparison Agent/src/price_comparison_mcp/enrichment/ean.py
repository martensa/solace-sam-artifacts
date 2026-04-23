"""Pure-function EAN / GTIN / ISBN validation and classification.

State-of-the-art references:
  - GS1 General Specifications chapter 3.8 (GTIN checksum)
  - ISO 2108 (ISBN-10 mod 11; ISBN-13 is a GTIN-13)
  - GS1 prefix-to-country allocation table

All functions are synchronous and allocation-light -- suitable for the
hot path at query ingress (fast-fail on invalid barcodes saves 15-75s
of Playwright work downstream).

Public API:
  normalize_ean(raw)        -> canonical digit string or None
  detect_code_type(s)       -> Literal["gtin8","upc12","ean13","gtin14",
                                        "isbn10","isbn13","unknown"]
  validate_gtin(s)          -> bool (8/12/13/14 digits)
  validate_isbn10(s)        -> bool (accepts trailing 'X' check char)
  validate_code(s)          -> bool (dispatches on length, handles ISBN-10)
  ean_prefix_country(s)     -> ISO-3166-1 alpha-2 country or None
  ean_prefix_kind(s)        -> descriptive label ("book", "refund-coupon", ...)
  isbn10_to_isbn13(s)       -> 13-digit GTIN-13 (978 prefix) or None

Design notes:
  - normalize_ean() strips whitespace, dashes, and ISBN "ISBN:" labels so
    callers can feed raw user input.
  - ISBN-10's "X" check digit is handled: validator accepts both digit
    and 'X' in position 10.
  - ean_prefix_country returns the *allocation* country, not the
    manufacturing country. EAN numbering is global, not nation-bound;
    the prefix only signals where the manufacturer joined GS1.
"""
from __future__ import annotations

import re
from typing import Literal


CodeType = Literal["gtin8", "upc12", "ean13", "gtin14", "isbn10", "isbn13", "unknown"]


# -----------------------------------------------------------------------------
# Normalisation
# -----------------------------------------------------------------------------

# Strip leading "ISBN", "ISBN-10", "ISBN-13", "ISBN:", "ISBN 13 " markers
# before the digit extraction. The optional "1?[03]" branch covers the
# -10 and -13 suffixes that callers often paste alongside.
_ISBN_PREFIX = re.compile(
    r"^\s*(?:ISBN(?:\s*-?\s*1[03])?\s*[:\s-]*)+",
    re.IGNORECASE,
)
_NON_DIGIT_EXCEPT_X = re.compile(r"[^0-9X]")


def normalize_ean(raw: str | None) -> str | None:
    """Strip whitespace, dashes, and 'ISBN' prefixes.

    Returns the canonical digit (or digit+X) string, or None if nothing
    parsable remains. Upper-cases a trailing 'x' to 'X' for ISBN-10.
    Rejects any input that produces no digits at all (safeguards against
    random text that happens to contain an 'x').

    Examples:
      "  978-3-16-148410-0 "       -> "9783161484100"
      "ISBN: 0-306-40615-2"        -> "0306406152"
      "4012195887515"              -> "4012195887515"
      "ISBN-13 9783161484100"      -> "9783161484100"
      "not-a-barcode"              -> None
    """
    if raw is None:
        return None
    s = _ISBN_PREFIX.sub("", raw).strip().upper()
    s = _NON_DIGIT_EXCEPT_X.sub("", s)
    if not s:
        return None
    # Must contain at least one digit -- a pure "X" artifact from random
    # text ("tExt") is not a barcode.
    if not any(c.isdigit() for c in s):
        return None
    # 'X' may only appear in the last slot (ISBN-10 check digit)
    if "X" in s[:-1]:
        return None
    return s


# -----------------------------------------------------------------------------
# Type detection
# -----------------------------------------------------------------------------


def detect_code_type(s: str | None) -> CodeType:
    """Classify a (normalised or raw) string by structural rules.

    Uses only length + Bookland/ISBN prefix heuristics. Does NOT run the
    checksum -- that's validate_*'s job. Callers typically do
    detect_code_type -> validate_code to guard the search pipeline.
    """
    n = normalize_ean(s)
    if not n:
        return "unknown"
    L = len(n)
    if L == 8:
        return "gtin8"
    if L == 10:
        # ISBN-10 only (valid check digit may be 'X')
        return "isbn10"
    if L == 12:
        return "upc12"
    if L == 13:
        # ISBN-13 (Bookland) or plain EAN-13
        if n.startswith(("978", "979")):
            return "isbn13"
        return "ean13"
    if L == 14:
        return "gtin14"
    return "unknown"


# -----------------------------------------------------------------------------
# Checksums
# -----------------------------------------------------------------------------


def _gtin_checksum_ok(digits: str) -> bool:
    """GS1 modulo-10 check for GTIN-8, UPC-A, EAN-13, GTIN-14.

    Algorithm (right-to-left, check digit is rightmost):
      - odd positions (1st from right): multiplied by 3
      - even positions: multiplied by 1
      - sum all, add check digit, must be divisible by 10
    """
    if not digits.isdigit():
        return False
    # Walk from right to left, not counting the check digit itself.
    total = 0
    body = digits[:-1]
    check = int(digits[-1])
    # Position 1 = rightmost body digit -> multiplier 3
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == check


def validate_gtin(s: str | None) -> bool:
    """Return True if s is a valid GTIN-8 / UPC-A / EAN-13 / GTIN-14.

    Accepts raw (unnormalised) input. Rejects ISBN-10 ('X' char) outright
    -- callers should use validate_code() for dispatch instead.
    """
    n = normalize_ean(s)
    if not n or "X" in n:
        return False
    if len(n) not in (8, 12, 13, 14):
        return False
    return _gtin_checksum_ok(n)


def validate_isbn10(s: str | None) -> bool:
    """Modulo-11 ISBN-10 check. Accepts 'X' as the final check character.

    Algorithm:
      - position 1 (leftmost) * 10, position 2 * 9, ..., position 10 * 1
      - sum must be divisible by 11
      - final digit may be 'X' (value 10)
    """
    n = normalize_ean(s)
    if not n or len(n) != 10:
        return False
    total = 0
    for i, ch in enumerate(n):
        if i < 9:
            if not ch.isdigit():
                return False
            total += int(ch) * (10 - i)
        else:
            # Last char: digit or 'X'
            if ch == "X":
                total += 10
            elif ch.isdigit():
                total += int(ch)
            else:
                return False
    return total % 11 == 0


def validate_code(s: str | None) -> bool:
    """Validate any of GTIN-8/UPC/EAN-13/GTIN-14/ISBN-10/ISBN-13.

    One-shot dispatcher: callers at the pipeline entry use this to
    early-reject invalid barcodes without caring which length/type.
    """
    kind = detect_code_type(s)
    if kind == "unknown":
        return False
    if kind == "isbn10":
        return validate_isbn10(s)
    return validate_gtin(s)  # covers gtin8/upc12/ean13/isbn13/gtin14


# -----------------------------------------------------------------------------
# ISBN-10 -> ISBN-13 upconversion
# -----------------------------------------------------------------------------


def isbn10_to_isbn13(s: str | None) -> str | None:
    """Convert a valid ISBN-10 to its canonical ISBN-13 form (978 prefix).

    Useful for query normalisation: many retailer URLs contain ISBN-13,
    and a single canonical representation improves URL matching. Returns
    None if the input is not a valid ISBN-10.
    """
    n = normalize_ean(s)
    if not n or not validate_isbn10(n):
        return None
    body = "978" + n[:-1]
    # Recompute the GS1 checksum
    total = sum(
        int(ch) * (3 if i % 2 == 1 else 1)
        for i, ch in enumerate(body)
    )
    check = (10 - (total % 10)) % 10
    return body + str(check)


# -----------------------------------------------------------------------------
# GS1 prefix lookup
# -----------------------------------------------------------------------------

# Condensed GS1 country/territory allocation. Only the ranges we actually
# care about for B2B / European retail are enumerated; the rest resolve
# to None and callers continue without the hint.
#
# Source: https://www.gs1.org/standards/id-keys/company-prefix
# (abridged for brevity; extend as new prefixes become relevant)
_GS1_RANGES: list[tuple[int, int, str]] = [
    # (lo_inclusive, hi_inclusive, ISO-3166-1 alpha-2 or descriptive marker)
    (0,   13,  "US"),   # UPC-A zone (North America, GS1 US)
    (20,  29,  "IN"),   # "in-store" restricted (book coupons, weighed goods)
    (30,  39,  "US"),   # Drug-related (DSN, GS1 US)
    (40,  49,  "IN"),   # Restricted distribution (manufacturer-internal)
    (50,  59,  "US"),   # Coupons
    (60,  99,  "US"),
    (100, 139, "US"),
    (200, 299, "IN"),   # Restricted distribution (regional)
    (300, 379, "FR"),
    (380, 380, "BG"),
    (383, 383, "SI"),
    (385, 385, "HR"),
    (387, 387, "BA"),
    (389, 389, "ME"),
    (390, 390, "XK"),
    (400, 440, "DE"),
    (450, 459, "JP"),
    (460, 469, "RU"),
    (470, 470, "KG"),
    (471, 471, "TW"),
    (474, 474, "EE"),
    (475, 475, "LV"),
    (476, 476, "AZ"),
    (477, 477, "LT"),
    (478, 478, "UZ"),
    (479, 479, "LK"),
    (480, 480, "PH"),
    (481, 481, "BY"),
    (482, 482, "UA"),
    (483, 483, "TM"),
    (484, 484, "MD"),
    (485, 485, "AM"),
    (486, 486, "GE"),
    (487, 487, "KZ"),
    (488, 488, "TJ"),
    (489, 489, "HK"),
    (490, 499, "JP"),
    (500, 509, "GB"),
    (520, 521, "GR"),
    (528, 528, "LB"),
    (529, 529, "CY"),
    (530, 530, "AL"),
    (531, 531, "MK"),
    (535, 535, "MT"),
    (539, 539, "IE"),
    (540, 549, "BE"),  # + LU
    (560, 560, "PT"),
    (569, 569, "IS"),
    (570, 579, "DK"),  # + FO + GL
    (590, 590, "PL"),
    (594, 594, "RO"),
    (599, 599, "HU"),
    (600, 601, "ZA"),
    (603, 603, "GH"),
    (604, 604, "SN"),
    (608, 608, "BH"),
    (609, 609, "MU"),
    (611, 611, "MA"),
    (613, 613, "DZ"),
    (615, 615, "NG"),
    (616, 616, "KE"),
    (618, 618, "CI"),
    (619, 619, "TN"),
    (620, 620, "TZ"),
    (621, 621, "SY"),
    (622, 622, "EG"),
    (624, 624, "LY"),
    (625, 625, "JO"),
    (626, 626, "IR"),
    (627, 627, "KW"),
    (628, 628, "SA"),
    (629, 629, "AE"),
    (640, 649, "FI"),
    (690, 699, "CN"),
    (700, 709, "NO"),
    (729, 729, "IL"),
    (730, 739, "SE"),
    (740, 740, "GT"),
    (741, 741, "SV"),
    (742, 742, "HN"),
    (743, 743, "NI"),
    (744, 744, "CR"),
    (745, 745, "PA"),
    (746, 746, "DO"),
    (750, 750, "MX"),
    (754, 755, "CA"),
    (759, 759, "VE"),
    (760, 769, "CH"),
    (770, 771, "CO"),
    (773, 773, "UY"),
    (775, 775, "PE"),
    (777, 777, "BO"),
    (778, 779, "AR"),
    (780, 780, "CL"),
    (784, 784, "PY"),
    (786, 786, "EC"),
    (789, 790, "BR"),
    (800, 839, "IT"),
    (840, 849, "ES"),
    (850, 850, "CU"),
    (858, 858, "SK"),
    (859, 859, "CZ"),
    (860, 860, "RS"),
    (865, 865, "MN"),
    (867, 867, "KP"),
    (868, 869, "TR"),
    (870, 879, "NL"),
    (880, 880, "KR"),
    (884, 884, "KH"),
    (885, 885, "TH"),
    (888, 888, "SG"),
    (890, 890, "IN"),
    (893, 893, "VN"),
    (896, 896, "PK"),
    (899, 899, "ID"),
    (900, 919, "AT"),
    (930, 939, "AU"),
    (940, 949, "NZ"),
    (950, 950, "BOOKLAND"),     # GS1 Global Office
    (955, 955, "MY"),
    (958, 958, "MO"),
    (977, 977, "ISSN"),         # Periodicals
    (978, 979, "ISBN"),         # Bookland
    (980, 980, "REFUND"),       # Refund receipts
    (981, 984, "COUPON"),       # Common currency coupons
    (990, 999, "COUPON"),
]


def _prefix_int(ean: str) -> int | None:
    """Return the first 3 digits as int, or None if not 13+ digits."""
    if not ean or len(ean) < 3 or not ean[:3].isdigit():
        return None
    return int(ean[:3])


def ean_prefix_country(s: str | None) -> str | None:
    """Return GS1 allocation country (ISO-3166-1 alpha-2) for an EAN-13/14.

    Note: returns the *allocation* country, not the origin of manufacture.
    Returns None for descriptive markers (BOOKLAND/ISBN/COUPON/IN/REFUND/ISSN).
    """
    n = normalize_ean(s)
    pfx = _prefix_int(n) if n else None
    if pfx is None:
        return None
    for lo, hi, code in _GS1_RANGES:
        if lo <= pfx <= hi:
            return code if len(code) == 2 else None
    return None


def ean_prefix_kind(s: str | None) -> str | None:
    """Return a descriptive label when the prefix marks a special range.

    Values: "isbn", "issn", "coupon", "refund", "in-store", or None for
    regular product EANs. The category classifier uses this as a strong
    Stage-1 hint (e.g. "issn" -> book_media).
    """
    n = normalize_ean(s)
    pfx = _prefix_int(n) if n else None
    if pfx is None:
        return None
    for lo, hi, code in _GS1_RANGES:
        if lo <= pfx <= hi:
            up = code.upper()
            if up == "ISBN" or up == "BOOKLAND":
                return "isbn"
            if up == "ISSN":
                return "issn"
            if up == "COUPON":
                return "coupon"
            if up == "REFUND":
                return "refund"
            if up == "IN":
                return "in-store"
            return None
    return None
