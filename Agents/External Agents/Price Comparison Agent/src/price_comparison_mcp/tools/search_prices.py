"""Main search pipeline: SearXNG + optional SerpAPI + Playwright detail fetch."""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
import time
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import quote_plus, urlparse

if TYPE_CHECKING:  # pragma: no cover
    # Forward reference for v1.0 category profile -- avoids runtime import cost
    # and keeps the zero-regression guarantee for profile=None callers.
    from ..categories.models import CategoryProfile

from ..browser_manager import BrowserManager, simulate_human_mouse, simulate_human_scroll
from ..cache import TTLCache
from ..config import PriceSearchConfig
from ..errors import ErrorCode, tool_error
from ..price_extractor import (
    ExtractedOffer,
    extract_ean,
    extract_page_signals,
    extract_prices,
    parse_price,
)
from ..searxng_client import SearXNGClient, SearchResult
# Additional search backends (all optional, empty results when unconfigured)
try:
    from ..brave_client import BraveSearchClient
except Exception:  # pragma: no cover
    BraveSearchClient = None  # type: ignore
try:
    from ..serper_client import SerperClient
except Exception:  # pragma: no cover
    SerperClient = None  # type: ignore
try:
    from ..apify_client import ApifyGoogleShoppingClient
except Exception:  # pragma: no cover
    ApifyGoogleShoppingClient = None  # type: ignore

logger = logging.getLogger("price-comparison-mcp.search")

# URL scoring for prioritization. Grouped by tier so adding a new domain
# is a one-line operation. Scores influence only fetch order within the
# same tier; domain DIVERSITY during fetch is guaranteed separately via
# _select_diverse_urls().
#
# Primary use case of this agent is B2B procurement. B2B distributors
# are scored at peer level with consumer aggregators so they actually
# reach the Playwright stage.
_PRICE_SITE_SCORES: dict[str, int] = {
    # --- Tier 1: consumer price aggregators (dense offer coverage) ---
    "idealo.de": 100,
    "geizhals.de": 95,
    "geizhals.at": 95,
    "billiger.de": 90,
    "guenstiger.de": 85,
    "preis.de": 85,
    "preisvergleich.de": 80,

    # --- Tier 2: major German electrical wholesalers (CRITICAL B2B) ---
    # Sonepar and Rexel are the two dominant German electrical
    # wholesalers and often the authoritative source for brands like
    # Niedax, OBO Bettermann, Hager, Siemens, ABB.
    "sonepar.de": 92,
    "rexel.de": 90,
    "fega.de": 82,
    "eibmarkt.com": 80,  # specialised KNX / building-tech wholesaler

    # --- Tier 2: industrial electronics & components distributors ---
    "conrad.de": 85,
    "reichelt.de": 85,
    "voelkner.de": 82,
    "rs-online.com": 85,     # RS Components -- EU-wide B2B
    "rs-components.com": 85,
    "farnell.com": 82,        # Newark / Farnell
    "de.farnell.com": 82,
    "digikey.de": 82,
    "mouser.de": 82,
    "distrelec.de": 82,
    "distrelec.biz": 82,
    "buerklin.com": 80,
    "buerklin.de": 80,
    "rutronik24.com": 78,
    "tme.eu": 78,
    "elv.com": 75,
    "elv.de": 75,

    # --- Tier 2: B2B industrial / MRO distributors ---
    "industry-electronics.de": 82,
    "voltus.de": 80,
    "elektro4000.de": 80,
    "mercateo.com": 80,        # major B2B procurement marketplace
    "contorion.de": 78,
    "kaiser-kraft.de": 78,
    "schaefer-shop.de": 76,
    "schaefer-shop.com": 76,
    "svh24.de": 76,
    "hoffmann-group.com": 82,  # Hoffmann is major tooling supplier
    "hoffmann-group.de": 82,
    "wuerth.com": 80,
    "wuerth-industrie.de": 80,
    "haberkorn.com": 76,
    "expondo.de": 72,
    "manomano.de": 68,
    "toolineo.de": 70,
    "berner-group.com": 74,
    "berner.de": 74,
    # Long-tail German B2B distributors. Added after empirical smoke
    # tests repeatedly surfaced these domains with correct visible
    # prices while our ranker was dropping them for having the default
    # unknown-domain score of 10.
    "unielektro.de": 78,               # major electrical wholesaler
    "tandmore.de": 68,                 # B2B electrical + building tech
    "schmitztools.com": 62,            # B2B tooling
    "meisterfokus.com": 60,            # B2B industrial
    "schweisstechniktotal.com": 58,    # welding & industrial tools
    "buero-bedarf-thueringen.de": 55,  # office + technical supplies
    "elektroland24.de": 70,
    "elektroshopwagner.de": 68,
    "elektroversand-schmidt.de": 65,
    "elektrogrosshandel-online.de": 65,
    "klingel-elektronik.de": 62,
    "obeta.de": 70,                    # OBETA -- major electrical wholesale
    "fegime.de": 72,
    "pkelektronik.com": 60,
    "trotec24.com": 66,
    "trotec.com": 66,
    "stego.de": 58,
    # Added after FLUKE 1674FC SCH smoke test (Apr 2026) found these
    # merchants were listing real prices but scored at the default
    # unknown-domain level of 10, so they never reached the fetch
    # stage. All directly observed in idealo's shop-logo enumeration
    # for this product.
    "galaxus.de": 72,                   # Swiss/DE major retailer, pan-industry
    "direkt.jacob.de": 78,              # Jacob Elektronik direkt (B2B)
    "bueromarkt-ag.de": 65,             # B2B office + technical
    "messgeraete-chemnitz.de": 70,      # B2B measurement-instrument specialist
    "volterix.de": 62,                  # B2B electrical appliances
    "smartgoods.de": 55,                # multi-sector online retailer
    "evolt24.de": 55,                   # electrical supplies
    "etoh24.de": 55,
    "elektrotresen.de": 55,
    "mytvshop.de": 55,
    "alles-mit-stecker.de": 55,
    "e-tec.at": 60,                     # AT consumer/B2B retailer
    "watt24.com": 60,                   # Electrical
    # Additional electrical distributors discovered on geizhals and
    # idealo listings.
    "hornbach.at": 60,
    "kaeufer.de": 50,
    "computerunie.de": 50,
    "pearl.de": 55,
    "mercateo.de": 80,                  # alternate TLD for mercateo

    # --- Tier 3: B2B IT / computing resellers ---
    "bechtle.com": 82,         # top German B2B IT
    "jacob.de": 80,
    "cyberport.de": 72,
    "future-x.de": 72,
    "computeruniverse.net": 68,

    # --- Tier 3: workwear / PPE / safety (common procurement) ---
    "engelbert-strauss.com": 78,
    "engelbert-strauss.de": 78,
    "mewa.de": 70,
    "arbeitsschutz-express.de": 70,

    # --- Tier 3: major consumer retailers (often still serve B2B SKUs) ---
    "amazon.de": 70,
    "otto.de": 65,
    "mediamarkt.de": 65,
    "saturn.de": 65,
    "notebooksbilliger.de": 60,
    "alternate.de": 60,

    # --- Tier 3: DIY / building materials (fallback for installation parts) ---
    "bauhaus.info": 65,
    "hornbach.de": 65,
    "obi.de": 62,
    "hagebau.de": 62,
    "toom.de": 58,

    # --- Tier 3: B2B marketplaces / sourcing platforms ---
    "wer-liefert-was.de": 72,
    "wlw.de": 72,
    "europages.de": 60,

    # --- Tier 4: marketplaces (price noise risk) ---
    "ebay.de": 40,
    "kleinanzeigen.de": 25,
}

# Manufacturer-owned sites are usually informational (datasheets, product
# configurators) and rarely have purchasable prices. They still get some
# score so they can serve as fallback, but distributor sites outrank them.
# The _score_url() function applies an explicit penalty when the URL's
# domain is in this set.
_MANUFACTURER_DOMAINS: set[str] = {
    # Electrical / industrial
    "niedax.de", "niedax-group.com",
    "obo-bettermann.com", "obo.de",
    "hager.com", "hager.de",
    "siemens.com", "siemens.de",
    "abb.com", "abb.de",
    "schneider-electric.com", "schneider-electric.de",
    "phoenixcontact.com", "phoenixcontact.de",
    "weidmueller.com", "weidmueller.de",
    "wago.com", "wago.de",
    "rittal.com", "rittal.de",
    "ledvance.com", "ledvance.de",
    "osram.com", "osram.de",
    "philips.com",  # Signify / Philips Lighting
    "trilux.com", "trilux.de",
    "bega.com", "bega.de",
    "gira.de", "gira.com",
    "merten.de",
    "jung.de",
    "busch-jaeger.de",
    # Tools / industrial
    "bosch.com",
    "bosch-professional.com",
    "makita.de", "makita.com",
    "festool.com", "festool.de",
    "hilti.com", "hilti.de",
    "wera.de",
    "gedore.com",
    "stahlwille.com",
    # Consumer electronics brands
    "apple.com",
    "sony.de", "sony.com",
    "samsung.com", "samsung.de",
    "lg.com",
    "panasonic.com",
}

# Explicit penalty (subtracted from the URL score) when the URL lives on a
# manufacturer domain. Keeps manufacturer pages available as fallback but
# ensures distributors with actual prices win the fetch slot.
_MANUFACTURER_PENALTY = 40

# URL-path signals. Product pages tend to win the fetch, category /
# search result pages tend to lose (they aggregate many prices, most
# unrelated to the query).
_PRODUCT_PATH_MARKERS = (
    "/produkt/", "/product/", "/artikel/", "/article/",
    "/p/", "/sku/", "/detail/", "/item/",
    "/shop/", "/kaufen/",
    # Aggregator-specific product page markers (strong signal)
    "/offersofproduct/",      # idealo.de/preisvergleich/OffersOfProduct/<id>
    "/baseproducts/",         # billiger.de/baseproducts/<id>
    "/dp/",                   # amazon /dp/<asin>
)
_CATEGORY_PATH_MARKERS = (
    "/kategorie/", "/category/", "/c/",
    "/suche", "/search", "?q=", "?query=", "?fs=", "?xf=",
    "?searchterm=", "?text=",
    "mainsearchproductcategory",
    "/marken/", "/brand/", "/sortiment/",
    "/hersteller/", "/manufacturer/",
)
_PRODUCT_PATH_BONUS = 15   # raised from 8 -- product URLs are much stronger
_CATEGORY_PATH_PENALTY = 30  # raised from 15 -- listing pages are traps

# URL paths that almost always yield junk prices (archive listings, sold
# items, classified ads closed). Exclude these entirely from fetch.
_URL_BLACKLIST_MARKERS = (
    "/verkauft/", "/sold/", "/ended/", "/abgelaufen/",
    "/archiv/", "/archive/", "/forum/", "/blog/",
)


_FOREIGN_QUALIFIER_PENALTY = 25  # raised enough to drop a mismatched
# model variant below any legitimate distributor listing.


def _foreign_model_qualifier_penalty(
    query: str,
    url: str,
    profile: "CategoryProfile | None" = None,
) -> int:
    """Penalise URLs that carry a model qualifier NOT in the query, sitting
    in the model-number zone of the URL path.

    When a `profile` is supplied, its `rule_c_fillers` extend the built-in
    Rule C filler set. When `profile=None`, behaviour is bit-identical to
    pre-v1.0 scoring.

    Distinguishes model variants that share a digit anchor but differ
    in a power/chassis/regional qualifier:
      - query "Bosch GBH 2-26 F"  (corded 830W)
      - url   ".../gbh-18v-26-f-..."   (cordless 18V Akku) -> rule A
      - query "Sony WH-1000XM5"
      - url   ".../sony-wh-1000xm4-..." (old gen)          -> rule A
      - query "OBO Bettermann KSA-S40"
      - url   ".../ksa-s50-kabelschelle/"                  -> rule B
      - query "Fluke 1674FC SCH"  (Schuko CEE 7/4)
      - url   ".../fluke-1674-fc-ftt-..."  (FTT regional)  -> rule C

    Three complementary rules:
      (A) A short (2-8 char) letter-digit segment IMMEDIATELY adjacent
          to a shared query digit anchor -- catches "18v" next to "26"
          (query has "2-26"), or "xm4" next to "1000" (query has XM5).
      (B) A short (2-5 char) letter-digit URL segment, not present in
          the query, neighbouring (via dash) any query alpha word of
          >= 2 letters -- catches "s50" between "ksa" and "kabelschelle"
          when the query is "KSA-S40".
      (C) Only when the query has an uppercase-only 2-4 char variant
          suffix (like "SCH", "FTT", "EU", "US"): a pure-alpha token
          (2-4 chars) sitting within a 30-char window around a shared
          digit anchor that is NOT present in the query's alpha tokens.
          Catches "ftt" in "/fluke-1674-fc-ftt-..." when the query
          asked for "Fluke 1674FC SCH".

    Rules A/B skip:
      - Segments containing the query's own text (query_alnum substring)
      - Spec ratings like "100a", "230v", "50hz" (\\d{2,}[a-z]{1,2}$)
      - ASIN-like IDs (>= 6 chars starting with letter-digit)

    Rule C skips well-known filler words ("und", "der", "die", "mit",
    "von", "auf", "fur", "der", "bei", "com", ...) and trigger-tokens
    the query itself contains lowercased.

    Returns _FOREIGN_QUALIFIER_PENALTY if any rule triggers, else 0.
    """
    try:
        parsed = urlparse(url)
        path = parsed.path.lower()
    except Exception:
        return 0
    if not path:
        return 0
    query_low = query.lower()
    query_alnum = re.sub(r"[^a-z0-9]", "", query_low)
    if not query_alnum:
        return 0

    # Common unit suffixes that indicate a rating rather than a model
    # qualifier. "18v" would match \\d{2,}v -> so we exclude "v" here
    # (in power-tool naming "18V" is a model identifier, not a rating).
    _is_unit = re.compile(r"^\d{2,}(a|w|hz|khz|mhz|kw|kwh|va|kva|mm|cm|m|km|kg|g|l|ml|pcs|stk)$").match

    def _is_foreign_compound(seg: str) -> bool:
        if not seg:
            return False
        has_l = any(c.isalpha() for c in seg)
        has_d = any(c.isdigit() for c in seg)
        if not (has_l and has_d):
            return False
        if seg in query_alnum:
            return False
        if _is_unit(seg):
            return False
        # ASIN / DB-ID heuristic: 6+ chars, starts letter-digit
        if len(seg) >= 6 and seg[0].isalpha() and seg[1].isdigit():
            return False
        return True

    # -------- Rule A: adjacent to query digit anchor --------
    anchors = re.findall(r"\d{2,}", query_low)
    for anchor in anchors:
        for m in re.finditer(re.escape(anchor), path):
            # Walk LEFT through alnum + dashes for the preceding segment
            i = m.start() - 1
            while i >= 0 and (path[i].isalnum() or path[i] == "-"):
                i -= 1
            prefix = path[i + 1 : m.start()].rstrip("-")
            prev_seg = prefix.split("-")[-1] if prefix else ""
            # Walk RIGHT for the following segment
            j = m.end()
            while j < len(path) and (path[j].isalnum() or path[j] == "-"):
                j += 1
            suffix = path[m.end() : j].lstrip("-")
            next_seg = suffix.split("-")[0] if suffix else ""
            if _is_foreign_compound(prev_seg) or _is_foreign_compound(next_seg):
                return _FOREIGN_QUALIFIER_PENALTY

    # -------- Rule B: short compound neighbouring a query alpha word --------
    query_alpha_words = {
        w for w in re.findall(r"[a-z]{2,}", query_low)
    }
    if not query_alpha_words:
        return 0
    segments = re.findall(r"[a-z0-9]+", path)
    for idx, seg in enumerate(segments):
        if not (2 <= len(seg) <= 5):
            continue
        if not _is_foreign_compound(seg):
            continue
        neighbors: list[str] = []
        if idx > 0:
            neighbors.append(segments[idx - 1])
        if idx + 1 < len(segments):
            neighbors.append(segments[idx + 1])
        if any(n in query_alpha_words for n in neighbors):
            return _FOREIGN_QUALIFIER_PENALTY

    # -------- Rule C: foreign pure-alpha variant suffix --------
    # Only trigger when the query carries an uppercase-only variant
    # suffix of length 3-4 (the telltale sign of "SCH/FTT" / "CAT"
    # regional or configuration variants). Length 2 tokens like "HP"
    # are brand prefixes, not variant suffixes -- excluded.
    upper_variant_tokens = re.findall(r"\b[A-Z]{3,4}\b", query)
    if not upper_variant_tokens:
        return 0
    # Filler tokens that commonly appear inside model-zone URL segments
    # but carry no variant semantics -- tech specs, German/English
    # connectives, domain artefacts, marketing chrome.
    _rule_c_fillers = frozenset({
        "und", "der", "die", "das", "mit", "von", "auf", "fur", "bei",
        "als", "dem", "den", "the", "for", "and", "ist", "inkl", "neu",
        "new", "top", "pro", "set", "std", "art", "ean", "iec", "din",
        "kat", "cat", "rj", "html", "htm", "php", "asp", "jsp", "aspx",
        "http", "https", "www",
    })
    # Category overlay: extend the base set with profile-specific fillers
    # (e.g. "nym", "nyy", "vde" for industrial_mro; "xxl", "xxs" for fashion).
    # A None profile keeps the legacy set, preserving v2.3.5 behaviour.
    if profile is not None and profile.rule_c_fillers:
        _rule_c_fillers = _rule_c_fillers | profile.rule_c_fillers
    # Stay within the URL path SEGMENT containing the anchor -- never
    # cross a "/" boundary -- AND within 10 chars of the anchor match
    # position. This narrows the scan to the true "model zone" and
    # ignores product-description words further along the URL path
    # (e.g. "bohr" in "...2-26-f-elektro-bohr-meisselhammer...").
    _MAX_DIST = 10
    path_segments_with_offset = []
    _pos = 0
    for s in path.split("/"):
        path_segments_with_offset.append((s, _pos))
        _pos += len(s) + 1  # +1 for the "/" separator
    for seg, _seg_offset in path_segments_with_offset:
        if not seg:
            continue
        for a in anchors:
            for m_anchor in re.finditer(re.escape(a), seg):
                a_start, a_end = m_anchor.start(), m_anchor.end()
                # Whole-word pure-alpha runs inside this segment
                for m_run in re.finditer(r"[a-z]+", seg):
                    t = m_run.group(0)
                    if not (3 <= len(t) <= 4):
                        continue
                    # Distance from the nearest edge of the anchor
                    if m_run.end() <= a_start:
                        dist = a_start - m_run.end()
                    elif m_run.start() >= a_end:
                        dist = m_run.start() - a_end
                    else:
                        dist = 0
                    if dist > _MAX_DIST:
                        continue
                    if t in query_alpha_words:
                        continue
                    if t in _rule_c_fillers:
                        continue
                    # Foreign pure-alpha token in the model zone -- the
                    # variant-suffix smell (e.g. "ftt" when query has "sch").
                    return _FOREIGN_QUALIFIER_PENALTY
    return 0


def _score_url(
    url: str,
    query: str = "",
    profile: "CategoryProfile | None" = None,
    context: str = "",
) -> int:
    """Score a URL for fetch prioritization.

    Composed of:
      - Domain base score from _PRICE_SITE_SCORES, possibly raised by the
        category profile's domain_scores overlay (max-wins, never lowers)
      - Manufacturer penalty (_MANUFACTURER_PENALTY) -- potentially
        overridden per category (chemicals_lab: 0, book_media: -60)
      - URL path bonus (+) for product-like paths
      - URL path penalty (-) for category/search paths
      - Foreign model-qualifier penalty (with per-category filler overlay)
      - Article-number-in-URL bonus if the query is a number (EAN / SKU)
      - Blacklist match -> -1 (excluded before fetch)

    When `profile=None`, behaviour is bit-identical to pre-v1.0 scoring
    (regression contract).

    Returns -1 for blacklisted URLs, else a non-negative int.
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        path_q = (parsed.path + "?" + parsed.query).lower()
    except Exception:
        return 0

    # Hard blacklist -- skip entirely
    if any(marker in path_q for marker in _URL_BLACKLIST_MARKERS):
        return -1

    # Domain base score (with subdomain fallback)
    score = _PRICE_SITE_SCORES.get(domain, 0)
    if score == 0:
        for known_domain, known_score in _PRICE_SITE_SCORES.items():
            if domain.endswith(f".{known_domain}"):
                score = known_score
                break
    if score == 0:
        # Unknown domain -- small generic score if it looks like a shop
        lower_url = url.lower()
        if any(w in lower_url for w in ("/shop", "/kaufen", "/product", "/produkt")):
            score = 30
        else:
            score = 10

    # Category overlay: promote domains the profile has ranked higher.
    # max()-based so a category can LIFT a domain but never demote an
    # already-trusted one. Preserves the hand-tuned base tiers.
    if profile is not None and profile.domain_scores:
        overlay = profile.domain_scores.get(domain)
        if overlay is None:
            # subdomain match (e.g. shop.wuerth.de)
            for known_domain, known_score in profile.domain_scores.items():
                if domain.endswith(f".{known_domain}"):
                    overlay = known_score
                    break
        if overlay is not None and overlay > score:
            score = overlay

    # v1.0 beta1: Learned-domain overlay from domain_stats (dynamic discovery).
    # Only when profile is supplied (category is known). Reader never
    # raises -- returns empty dict on any SQLite issue.
    if profile is not None and profile.key != "default":
        try:
            from ..discovery.domain_stats import instance as _stats_instance
            _learned = _stats_instance().get_promoted_overlay(profile.key)
            if domain in _learned and _learned[domain] > score:
                score = _learned[domain]
        except Exception as e:  # pragma: no cover
            logger.debug("domain_stats read skipped: %s", e)

    # Manufacturer deprioritization (often informational, no prices)
    mfr_penalty = (
        profile.manufacturer_penalty_override
        if profile is not None and profile.manufacturer_penalty_override is not None
        else _MANUFACTURER_PENALTY
    )
    # Combine base manufacturer set with the profile's extension
    extra_mfrs = profile.manufacturer_domains if profile is not None else frozenset()
    all_mfrs = _MANUFACTURER_DOMAINS | extra_mfrs
    is_mfr = domain in all_mfrs or any(
        domain.endswith(f".{m}") for m in all_mfrs
    )
    if is_mfr and mfr_penalty != 0:
        score = max(5, score - mfr_penalty)

    # Path-based adjustments
    if any(marker in path_q for marker in _PRODUCT_PATH_MARKERS):
        score += _PRODUCT_PATH_BONUS
    if any(marker in path_q for marker in _CATEGORY_PATH_MARKERS):
        score = max(5, score - _CATEGORY_PATH_PENALTY)

    # Foreign model-qualifier penalty: query "GBH 2-26 F" vs URL
    # "/gbh-18v-26-f-...". Without this, shared digit anchors let the
    # cordless variant score identical to the corded. Profile's
    # rule_c_fillers extend the base filler set.
    fq_penalty = _foreign_model_qualifier_penalty(query, url, profile=profile)
    if fq_penalty:
        score = max(5, score - fq_penalty)

    # v1.0 Category-specific variant detectors
    # (fashion_size / fashion_color / wine_vintage / book_edition /
    #  automotive_oem). Fires only when the active profile names them.
    # Context (SearXNG title + snippet, or page title after fetch)
    # is threaded through so fashion detectors can catch size/color
    # mismatches that never appear in the URL path.
    if profile is not None and profile.variant_detectors:
        try:
            from ..categories.variant_detectors import run_category_detectors
            vd_penalty = run_category_detectors(
                query, url, profile.variant_detectors, context=context,
            )
            if vd_penalty:
                score = max(5, score - vd_penalty)
        except Exception as e:  # pragma: no cover
            logger.debug("variant_detectors unavailable: %s", e)

    # If the query is an EAN / article number and it appears in the URL,
    # this URL is almost certainly a direct product match.
    q_token = re.sub(r"[^0-9A-Za-z.\-]", "", query.strip())
    if q_token and len(q_token) >= 6 and q_token.lower() in path_q:
        score += 10

    return score


def _domain_of(url_or_merchant: str) -> str:
    """Extract a normalized domain from a URL or merchant string."""
    s = url_or_merchant.strip().lower()
    if "://" in s:
        try:
            s = urlparse(s).netloc
        except Exception:
            pass
    if s.startswith("www."):
        s = s[4:]
    return s


# Country-variant domains that belong to the same retailer. Without
# grouping, "geizhals.de" and "geizhals.at" consume two fetch slots
# for what is essentially one price index. When one is present in
# top URLs we skip the other for diversity purposes.
_DOMAIN_FAMILY_ALIASES: dict[str, str] = {
    # Geizhals runs a shared price database across DE/AT
    "geizhals.at": "geizhals.de",
    "geizhals.eu": "geizhals.de",
    # RS Components EU country sites
    "de.rs-online.com": "rs-online.com",
    "at.rs-online.com": "rs-online.com",
    "rs-components.com": "rs-online.com",
    # Farnell / Newark country sites
    "de.farnell.com": "farnell.com",
    "at.farnell.com": "farnell.com",
    "uk.farnell.com": "farnell.com",
    # Schaefer-Shop DE/COM
    "schaefer-shop.com": "schaefer-shop.de",
    # Wuerth industrie/main
    "wuerth-industrie.de": "wuerth.com",
    "hoffmann-group.de": "hoffmann-group.com",
    # Berner country variants
    "berner.de": "berner-group.com",
    # Engelbert Strauss DE/COM
    "engelbert-strauss.com": "engelbert-strauss.de",
    # Jacob Elektronik -- "direkt.jacob.de" is the B2B subdomain, jacob.de
    # is consumer. Treat as one family so one idealo listing doesn't
    # consume two fetch slots.
    "direkt.jacob.de": "jacob.de",
    # Mercateo TLD variants share one index
    "mercateo.de": "mercateo.com",
}


def _domain_family(domain: str) -> str:
    """Canonical family key for domain-diversity checks.

    Different TLD variants of the same retailer (geizhals.de vs
    geizhals.at, de.rs-online.com vs rs-online.com) share a price
    database and should count as ONE source for fetch diversity.

    Also collapses arbitrary subdomains onto their registrable parent:
    webshop.unielektro.de and unielektro.de should share one fetch
    slot, not two.
    """
    if not domain:
        return domain
    # Explicit alias first (for cases where the parent domain is wrong,
    # e.g., geizhals.at -> geizhals.de).
    if domain in _DOMAIN_FAMILY_ALIASES:
        return _DOMAIN_FAMILY_ALIASES[domain]
    # Collapse arbitrary subdomains onto registrable parent. Naive
    # eTLD handling (sufficient for our use case: we care about
    # shop.acme.de -> acme.de, not edge cases like foo.co.uk).
    parts = domain.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[-2:])
        if parent in _DOMAIN_FAMILY_ALIASES:
            return _DOMAIN_FAMILY_ALIASES[parent]
        return parent
    return domain


def _is_trusted_domain(domain: str) -> bool:
    """Subdomain-aware trusted-domain check.

    We keep TRUSTED_PRICE_DOMAINS as a flat set of bare domains, but
    shops often appear as subdomains ("de.rs-online.com",
    "shop.conrad.de"). Without this helper such prices would be
    excluded from the anchor pool even though the underlying retailer
    is trusted.
    """
    if not domain:
        return False
    if domain in TRUSTED_PRICE_DOMAINS:
        return True
    for td in TRUSTED_PRICE_DOMAINS:
        if domain.endswith(f".{td}"):
            return True
    return False


# Search-URL templates for the major German B2B wholesalers. These
# shops almost never surface their prices in SearXNG (gated behind
# customer login), but procurement users want to know where to go
# manually. When a B2B query returns thin results, we synthesize a
# "login-gated" entry pointing to the distributor's search page so
# the user can click through with their own credentials.
_B2B_DISTRIBUTOR_HINTS: list[tuple[str, str]] = [
    ("sonepar.de", "https://www.sonepar.de/shop/de/DE/search/?text={q}"),
    ("rexel.de", "https://www.rexel.de/shop/de/DE/search?text={q}"),
    ("mercateo.com", "https://www.mercateo.com/s.html?searchterm={q}"),
]


# Direct-search URL templates for major public aggregators. Unlike
# the B2B hints above, these ARE actually fetched by Playwright. We
# inject them into the URL candidate pool whenever SearXNG failed
# to surface a direct product-page URL for that aggregator. Idealo
# in particular often has B2B-adjacent products indexed, but Google
# / Bing may not always rank the specific product page high enough
# to appear in the top SearXNG results. The search URL redirects
# to the product page if the aggregator has the SKU, otherwise it
# returns an empty search-results page (yielding 0 offers, no harm).
_AGGREGATOR_SEARCH_TEMPLATES: list[tuple[str, str]] = [
    ("idealo.de", "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q={q}"),
    ("geizhals.de", "https://geizhals.de/?fs={q}"),
    ("conrad.de", "https://www.conrad.de/de/search.html?search={q}"),
]


def _build_b2b_distributor_hints(query: str) -> list[dict[str, Any]]:
    """Build synthetic login-gated entries for top German B2B wholesalers.

    Returned list has the same shape as a login-gated offer coming from
    an actual Playwright fetch. The `source` field is set to "b2b-hint"
    so the LLM can differentiate these proactively surfaced merchants
    from ones we actually probed and found gated.
    """
    q_encoded = quote_plus(query.strip())
    hints: list[dict[str, Any]] = []
    for domain, template in _B2B_DISTRIBUTOR_HINTS:
        hints.append({
            "merchant": domain,
            "price": 0.0,
            "shipping_cost": 0.0,
            "total_price": 0.0,
            "currency": "EUR",
            "url": template.format(q=q_encoded),
            "source": "b2b-hint",
            "login_required": True,
            "availability": "on_request",
            "vat_status": "net",  # DE electrical wholesalers are always net
            "has_tier_pricing": False,
            "min_order_quantity": None,
            "tier_pricing": None,
        })
    return hints


def _select_diverse_urls(
    scored_urls: list[tuple[str, int]],
    max_total: int,
    max_per_domain: int,
) -> list[str]:
    """Select top URLs by score, enforcing max-per-domain-family for
    fetch diversity.

    Without this, a top-scoring domain like idealo.de would consume
    all fetch slots, leaving no room for distributor sites with
    prices for the same product. Domain grouping goes one level
    deeper: country variants (geizhals.de + geizhals.at, de.rs-online
    + rs-online.com) are collapsed to one family so they don't each
    grab a slot.
    """
    family_counts: dict[str, int] = {}
    selected: list[str] = []
    for url, _score in scored_urls:
        domain = _domain_of(url)
        if not domain:
            continue
        family = _domain_family(domain)
        if family_counts.get(family, 0) >= max_per_domain:
            continue
        selected.append(url)
        family_counts[family] = family_counts.get(family, 0) + 1
        if len(selected) >= max_total:
            break
    return selected


def _product_match_confidence(
    query: str,
    page_title: str,
    profile: "CategoryProfile | None" = None,
    apply_antilex: bool = True,
) -> str:
    """Estimate how well a fetched page matches the query.

    Uses the same digit-anchor heuristic as the extractor's title-gate:
    model-number digit RUNS ("1674", "200", "400") are the stable
    core of a SKU and survive catalog formatting churn ("1674FC" vs
    "1674 FC", "200.400" vs "200/400").

    Returns one of:
      - "high"   -- brand present + all digit anchors covered
      - "medium" -- most (>=80%) digit anchors covered, or strong
                    descriptive overlap without anchors
      - "low"    -- brand-only, or wrong/missing anchors
      - ""       -- no title available, cannot judge

    NOTE: Intentionally FORGIVING toward variant-suffix churn (query
    "FLUKE 1674FC SCH" on a page titled "Fluke 1674 FC FTT"). The
    catalog side almost always normalises whitespace/suffixes while
    the user types the marketing-copy SKU. Matching on stable digit
    runs yields the same result across both catalogs and queries.

    v1.0 enhancement: when `profile` is supplied and the page title
    contains any word from `profile.title_gate_antilex`, the result is
    forced to "low" regardless of token match. Catches brand collisions
    like a tools_hardware query landing on a "Bosch Spuelmaschine" page.
    """
    if not page_title:
        return ""

    t_low = page_title.lower()

    # v1.0 Anti-Lex Title-Gate -- evaluated first so it overrides the
    # token/anchor logic below. Only activates when profile has antilex
    # entries AND the caller requested the gate; profile=None or
    # apply_antilex=False preserves pre-v1.0 behaviour.
    if (apply_antilex
        and profile is not None
        and profile.title_gate_antilex):
        for word in profile.title_gate_antilex:
            if word and word in t_low:
                logger.info(
                    "[antilex] downgrading mc to low: %r found in title %r",
                    word, page_title[:80],
                )
                return "low"

    q_alpha = re.findall(r"[A-Za-z]{3,}", query)
    brand = q_alpha[0].lower() if q_alpha else None
    anchors = _digit_anchors(query, min_len=3)

    if not anchors and not q_alpha:
        # Pure digit / very short query -> no info
        return ""

    # Case A: Query carries digit anchors (model numbers).
    if anchors:
        t_digits = re.sub(r"[^0-9a-z]+", "", t_low)
        present = [a for a in anchors if a in t_digits]
        coverage = len(present) / len(anchors)
        brand_ok = (brand is None) or (brand in t_low)

        if coverage >= 1.0 and brand_ok:
            return "high"
        if coverage >= 0.8 and brand_ok:
            return "medium"
        if coverage >= 0.5:
            return "low"
        # Anchor coverage below 50% -> wrong SKU
        return "low"

    # Case B: Descriptive query (no anchors). Use token overlap.
    def _tokenize(s: str) -> set[str]:
        tokens = re.findall(r"[\w.\-]+", s.lower(), flags=re.UNICODE)
        stop = {
            "und", "oder", "fuer", "für", "mit", "ohne",
            "the", "and", "for", "with", "shop", "online",
            "kaufen", "preis", "bestellen",
        }
        return {t for t in tokens if len(t) >= 3 and t not in stop}

    q_tokens = _tokenize(query)
    t_tokens = _tokenize(page_title)
    if not q_tokens:
        return ""
    matched = q_tokens & t_tokens
    if brand and brand not in t_low:
        return "low"
    if len(matched) >= max(2, len(q_tokens) - 1):
        return "high"
    if len(matched) >= len(q_tokens) // 2:
        return "medium"
    return "low"


def _digit_anchors(text: str, min_len: int = 3) -> list[str]:
    """Extract digit-runs of length >= min_len (same as price_extractor)."""
    return [s for s in re.findall(r"\d+", text) if len(s) >= min_len]


def _enforce_per_domain_cap(
    offers: list[dict[str, Any]],
    max_per_domain: int,
) -> list[dict[str, Any]]:
    """Limit offers shown per merchant domain-family.

    Same idea as `_select_diverse_urls`: group country-variant
    domains so "geizhals.de + geizhals.at" don't together exceed
    the effective per-source cap.
    """
    family_counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for offer in offers:
        merchant = offer.get("merchant", "")
        family = _domain_family(_domain_of(merchant)) or "unknown"
        if family_counts.get(family, 0) >= max_per_domain:
            continue
        result.append(offer)
        family_counts[family] = family_counts.get(family, 0) + 1
    return result


def _deduplicate_urls(urls: list[str]) -> list[str]:
    """Remove duplicate URLs (normalize trailing slashes, www prefix)."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        normalized = url.rstrip("/")
        parsed = urlparse(normalized)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        key = f"{domain}{parsed.path}"
        if key not in seen:
            seen.add(key)
            result.append(url)
    return result


def _detect_search_type(query: str) -> str:
    """Detect if query is an EAN barcode or product name."""
    cleaned = re.sub(r"[\s\-]", "", query.strip())
    if cleaned.isdigit() and len(cleaned) in (8, 12, 13, 14):
        return "ean"
    return "name"


# v1.0 price_source_confidence -> numeric weights used to compute
# the composite_confidence per offer. JSON-LD is structured and parsed
# by the browser/site vendor; weaker signals get smaller weights.
_PRICE_SOURCE_WEIGHTS: dict[str, float] = {
    "json_ld": 1.00,
    "microdata": 0.90,
    "css_site": 0.85,
    "css_generic": 0.60,
    "regex": 0.40,
    "": 0.75,  # unknown -- assume site-specific-equivalent
}

# match_confidence -> numeric weights for composite.
_MATCH_CONF_WEIGHTS: dict[str, float] = {
    "exact": 1.00,
    "high": 0.90,
    "medium": 0.70,
    "low": 0.40,
    "": 0.60,
}


def _composite_confidence(
    match_confidence: str,
    price_source: str,
) -> float:
    """Combine (match_confidence, price_source) -> single float in [0, 1].

    Multiplicative: a low-confidence extraction path shrinks the score
    even when the title match looks great. Used by procurement reports
    to rank trust among offers with otherwise-identical total_price.
    """
    m = _MATCH_CONF_WEIGHTS.get((match_confidence or "").lower(), 0.60)
    s = _PRICE_SOURCE_WEIGHTS.get((price_source or "").lower(), 0.75)
    return round(m * s, 3)


# Outlier thresholds, expressed as multiples of the anchor price.
# Wide on purpose -- only clear extremes (accessories, packaging units,
# bundles, contracts) get flagged, not normal market spread.
_OUTLIER_LOW_RATIO = 0.2   # < 20% of anchor
_OUTLIER_HIGH_RATIO = 5.0  # > 500% of anchor

# Secondary, MAD-based threshold. A price is flagged when it is more
# than MAD_K * MAD away from the anchor AND outside the ratio window.
# Useful for tight markets where 0.2x/5x is too loose (e.g. a product
# clustered in a 10-EUR window where a 200-EUR outlier should be flagged).
_OUTLIER_MAD_K = 10.0

# v1.0 cluster-mode threshold. When the overall price range spans more
# than _CLUSTER_MODE_SPREAD_RATIO (max/min), the data carries multiple
# legitimate product variants at different scale units (single stick
# 0.60 EUR vs 12-pack 4 EUR vs mega-pack 45 EUR -- Staedtler case).
# Ratio/MAD would flag everything as outlier; cluster mode groups
# prices by log-scale gaps and only flags offers that fall BETWEEN
# clusters (true anomalies), not offers WITHIN a multi-member cluster.
_CLUSTER_MODE_SPREAD_RATIO = 10.0   # max/min ratio triggering cluster mode
_CLUSTER_LOG_GAP_THRESHOLD = 1.0986  # ln(3) -- 3x price jump between clusters


def _cluster_prices(prices: list[float]) -> list[list[float]]:
    """Split a sorted price list into clusters at >=3x log-scale gaps.

    Example: [0.60, 0.65, 3.50, 4.00, 4.50, 45.0, 48.0] ->
      [[0.60, 0.65], [3.50, 4.00, 4.50], [45.0, 48.0]]

    Singleton clusters are kept but can be treated as candidate
    outliers by the caller (a lone price separated by 3x from any
    peer is statistically isolated).
    """
    if not prices:
        return []
    import math
    sorted_p = sorted(prices)
    if len(sorted_p) == 1:
        return [sorted_p]
    clusters: list[list[float]] = [[sorted_p[0]]]
    for i in range(1, len(sorted_p)):
        prev = sorted_p[i - 1]
        curr = sorted_p[i]
        if prev <= 0:
            clusters[-1].append(curr)
            continue
        log_gap = math.log(curr) - math.log(prev)
        if log_gap >= _CLUSTER_LOG_GAP_THRESHOLD:
            clusters.append([curr])
        else:
            clusters[-1].append(curr)
    return clusters

# Domains whose prices serve as the "truth anchor" for outlier detection.
# Derived automatically from _PRICE_SITE_SCORES tier 2-3 (distributors,
# aggregators, retailers) -- we keep a single source of truth. Marketplace
# tier (ebay, kleinanzeigen) and manufacturer domains are excluded.
TRUSTED_PRICE_DOMAINS: set[str] = {
    domain for domain, score in _PRICE_SITE_SCORES.items()
    if score >= 55 and domain not in _MANUFACTURER_DOMAINS
}


def _median_absolute_deviation(values: list[float], median: float) -> float:
    """Compute the Median Absolute Deviation (MAD) -- a robust dispersion
    statistic that is resilient to extreme outliers (unlike the standard
    deviation). MAD is used as a secondary check alongside the simple
    ratio-based thresholds to catch cases where the market is tight and
    a few extreme values need flagging even though they are within 5x.
    """
    if not values:
        return 0.0
    absolute_deviations = [abs(v - median) for v in values]
    return statistics.median(absolute_deviations)


def _flag_outliers(
    offers: list[dict[str, Any]],
    profile: "CategoryProfile | None" = None,
) -> tuple[int, float | None]:
    """Annotate each offer in-place with `is_outlier` and `outlier_reason`.

    Anchor selection:
      - If the result contains prices from TRUSTED_PRICE_DOMAINS, anchor
        on the median (or single value) of those trusted prices.
      - Otherwise fall back to the median of all prices.

    Trusted offers ARE still checked against the anchor (a trusted
    domain can list an accessory by mistake), but the anchor is computed
    only from trusted prices when available -- so trusted-domain medians
    are not poisoned by junk hits from less-known shops.

    v1.0 Price-Band Gate:
      When `profile` exposes a `price_band=(min, max)`, every offer
      outside that band is flagged regardless of the statistical window.
      Example: a book at 499 EUR (book_media band 1-500) stays valid,
      but a book at 1200 EUR is flagged as "outside category price band".
      Applied BEFORE the statistical tests so the reason string is
      category-specific and more actionable.

    Returns (count_flagged, anchor_price_used).
    """
    prices_with_offers = [
        (o["total_price"], o) for o in offers if o.get("total_price")
    ]
    if len(prices_with_offers) < 4:
        # Too few offers for meaningful outlier detection --
        # but still apply category price-band check (pure threshold,
        # needs no statistics).
        band = profile.price_band if profile is not None else None
        for o in offers:
            p = o.get("total_price")
            if band and p is not None and (p < band[0] or p > band[1]):
                o["is_outlier"] = True
                cat_name = profile.display_name if profile else "category"
                o["outlier_reason"] = (
                    f"price {p:.2f} EUR is outside the {cat_name} price "
                    f"band ({band[0]:.2f}-{band[1]:.2f} EUR) -- likely wrong SKU or unit"
                )
            else:
                o["is_outlier"] = False
                o["outlier_reason"] = None
        return sum(1 for o in offers if o.get("is_outlier")), None

    # Layered anchor selection:
    #   1. Exclude offers with low product-match confidence from the
    #      anchor pool -- listing / category pages would otherwise
    #      pull the anchor away from the real product price.
    #   2. Within reliable offers, prefer trusted-domain prices.
    #   3. Fall back to all offers only if every candidate is low-match
    #      (rare; happens when SearXNG found no real product pages).
    reliable_offers = [
        (p, o) for p, o in prices_with_offers
        if o.get("match_confidence") != "low"
    ]
    anchor_pool = reliable_offers if reliable_offers else prices_with_offers
    anchor_pool_was_filtered = bool(reliable_offers) and len(reliable_offers) < len(prices_with_offers)

    trusted_prices: list[float] = []
    for price, offer in anchor_pool:
        merchant = offer.get("merchant", "")
        domain = _domain_of(merchant)
        if _is_trusted_domain(domain):
            trusted_prices.append(price)

    # Pick anchor: trusted-domain median when available; else median of
    # the reliable (non-low-match) pool; else median of all offers.
    if trusted_prices:
        anchor = statistics.median(trusted_prices)
        desc = (
            "trusted-domain median over "
            f"{len(trusted_prices)} product-match offer(s)"
            if anchor_pool_was_filtered
            else f"trusted-domain median over {len(trusted_prices)} offer(s)"
        )
        anchor_source = desc
    elif reliable_offers:
        anchor = statistics.median(p for p, _ in reliable_offers)
        anchor_source = (
            f"product-match median over {len(reliable_offers)} offer(s) "
            f"(no trusted-domain with match_confidence>=medium)"
        )
    else:
        anchor = statistics.median(p for p, _ in prices_with_offers)
        anchor_source = (
            "overall median (no reliable product-matches found)"
        )

    low_threshold = anchor * _OUTLIER_LOW_RATIO
    high_threshold = anchor * _OUTLIER_HIGH_RATIO

    # v1.0 cluster mode: when the price range spans >10x, the data
    # almost certainly contains multiple legitimate scale units
    # (single-stick / pack / bulk). Ratio/MAD would flag everything;
    # cluster mode identifies valid groups and only flags offers that
    # sit in singleton clusters surrounded by >3x gaps.
    all_prices = [p for p, _ in prices_with_offers]
    price_spread = (max(all_prices) / min(all_prices)) if min(all_prices) > 0 else 0
    use_cluster_mode = price_spread >= _CLUSTER_MODE_SPREAD_RATIO
    cluster_membership: dict[float, int] = {}
    cluster_sizes: list[int] = []
    if use_cluster_mode:
        clusters = _cluster_prices(all_prices)
        cluster_sizes = [len(c) for c in clusters]
        for idx, cl in enumerate(clusters):
            for p in cl:
                cluster_membership[p] = idx
        logger.info(
            "Outlier cluster mode: spread %.1fx -> %d clusters %s",
            price_spread, len(clusters), cluster_sizes,
        )

    # MAD is computed over the anchor pool (trusted prices if we have
    # them, else the reliable/any pool used for the anchor itself).
    mad_sample = (
        trusted_prices
        if trusted_prices
        else [p for p, _ in anchor_pool]
    )
    mad_sample_median = statistics.median(mad_sample) if mad_sample else anchor
    mad = _median_absolute_deviation(mad_sample, mad_sample_median)
    # If MAD is tiny (tight cluster), still use a minimum floor of 10% of
    # anchor so we don't flag every price slightly off-median.
    effective_mad = max(mad, anchor * 0.10) if anchor > 0 else mad

    # Category price-band (profile-driven, hard bound)
    price_band = profile.price_band if profile is not None else None
    cat_name = profile.display_name if profile is not None else None

    flagged = 0
    for price, offer in prices_with_offers:
        merchant = offer.get("merchant", "")
        domain = _domain_of(merchant)
        is_trusted = _is_trusted_domain(domain)

        # v1.0: Category price-band gate (evaluated FIRST so the reason
        # is user-facing and category-specific).
        band_outlier = (
            price_band is not None
            and (price < price_band[0] or price > price_band[1])
        )

        # v1.0 Cluster mode: when price spread is >10x, only flag
        # offers sitting in SINGLETON clusters. Multi-member clusters
        # are valid product variants at different scale units and
        # must NOT all be marked as outliers.
        cluster_outlier = False
        if use_cluster_mode and not band_outlier:
            cid = cluster_membership.get(price)
            if cid is not None and cluster_sizes[cid] == 1:
                cluster_outlier = True

        # Primary test: ratio window around anchor. Skipped in cluster
        # mode because the spread legitimately spans >5x.
        ratio_outlier = (
            not use_cluster_mode
            and (price < low_threshold or price > high_threshold)
        )

        # Secondary test: MAD distance from anchor (catches tight markets)
        # Also skipped in cluster mode -- MAD over multi-modal data is
        # meaningless.
        mad_outlier = (
            not use_cluster_mode
            and effective_mad > 0
            and abs(price - anchor) > _OUTLIER_MAD_K * effective_mad
        )

        if not band_outlier and not ratio_outlier and not mad_outlier and not cluster_outlier:
            offer["is_outlier"] = False
            offer["outlier_reason"] = None
            continue

        direction = "low" if price < anchor else "high"
        comparator = "<" if direction == "low" else ">"
        if band_outlier and price_band is not None:
            # Category-specific reason (highest-priority signal)
            threshold_note = (
                f"outside the {cat_name} price band "
                f"({price_band[0]:.2f}-{price_band[1]:.2f} EUR)"
            )
        elif cluster_outlier:
            threshold_note = (
                f"isolated price (singleton cluster amid {len(cluster_sizes)} "
                f"multi-member clusters -- likely different SKU / scale unit)"
            )
        elif ratio_outlier:
            ratio_pct = (
                _OUTLIER_LOW_RATIO * 100 if direction == "low"
                else _OUTLIER_HIGH_RATIO * 100
            )
            threshold_note = (
                f"{comparator} {ratio_pct:.0f}% of {anchor_source} "
                f"({anchor:.2f} EUR)"
            )
        else:
            threshold_note = (
                f"more than {_OUTLIER_MAD_K:.0f}x MAD "
                f"({effective_mad:.2f} EUR) from anchor ({anchor:.2f} EUR)"
            )

        if is_trusted:
            cause = (
                "likely a stray sub-listing on a trusted domain (different SKU, "
                "accessory, or refurbished); verify the product page"
            )
        elif direction == "low":
            cause = "likely accessory, quantity unit, or wrong product"
        else:
            cause = "likely bundle, contract, or wrong variant"

        offer["is_outlier"] = True
        offer["outlier_reason"] = (
            f"price {price:.2f} EUR is {threshold_note} -- {cause}"
        )
        flagged += 1

    # Ensure offers without total_price also get the fields (consistency)
    for o in offers:
        o.setdefault("is_outlier", False)
        o.setdefault("outlier_reason", None)

    return flagged, round(anchor, 2)


def _compute_insights(offers: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute price insights from a list of offer dicts.

    Insights are computed twice: once over all offers (raw) and once over
    the non-outlier subset (filtered). The filtered values are usually the
    more meaningful market signal.
    """
    prices = [o["total_price"] for o in offers if o.get("total_price")]
    if len(prices) < 2:
        return {}

    merchants = set(o.get("merchant", "").lower() for o in offers if o.get("merchant"))
    sorted_prices = sorted(prices)

    insights: dict[str, Any] = {
        "min_price": sorted_prices[0],
        "max_price": sorted_prices[-1],
        "median_price": round(statistics.median(sorted_prices), 2),
        "avg_price": round(statistics.mean(sorted_prices), 2),
        "price_spread": round(sorted_prices[-1] - sorted_prices[0], 2),
        "num_offers": len(prices),
        "num_merchants": len(merchants),
    }

    # Filtered insights (non-outlier subset)
    clean_prices = [
        o["total_price"] for o in offers
        if o.get("total_price") and not o.get("is_outlier", False)
    ]
    if 0 < len(clean_prices) < len(prices):
        clean_sorted = sorted(clean_prices)
        insights["filtered"] = {
            "min_price": clean_sorted[0],
            "max_price": clean_sorted[-1],
            "median_price": round(statistics.median(clean_sorted), 2),
            "avg_price": round(statistics.mean(clean_sorted), 2),
            "num_offers": len(clean_prices),
            "outliers_excluded": len(prices) - len(clean_prices),
            "note": "values computed after excluding flagged outliers (is_outlier=true)",
        }

    return insights


# ---------------------------------------------------------------------------
# Aggregator search-page -> product-page resolver
# ---------------------------------------------------------------------------
# Some aggregator search endpoints (geizhals.de/?fs=, idealo.de/preisver-
# gleich/MainSearchProductCategory.html when ambiguous) don't 302 to a
# product page; they render a SERP-like grid. In that case we pick the
# best-matching product link and navigate to it so the downstream
# aggregator-specific extractor runs on a real product page.
_AGGREGATOR_SEARCH_PRODUCT_SELECTORS: dict[str, list[str]] = {
    # Geizhals search results: cards link to "-a<digits>.html" or "-v<digits>.html"
    "geizhals.de": [
        "a[href*='-a'][href$='.html']",
        "a[href*='-v'][href$='.html']",
        "div.cat-product-list__item a[href*='.html']",
    ],
    "geizhals.at": [
        "a[href*='-a'][href$='.html']",
        "a[href*='-v'][href$='.html']",
    ],
    # Idealo search results: cards link to "/preisvergleich/OffersOfProduct/<id>"
    "idealo.de": [
        "a[href*='/preisvergleich/OffersOfProduct/']",
    ],
}


def _is_product_url_for_domain(url: str, domain: str) -> bool:
    """Narrow product-URL check for the three aggregators we resolve."""
    if "geizhals." in domain:
        return bool(re.search(r"-[va]\d{5,}\.html", url, re.IGNORECASE))
    if "idealo." in domain:
        return "OffersOfProduct/" in url
    return False


async def _resolve_aggregator_search_to_product(
    page,
    url: str,
    query: str,
    timeout_seconds: int,
    profile: "CategoryProfile | None" = None,
) -> str:
    """If `url` is an aggregator SERP, navigate to the best product link.

    Returns the post-navigation URL (equal to `url` when no resolution
    was needed or the SERP had no matching product cards). Safe to call
    on any URL; it early-returns for non-aggregator or already-product
    pages.

    The optional `profile` flows into the foreign-qualifier check so
    per-category rule_c_fillers apply during link scoring.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        return url

    # Match domain against our aggregator list
    aggregator_domain = None
    for agg_domain in _AGGREGATOR_SEARCH_PRODUCT_SELECTORS:
        if agg_domain in domain:
            aggregator_domain = agg_domain
            break
    if aggregator_domain is None:
        return url

    # Already on a product page? Nothing to do.
    if _is_product_url_for_domain(url, aggregator_domain):
        return url

    # Only act when the URL is a search endpoint (has fs=, q=, etc.).
    qs = (parsed.query or "").lower()
    first_param = qs.split("&", 1)[0]
    is_search = any(first_param.startswith(p) for p in ("fs=", "q=", "search=", "keyword="))
    if not is_search:
        return url

    selectors = _AGGREGATOR_SEARCH_PRODUCT_SELECTORS[aggregator_domain]
    # Build digit anchors from the query for match scoring
    anchors = [s for s in re.findall(r"\d+", query) if len(s) >= 3]
    anchor_set = set(anchors)
    q_alpha = [t.lower() for t in re.findall(r"[A-Za-z]{3,}", query)]

    # Distinctive tokens: query tokens that uniquely identify the
    # product SKU rather than the brand. A brand-only match ("obo"
    # + "bettermann") must NOT be enough to resolve -- otherwise the
    # aggregator resolver substitutes an unrelated product from the
    # same brand (e.g. OBO Kabelabzweigkasten for OBO KSA-S40 query).
    # A token is "distinctive" when it contains at least one digit
    # (model codes like "1674FC", "2-26", "KSA-S40", "WH-1000XM5").
    # Pure-alpha tokens even in all-caps are excluded because brand
    # names (OBO, HP, FLUKE) are often all-caps yet non-distinctive.
    distinctive_tokens: list[str] = []
    for t in re.findall(r"[A-Za-z0-9\-]+", query):
        t_clean = t.strip("-")
        if len(t_clean) < 2:
            continue
        if any(c.isdigit() for c in t_clean):
            distinctive_tokens.append(t_clean.lower())

    best_href: str | None = None
    best_haystack: str = ""
    best_score = -1
    try:
        for sel in selectors:
            links = await page.query_selector_all(sel)
            for link in links[:30]:
                try:
                    href = await link.get_attribute("href") or ""
                    if not href:
                        continue
                    # Skip fragment / obvious non-product links
                    if href.startswith("#") or "javascript:" in href:
                        continue
                    text = ""
                    try:
                        text = (await link.inner_text()).strip()
                    except Exception:
                        pass
                    title_attr = await link.get_attribute("title") or ""
                    haystack = (href + " " + text + " " + title_attr).lower()

                    score = 0
                    # Digit anchors: each match is worth a lot
                    for a in anchors:
                        if a in haystack:
                            score += 10
                    # Penalty for extra digit anchors the query did NOT
                    # specify. A combo/bundle like "1674 FC 1630" introduces
                    # "1630" which the user didn't ask for, so prefer the
                    # standalone (exactly-matching) product.
                    # Look at the link haystack only (not page-wide).
                    link_anchors = set(
                        s for s in re.findall(r"\d{3,}", haystack)
                        if s not in anchor_set
                    )
                    # Strip out the aggregator's internal product-id numbers
                    # (geizhals "-5581087-a3357933") so they don't count as
                    # "extra" anchors. Those are usually length >= 5.
                    extra_anchors = [a for a in link_anchors if len(a) < 5]
                    score -= 4 * len(extra_anchors)

                    # Alpha tokens: brand/name match
                    for t in q_alpha:
                        if t in haystack:
                            score += 3
                    # Prefer links that already look like product URLs
                    if aggregator_domain.startswith("geizhals") and re.search(
                        r"-[va]\d{5,}\.html", href
                    ):
                        score += 5
                    if aggregator_domain.startswith("idealo") and "OffersOfProduct/" in href:
                        score += 5
                    # Foreign-qualifier penalty: if the candidate URL has a
                    # letter-digit compound the query does NOT mention
                    # (e.g. query "GBH 2-26 F" vs link "gbh-18v-26-f"), demote
                    # it hard. The aggregator SERP often lists both variants,
                    # and the main URL-score penalty never runs here because
                    # we pick by link-score, not _score_url. Applied to the
                    # absolute href so Rule A can see anchor neighbours.
                    abs_href = href
                    if abs_href.startswith("/"):
                        abs_href = f"{parsed.scheme}://{parsed.netloc}{abs_href}"
                    elif not abs_href.startswith("http"):
                        abs_href = f"{parsed.scheme}://{parsed.netloc}/{abs_href.lstrip('/')}"
                    if _foreign_model_qualifier_penalty(query, abs_href, profile=profile):
                        score -= 20
                    if score > best_score:
                        best_score = score
                        best_href = href
                        best_haystack = haystack
                except Exception:
                    continue
    except Exception as e:
        logger.debug("Aggregator resolve: selector iteration failed on %s: %s", domain, e)
        return url

    if not best_href or best_score < 3:
        logger.debug(
            "Aggregator resolve: no strong product link on %s (best_score=%d)",
            domain, best_score,
        )
        return url

    # Distinctive-token gate: only resolve when the winning link's
    # haystack contains at least ONE distinctive query token. A
    # brand-only match (obo + bettermann) is not enough -- refuse
    # to substitute an unrelated product from the same brand.
    if distinctive_tokens and not any(
        t in best_haystack for t in distinctive_tokens
    ):
        logger.info(
            "Aggregator resolve: refusing %s -> %s (no distinctive query "
            "token %s in candidate haystack)",
            url[:80], best_href[:80], distinctive_tokens,
        )
        return url

    # Make absolute
    if best_href.startswith("/"):
        resolved = f"{parsed.scheme}://{parsed.netloc}{best_href}"
    elif best_href.startswith("http"):
        resolved = best_href
    else:
        resolved = f"{parsed.scheme}://{parsed.netloc}/{best_href.lstrip('/')}"

    try:
        response = await page.goto(
            resolved,
            wait_until="domcontentloaded",
            timeout=max(5, timeout_seconds - 3) * 1000,
        )
        if not response or response.status >= 400:
            logger.debug("Aggregator resolve: navigation failed for %s", resolved[:80])
            return url
        # Small settling delay for lazy-loaded offer grid
        await page.wait_for_timeout(1500)
        return page.url or resolved
    except Exception as e:
        logger.debug("Aggregator resolve: goto failed for %s: %s", resolved[:80], e)
        return url


async def _fetch_detail_page(
    url: str,
    browser_mgr: BrowserManager,
    timeout_seconds: int,
    query: str = "",
    profile: "CategoryProfile | None" = None,
) -> tuple[list[ExtractedOffer], str, dict[str, Any]]:
    """Fetch a single URL via Playwright, extract prices + page signals.

    Returns (offers, page_title, signals). Each offer is already
    annotated with the page-level signals (vat_status, login_required,
    has_tier_pricing, min_order_quantity) where applicable. If the
    page is login-gated and no price was extracted, we still return a
    synthetic login-only offer so the caller can surface the merchant
    transparently to the user rather than silently dropping the URL.
    """
    page = None
    signals: dict[str, Any] = {
        "login_gate": False,
        "vat_status": "unknown",
        "has_tier_pricing": False,
        "min_order_quantity": None,
        "availability": "",
        "tier_pricing": None,
    }
    try:
        page = await browser_mgr.get_page(url)

        # Navigate with timeout. Retry once on 503/429 (bot-detection
        # bounce) with a short jittered delay and a fresh context. Major
        # aggregators (idealo, geizhals) often 503 the first request in
        # parallel bursts but serve the retry cleanly.
        response = None
        for attempt in range(2):
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_seconds * 1000,
                )
            except Exception as e:
                if attempt == 0:
                    logger.info("Navigation failed for %s: %s (retrying)", url[:80], e)
                    await asyncio.sleep(1.5)
                    try:
                        await page.close()
                    except Exception:
                        pass
                    try:
                        page = await browser_mgr.get_page(url)
                    except Exception:
                        return [], "", signals
                    continue
                logger.info("Navigation failed for %s: %s (final)", url[:80], e)
                return [], "", signals

            if response and response.status < 400:
                break

            status = response.status if response else "no-response"
            if attempt == 0 and status in (429, 503, 403):
                logger.info(
                    "HTTP %s for %s (retrying with fresh context)",
                    status, url[:80],
                )
                await asyncio.sleep(1.5)
                try:
                    await page.close()
                except Exception:
                    pass
                try:
                    page = await browser_mgr.get_page(url)
                except Exception:
                    return [], "", signals
                continue

            logger.info("HTTP %s for %s", status, url[:80])
            return [], "", signals

        if not response:
            return [], "", signals

        # Brief human simulation to trigger lazy-loaded content
        await simulate_human_mouse(page)
        await asyncio.sleep(0.5)
        await simulate_human_scroll(page)
        await asyncio.sleep(0.5)

        # Capture title (for product-match confidence)
        try:
            page_title = (await page.title()) or ""
        except Exception as e:
            logger.info("Title fetch failed for %s: %s", url[:80], e)
            page_title = ""

        # Use the FINAL URL after any redirects for extraction rules.
        # An injected aggregator search URL (e.g. idealo's MainSearchProductCategory
        # or geizhals's ?fs=) typically 302-redirects to the real product
        # page when the SKU is indexed. Using the post-redirect URL lets
        # _is_product_page_url() and domain-based dispatch see the actual
        # product page, so we get the full 20-offer aggregator extraction
        # instead of being short-circuited by _is_aggregator_search_url().
        final_url = url
        try:
            final_url = page.url or url
        except Exception:
            pass
        if final_url != url:
            logger.debug("Redirect: %s -> %s", url[:80], final_url[:80])

        # Aggregator-search-page escape hatch: geizhals.de/?fs=... does NOT
        # auto-redirect to a product page; it renders a SERP-like grid.
        # Detect that case and navigate to the first matching product link
        # so that the downstream extractor runs on a real product page.
        # Similar fallback applies to idealo's MainSearchProductCategory
        # when its 302 resolver fails (rare, but seen for ambiguous SKUs).
        resolved_url = await _resolve_aggregator_search_to_product(
            page, final_url, query, timeout_seconds, profile=profile,
        )
        if resolved_url and resolved_url != final_url:
            logger.info(
                "Aggregator search resolved: %s -> %s",
                final_url[:80], resolved_url[:80],
            )
            final_url = resolved_url
            try:
                page_title = (await page.title()) or page_title
            except Exception:
                pass

        # Capture page-level B2B signals (login-gate, VAT, tier pricing,
        # MOQ). Done in parallel with price extraction for efficiency.
        signals = await extract_page_signals(page)

        # Extract prices using layered strategy. Passing `query` enables
        # the title-gate on Geizhals/Idealo/billiger to reject pages
        # whose H1 clearly shows a different SKU.
        offers = await extract_prices(page, final_url, query=query)

        # Extract EAN / GTIN-13 in parallel (cheap, same page context).
        # When found, attach to every offer for later cross-match.
        try:
            page_ean = await extract_ean(page)
        except Exception:
            page_ean = ""
        if page_ean:
            for offer in offers:
                if not offer.ean:
                    offer.ean = page_ean

        # Annotate every extracted offer with page-level signals.
        for offer in offers:
            if not offer.vat_status:
                offer.vat_status = signals["vat_status"]
            offer.has_tier_pricing = signals["has_tier_pricing"]
            if offer.min_order_quantity is None:
                offer.min_order_quantity = signals["min_order_quantity"]
            if not offer.availability and signals["availability"]:
                offer.availability = signals["availability"]
            if offer.tier_pricing is None and signals["tier_pricing"]:
                offer.tier_pricing = signals["tier_pricing"]
            if signals["login_gate"]:
                offer.login_required = True

        # If the page is login-gated and no price was extracted, surface
        # a placeholder "login-required" offer so the caller can report
        # the merchant explicitly ("Preis nach Login bei Sonepar") rather
        # than hiding a silent fetch failure.
        if not offers and signals["login_gate"]:
            domain = url
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                if domain.startswith("www."):
                    domain = domain[4:]
            except Exception:
                pass
            logger.info(
                "Login gate detected for %s (no extractable price)",
                domain,
            )
            offers = [ExtractedOffer(
                merchant=domain,
                price=0.0,  # signals the caller: no real price available
                url=url,
                login_required=True,
                vat_status=signals["vat_status"],
                has_tier_pricing=signals["has_tier_pricing"],
                min_order_quantity=signals["min_order_quantity"],
                availability=signals["availability"],
                tier_pricing=signals["tier_pricing"],
            )]

        return offers, page_title.strip(), signals

    except asyncio.TimeoutError:
        logger.debug("Timeout fetching %s", url[:80])
        return [], "", signals
    except Exception as e:
        logger.debug("Error fetching %s: %s", url[:80], e)
        return [], "", signals
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


def _generate_query_variants(
    query: str,
    category: str | None = None,
    locale: str | None = None,
    use_templates: bool = False,
) -> list[str]:
    """Generate query variants (original + exact-model fallback + optional
    category-/locale-specific expansions).

    Legacy mode (`use_templates=False`, category=None): exactly two
    variants are produced -- the raw query plus a quoted-model-number
    form. Matches pre-v1.0 behaviour bit-identically (BC for every
    regression test).

    v1.0 mode (`use_templates=True`): after the raw query + quoted-model
    form, inserts a brand-free SKU-only fallback (distinctive tokens
    only, often rank better in SearXNG shopping) THEN the category-
    locale templates from locale.templates (e.g. "{q} Datenblatt" for
    industrial_mro). Capped at 6 variants to keep the discovery fan-out
    bounded.

    Discriminating digit-token rule (same as pre-v1.0): an additional
    quoted-model-number variant is added ONLY when the query contains a
    digit-bearing token of length >= 4 such as "1674FC", "WRL 200.400".
    """
    base = query.strip()
    if not base:
        return []
    variants: list[str] = [base]

    # Locate the most discriminating digit-bearing SKU token (e.g.
    # "1674FC", "WRL 200.400", "5SV1316-6KK16"). Reused below for the
    # quoted-model variant AND the brand-free SKU-only fallback.
    raw_tokens = re.findall(r"[\w.\-]+", base)
    sku_token: str | None = None
    first_token: str | None = None
    if len(raw_tokens) >= 2:
        first_token = raw_tokens[0]
        for tok in raw_tokens:
            if any(c.isdigit() for c in tok) and len(tok) >= 4:
                sku_token = tok
                break

    if sku_token and first_token:
        first_has_digit = any(c.isdigit() for c in first_token)
        if first_token != sku_token and not first_has_digit:
            quoted = f'{first_token} "{sku_token}"'
        else:
            quoted = f'"{sku_token}"'
        if quoted != base and quoted not in variants:
            variants.append(quoted)

    # Legacy mode: exactly two variants, unchanged from pre-v1.0.
    if not use_templates or category is None:
        return variants[:2]

    # v1.0 brand-free SKU-only fallback (inserted BEFORE template
    # expansions so it doesn't get pushed out of the 6-variant cap):
    # B2B-specific SKUs (Siemens 5SV1316-6KK16, Multipower MP26-12,
    # Fluke 1674FC) often rank better in SearXNG's shopping engines
    # WITHOUT the brand prefix, which otherwise over-constrains
    # Google-Shopping's fuzzy matching. The SKU alone is distinctive
    # enough -- if SearXNG finds it, the shop's title still carries
    # the brand, so downstream scoring stays solid.
    if sku_token and len(sku_token) >= 5:
        sku_quoted = f'"{sku_token}"'
        for v in (sku_quoted, sku_token):
            if v and v not in variants:
                variants.append(v)

    # v1.0: add category/locale-specific expansions.
    # Import inline so the module stays importable when locale/ isn't
    # available yet (older pod filesystems during rolling upgrade).
    try:
        from ..locale.templates import expand
        tmpl_variants = expand(base, category=category, locale=locale or "de")
        for t in tmpl_variants:
            if t and t not in variants:
                variants.append(t)
    except Exception as e:  # pragma: no cover
        logger.debug("locale.templates unavailable: %s", e)

    # Keep the discovery fan-out bounded. 6 variants (was 4 pre-SKU-
    # fallback) * 2 SearXNG channels (general + shopping) = 12 parallel
    # SearXNG calls per query, each capped at 25 results -- still
    # comfortably within rate budget.
    return variants[:6]


async def handle_search_prices(
    arguments: dict[str, Any],
    browser_mgr: BrowserManager,
    searxng_client: SearXNGClient,
    serpapi_client: Any,
    search_config: PriceSearchConfig,
    cache: TTLCache,
    brave_client: Any = None,
    serper_client: Any = None,
    apify_client: Any = None,
    llm_validator: Any = None,
) -> dict[str, Any]:
    """Main search pipeline: discovery -> ranking -> detail fetch -> aggregate."""
    query = arguments.get("query", "").strip()
    max_results = min(arguments.get("max_results", 10), 20)
    fetch_details = arguments.get("fetch_details", True)
    response_mode = arguments.get("response_mode", "full")

    if not query or len(query) < 2:
        return tool_error(ErrorCode.INVALID_PARAMETER, "query must be at least 2 characters")

    # ── v1.0 Phase 0: EAN fast-fail ────────────────────────────────────
    # If the query looks like a barcode (8/10/12/13/14 digits with
    # optional prefixes like "ISBN:" / dashes) but its GS1 mod-10 or
    # ISBN mod-11 checksum is invalid, reject immediately. This saves
    # 60-75s of Playwright work on typos.
    if search_config.enable_ean_fastfail:
        try:
            from ..enrichment.ean import detect_code_type, validate_code
            code_kind = detect_code_type(query)
            if code_kind != "unknown" and not validate_code(query):
                logger.info("EAN fastfail: %s failed checksum for %s", code_kind, query)
                return tool_error(
                    ErrorCode.INVALID_PARAMETER,
                    f"Barcode checksum invalid for {code_kind.upper()}: '{query}'. "
                    "Please verify the digits.",
                )
        except Exception as e:  # pragma: no cover -- belt-and-suspenders
            logger.debug("EAN fastfail import/check skipped: %s", e)

    # ── v1.0 Phase 0b: Category classification + locale detection ─────
    # Both are cheap (heuristic only at alpha3 scope). The resolved
    # CategoryProfile is threaded through scoring, penalty, and
    # aggregator resolution so domain overlays + rule_c_fillers apply.
    # When enable_categories=False, profile stays None and every
    # downstream function behaves bit-identically to v2.3.5.
    profile = None
    locale = "de"
    classification = None
    if search_config.enable_categories:
        try:
            from ..categories.classifier_llm import (
                LLMClassifierConfig,
                classify_cascaded,
            )
            from ..categories.registry import CategoryRegistry
            from ..locale.detector import detect_locale

            llm_cfg = LLMClassifierConfig.from_env()
            classification = await classify_cascaded(query, llm_cfg)
            profile = CategoryRegistry.instance().get(classification.category)
            locale = detect_locale(query, fallback="de")
            logger.info(
                "[category] query=%r -> category=%s confidence=%.2f source=%s "
                "locale=%s details=%r",
                query[:80], classification.category, classification.confidence,
                classification.source, locale, classification.details[:60],
            )
        except Exception as e:
            logger.warning("Category classification failed, falling back to default: %s", e)
            profile = None
            locale = "de"

    # ── v1.0 Phase 0c: Free EAN/ISBN enrichment (OpenFoodFacts + Wikidata)
    # When the query is a valid barcode, try to resolve it to a brand +
    # product name via free public providers. This:
    #   1) serves as a deterministic category override for ISBNs (always
    #      route to book_media, even if the heuristic classifier picked
    #      something else);
    #   2) injects brand+product+quantity tokens as a BONUS query variant
    #      later, which opens the long tail of shops that don't index the
    #      raw GTIN.
    # Fail-silent: 5 s total budget, no exceptions propagate.
    enrichment_hint: str = ""
    if search_config.enable_ean_fastfail:
        try:
            from ..enrichment.ean import detect_code_type, normalize_ean, validate_code
            code_kind = detect_code_type(query)
            if code_kind != "unknown" and validate_code(query):
                from ..enrichment.dispatcher import enrich
                normalized = normalize_ean(query) or query
                enrichment = await enrich(None, normalized, total_timeout=5.0)
                if enrichment is not None:
                    enrichment_hint = enrichment.to_query_hint()
                    logger.info(
                        "[enrichment] %s -> brand=%r product=%r source=%s hint=%r",
                        normalized, enrichment.brand, enrichment.product_name,
                        enrichment.source, enrichment_hint[:80],
                    )
                    # ISBN-branch: force book_media profile regardless of
                    # the classifier verdict. Publishers are rarely in our
                    # brand dictionary, so the heuristic stage often misses
                    # them.
                    if code_kind in ("isbn10", "isbn13") and enrichment.category_hint:
                        try:
                            from ..categories.registry import CategoryRegistry
                            profile = CategoryRegistry.instance().get(enrichment.category_hint)
                            logger.info(
                                "[enrichment] ISBN detected -> profile overridden to %s",
                                enrichment.category_hint,
                            )
                        except Exception as exc:
                            logger.debug("profile override failed: %s", exc)
                    # Food/cosmetics category override when classifier was
                    # unsure (confidence < 0.5) but OpenFoodFacts is
                    # authoritative.
                    elif (
                        enrichment.category_hint
                        and classification is not None
                        and getattr(classification, "confidence", 1.0) < 0.5
                    ):
                        try:
                            from ..categories.registry import CategoryRegistry
                            profile = CategoryRegistry.instance().get(enrichment.category_hint)
                            logger.info(
                                "[enrichment] low-conf classifier overridden -> %s",
                                enrichment.category_hint,
                            )
                        except Exception as exc:
                            logger.debug("profile override failed: %s", exc)
        except Exception as exc:  # pragma: no cover
            logger.debug("enrichment skipped: %s", exc)

    # Cache key includes the resolved category so two queries that look
    # similar but classify differently don't collide.
    _cat_key = profile.key if profile else "none"
    cache_key = f"search:{query.lower()}:{fetch_details}:{max_results}:{_cat_key}:{locale}"
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        logger.info("Cache hit for query: %s", query[:60])
        return cached_result

    start_time = time.monotonic()
    search_type = _detect_search_type(query)
    sources_queried: list[str] = []
    all_offers: list[dict[str, Any]] = []
    timing: dict[str, int] = {}

    # ── Phase 1: Discovery (all sources x query variants in parallel) ────
    #
    # Sources run concurrently:
    #   - SearXNG general + SearXNG shopping (Google Shopping tiles)
    #   - SerpAPI Google Shopping  (if PRICE_SERPAPI_KEY set)
    #   - Brave Search             (if PRICE_BRAVE_API_KEY set)
    #   - Serper.dev Shopping      (if PRICE_SERPER_API_KEY set)
    #   - Apify Google Shopping    (if PRICE_APIFY_TOKEN set)
    # All are called with 1-3 query variants generated heuristically
    # (brand-free, quoted model-number) to broaden distributor coverage.

    query_variants = _generate_query_variants(
        query,
        category=(profile.key if profile else None),
        locale=locale,
        use_templates=search_config.enable_locale_templates,
    )
    # Enrichment-backed variant: prepend the resolved brand+product+quantity
    # string so SearXNG sees both the raw GTIN AND the human-readable
    # identification. Many long-tail shops don't index barcodes, so this
    # is often the difference between zero and several offers.
    if enrichment_hint and enrichment_hint.lower() not in (v.lower() for v in query_variants):
        # Insert after raw+quoted but before templates; keeps 6-cap.
        insert_at = min(2, len(query_variants))
        query_variants.insert(insert_at, enrichment_hint)
        query_variants = query_variants[:6]
    logger.info("Query variants for '%s': %s", query[:60], query_variants)

    discovery_tasks: list[Any] = []
    task_labels: list[str] = []

    # SearXNG general+shopping per variant
    async def _searxng_variant(q: str) -> list[SearchResult]:
        try:
            return await searxng_client.search_both(q, max_results=25)
        except Exception as e:
            logger.debug("SearXNG search_both failed for %s: %s", q[:40], e)
            try:
                return await searxng_client.search(q, max_results=25)
            except Exception:
                return []

    for qv in query_variants:
        discovery_tasks.append(_searxng_variant(qv))
        task_labels.append(f"searxng:{qv[:40]}")

    # SerpAPI (Google Shopping) - run only on primary query, it's paid
    async def _serpapi() -> list[Any]:
        if serpapi_client is None or not getattr(serpapi_client, "available", False):
            return []
        try:
            t0 = time.monotonic()
            results = await serpapi_client.search(query, max_results=15)
            timing["serpapi_ms"] = int((time.monotonic() - t0) * 1000)
            return results
        except Exception as e:
            logger.debug("SerpAPI failed: %s", e)
            return []

    discovery_tasks.append(_serpapi())
    task_labels.append("serpapi")

    # Brave Search - per variant (free tier 2000/month)
    async def _brave(q: str) -> list[Any]:
        if brave_client is None or not getattr(brave_client, "available", False):
            return []
        try:
            return await brave_client.search(q, max_results=15)
        except Exception as e:
            logger.debug("Brave search failed: %s", e)
            return []

    for qv in query_variants:
        discovery_tasks.append(_brave(qv))
        task_labels.append(f"brave:{qv[:40]}")

    # Serper.dev Google Shopping - only primary query (limited free tier)
    async def _serper() -> list[Any]:
        if serper_client is None or not getattr(serper_client, "available", False):
            return []
        try:
            t0 = time.monotonic()
            results = await serper_client.search(query, max_results=15)
            timing["serper_ms"] = int((time.monotonic() - t0) * 1000)
            return results
        except Exception as e:
            logger.debug("Serper search failed: %s", e)
            return []

    discovery_tasks.append(_serper())
    task_labels.append("serper")

    # Apify Google Shopping Actor - only primary (slowest source)
    async def _apify() -> list[Any]:
        if apify_client is None or not getattr(apify_client, "available", False):
            return []
        try:
            t0 = time.monotonic()
            results = await apify_client.search(query, max_results=15)
            timing["apify_ms"] = int((time.monotonic() - t0) * 1000)
            return results
        except Exception as e:
            logger.debug("Apify search failed: %s", e)
            return []

    discovery_tasks.append(_apify())
    task_labels.append("apify")

    t_disc = time.monotonic()
    disco_results = await asyncio.gather(*discovery_tasks, return_exceptions=True)
    timing["discovery_ms"] = int((time.monotonic() - t_disc) * 1000)

    # Collect SearXNG results (dedup by URL across variants)
    searxng_results: list[SearchResult] = []
    seen_urls: set[str] = set()
    brave_results: list[Any] = []
    serpapi_results: list[Any] = []
    serper_results: list[Any] = []
    apify_results: list[Any] = []

    for label, res in zip(task_labels, disco_results):
        if isinstance(res, Exception):
            logger.debug("Discovery task %s raised: %s", label, res)
            continue
        if not res:
            continue
        if label.startswith("searxng:"):
            for r in res:
                url = getattr(r, "url", None)
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    searxng_results.append(r)
        elif label.startswith("brave:"):
            for r in res:
                url = getattr(r, "url", None)
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    brave_results.append(r)
        elif label == "serpapi":
            serpapi_results = res
        elif label == "serper":
            serper_results = res
        elif label == "apify":
            apify_results = res

    timing["searxng_ms"] = timing.get("discovery_ms", 0)  # legacy field
    sources_queried.append("searxng")
    if brave_results:
        sources_queried.append("brave")
    if serpapi_results:
        sources_queried.append("serpapi")
    if serper_results:
        sources_queried.append("serper")
    if apify_results:
        sources_queried.append("apify")

    logger.info(
        "Discovery: searxng=%d brave=%d serpapi=%d serper=%d apify=%d",
        len(searxng_results), len(brave_results), len(serpapi_results),
        len(serper_results), len(apify_results),
    )

    # B2B multi-query retry:
    # If the first query returned fewer than 3 URLs on known trusted
    # domains, and the query looks like a B2B article (manufacturer
    # name + alphanumeric article code pattern), issue one additional
    # SearXNG query with B2B-oriented terms. This targets cases like
    # "Niedax WRL 200.400 F" where consumer-oriented "Preis kaufen"
    # fails but "Datenblatt Distributor" hits catalog pages.
    trusted_hits = sum(
        1 for r in searxng_results
        if r.domain and _is_trusted_domain(r.domain)
    )
    # B2B gating threshold: trigger hints + retry when trusted coverage
    # is moderate-or-thin (<4). A pure-consumer query like "Sony WH-
    # 1000XM5" typically returns 5+ trusted aggregators (idealo,
    # geizhals, billiger, guenstiger, amazon) -- no hints needed.
    # B2B articles (Niedax, OBO, FLUKE, DOTLUX) usually have 1-3
    # trusted hits, which is exactly when Sonepar/Rexel/Mercateo
    # cross-checks add the most procurement value.
    looks_b2b = (
        search_type == "name"
        and bool(re.search(r"[A-Za-z]+.*\d[\w\-.]*", query))
        and trusted_hits < 4
    )
    if looks_b2b:
        try:
            t_retry = time.monotonic()
            b2b_query = f"{query} Datenblatt Artikelnummer"
            retry_results = await searxng_client.search(
                b2b_query,
                max_results=20,
                append_price_term=False,
            )
            timing["searxng_retry_ms"] = int((time.monotonic() - t_retry) * 1000)
            sources_queried.append("searxng-b2b-retry")
            logger.info(
                "B2B retry: +%d results for '%s' (first query had %d trusted hits)",
                len(retry_results), b2b_query[:60], trusted_hits,
            )
            # Merge, letting the deduplicator dedup by URL.
            existing_urls = {r.url for r in searxng_results}
            for r in retry_results:
                if r.url and r.url not in existing_urls:
                    searxng_results.append(r)
        except Exception as e:
            logger.debug("B2B retry failed: %s", e)

        # Also surface top German B2B wholesalers (Sonepar, Rexel,
        # Mercateo) as "B2B hints" in the login-gated block. These
        # structurally don't publish prices but holding a quote from
        # them is standard procurement practice. We include clickable
        # search URLs so the user can verify manually with their own
        # customer credentials.
        b2b_hints = _build_b2b_distributor_hints(query)
        # Only keep hints whose domain isn't already represented in
        # real SearXNG results (avoid duplicating an actually-fetched
        # Sonepar page, rare but possible).
        existing_domains = {r.domain for r in searxng_results if r.domain}
        for hint in b2b_hints:
            if hint["merchant"] not in existing_domains:
                all_offers.append(hint)
        logger.info(
            "B2B hints: added %d distributor shortcut(s) (sonepar/rexel/mercateo)",
            sum(1 for h in b2b_hints if h["merchant"] not in existing_domains),
        )

    # Extract inline offers from SearXNG results.
    # Apply match_confidence against the search result's title so that
    # snippet-level prices for UNRELATED products (Geizhals "related
    # listings", aggregator teaser prices) don't poison the anchor.
    # Without this, a snippet like "51,95 EUR ... aehnliche Artikel"
    # would be indistinguishable from the real product price.
    for result in searxng_results:
        if result.inline_price is not None:
            # Skip inline prices whose URL is an aggregator SERP
            # (e.g. geizhals.de/?fs=...). Those snippet prices are
            # from whatever the aggregator listed on top, not the
            # target SKU.
            from ..price_extractor import _is_aggregator_search_url
            if _is_aggregator_search_url(result.url):
                continue
            match_conf = _product_match_confidence(
                query, result.title, profile=profile,
                apply_antilex=search_config.enable_antilex_gate,
            )
            offer = {
                "merchant": result.domain or "unknown",
                "price": result.inline_price,
                "shipping_cost": 0.0,
                "total_price": result.inline_price,
                "currency": "EUR",
                "url": result.url,
                "source": "searxng",
                "availability": "",
            }
            if match_conf:
                offer["match_confidence"] = match_conf
            all_offers.append(offer)

    # Extract offers from SerpAPI results
    for result in serpapi_results:
        if result.price is not None:
            all_offers.append({
                "merchant": result.merchant or "unknown",
                "price": result.price,
                "shipping_cost": 0.0,
                "total_price": result.price,
                "currency": "EUR",
                "url": result.url,
                "source": "serpapi",
                "availability": "",
            })

    # Extract inline offers from Brave / Serper / Apify. These come from
    # structured shopping APIs or in-snippet prices. Apply the same
    # match-confidence gate so wrong-SKU tiles don't poison the anchor.
    def _ingest_inline(results_list: list[Any], source_label: str) -> None:
        from ..price_extractor import _is_aggregator_search_url
        for r in results_list:
            price = getattr(r, "inline_price", None)
            url = getattr(r, "url", "")
            if price is None or not url:
                continue
            if _is_aggregator_search_url(url):
                continue
            title = getattr(r, "title", "") or ""
            match_conf = _product_match_confidence(
                query, title, profile=profile,
                apply_antilex=search_config.enable_antilex_gate,
            )
            offer: dict[str, Any] = {
                "merchant": getattr(r, "domain", "") or "unknown",
                "price": price,
                "shipping_cost": 0.0,
                "total_price": price,
                "currency": "EUR",
                "url": url,
                "source": source_label,
                "availability": "",
            }
            if match_conf:
                offer["match_confidence"] = match_conf
            all_offers.append(offer)

    _ingest_inline(brave_results, "brave")
    _ingest_inline(serper_results, "serper")
    _ingest_inline(apify_results, "apify")

    # ── Phase 2: URL ranking + deduplication ─────────────────────────────

    if fetch_details:
        # Collect candidate URLs from all discovery sources
        candidate_urls = [r.url for r in searxng_results if r.url]
        candidate_urls.extend(r.url for r in serpapi_results if r.url)
        candidate_urls.extend(
            getattr(r, "url", "") for r in brave_results
            if getattr(r, "url", None)
        )
        candidate_urls.extend(
            getattr(r, "url", "") for r in serper_results
            if getattr(r, "url", None)
        )
        candidate_urls.extend(
            getattr(r, "url", "") for r in apify_results
            if getattr(r, "url", None)
        )

        # Deduplicate, score (with query + SearXNG-title context), and
        # drop blacklisted URLs. Title + snippet from the SearXNG
        # result feeds the variant detectors (fashion_size/color catch
        # mismatches encoded only in the title, not the URL path).
        candidate_urls = _deduplicate_urls(candidate_urls)
        url_context: dict[str, str] = {}
        for r in searxng_results:
            if r.url and r.url not in url_context:
                title = getattr(r, "title", "") or ""
                snippet = getattr(r, "snippet", "") or getattr(r, "content", "") or ""
                url_context[r.url] = f"{title} {snippet}".strip()
        for r_list in (serpapi_results, brave_results, serper_results, apify_results):
            for r in r_list:
                u = getattr(r, "url", "") or ""
                if u and u not in url_context:
                    t = getattr(r, "title", "") or ""
                    s = getattr(r, "snippet", "") or getattr(r, "content", "") or ""
                    url_context[u] = f"{t} {s}".strip()
        scored = [
            (url, _score_url(url, query, profile=profile,
                             context=url_context.get(url, "")))
            for url in candidate_urls
        ]
        scored = [(url, s) for url, s in scored if s >= 0]

        # Active-coverage injection: for every major aggregator
        # (Idealo, Geizhals, Conrad) that SearXNG did NOT surface
        # with a direct URL, inject the aggregator's own search URL.
        # Those URLs redirect to the product page when the SKU is
        # indexed and return an empty results page otherwise. Gives
        # us a deterministic fallback when Google/Bing don't rank
        # the product page high enough.
        #
        # Gating logic (two tiers):
        #   (a) Always inject when the query contains a discriminating
        #       model-number token (digits, length >= 4). In that case
        #       idealo/geizhals almost certainly have an OffersOfProduct
        #       page with 20+ offers we can harvest in one fetch -- the
        #       single richest source of B2B coverage we have. Skipping
        #       this would leave us with only 5-8 offers when 20+ are
        #       a single click away.
        #   (b) Thin-pool fallback: for purely descriptive queries
        #       ("Dell Monitor 27 Zoll"), inject only if SearXNG's
        #       candidate pool is short on strong candidates.
        #
        # IMPORTANT: injected URLs are tracked separately and appended
        # AFTER the scored top-N selection so they do NOT evict trusted
        # direct-product distributors (tandmore.de, schmitztools.com,
        # ...) from the fetch pool. They are pure additions.
        strong_candidates = [(u, s) for u, s in scored if s >= 60]
        existing_families = {
            _domain_family(_domain_of(u))
            for u, _ in scored
            if _domain_of(u)
        }
        q_encoded = quote_plus(query.strip())
        _thin_pool_threshold = max(1, search_config.max_detail_urls // 2)
        # Detect model-number token: word containing digit(s), length >= 4.
        # Examples: "1674FC" (6), "200.400" (7), "WH-1000XM5" (9),
        # "GBH 2-28" -> token "2-28" (4). A plain brand like "Dell"
        # doesn't trigger this.
        has_model_token = any(
            len(t) >= 4 and any(c.isdigit() for c in t)
            for t in re.findall(r"[\w.\-]+", query)
        )
        should_inject = has_model_token or len(strong_candidates) < _thin_pool_threshold
        injected_agg_urls: list[str] = []
        if should_inject:
            reason = (
                "model-number query"
                if has_model_token
                else f"thin pool ({len(strong_candidates)} strong < {_thin_pool_threshold})"
            )
            for agg_domain, template in _AGGREGATOR_SEARCH_TEMPLATES:
                if _domain_family(agg_domain) in existing_families:
                    continue
                search_url = template.format(q=q_encoded)
                injected_agg_urls.append(search_url)
                logger.info(
                    "Active coverage (%s): injected %s search URL",
                    reason, agg_domain,
                )
        else:
            logger.info(
                "Active coverage: skipped -- %d strong candidates "
                ">= threshold %d (no model-number token)",
                len(strong_candidates), _thin_pool_threshold,
            )

        scored.sort(key=lambda x: x[1], reverse=True)
        # Enforce domain diversity at fetch time -- prevents a single
        # high-scoring domain (e.g. idealo.de) from consuming all slots.
        top_urls = _select_diverse_urls(
            scored,
            max_total=search_config.max_detail_urls,
            max_per_domain=search_config.max_detail_urls_per_domain,
        )
        # Append injected aggregator URLs on TOP of the selected set.
        # They are deterministic, high-signal, and must not compete
        # with trusted direct-product distributors for fetch slots.
        top_urls_families = {
            _domain_family(_domain_of(u)) for u in top_urls
        }
        for inj_url in injected_agg_urls:
            fam = _domain_family(_domain_of(inj_url))
            if fam in top_urls_families:
                continue
            top_urls.append(inj_url)
            top_urls_families.add(fam)
        if logger.isEnabledFor(logging.INFO):
            logger.info(
                "Top URLs (%d) selected for Playwright: %s",
                len(top_urls),
                [u[:90] for u in top_urls],
            )

        # ── Phase 3: Playwright detail fetch (parallel) ──────────────────

        if top_urls:
            elapsed = time.monotonic() - start_time
            remaining = max(5, search_config.total_timeout_seconds - elapsed)
            per_url_timeout = min(
                search_config.detail_timeout_seconds,
                int(remaining / min(len(top_urls), search_config.concurrent_fetches)),
            )

            t0 = time.monotonic()
            semaphore = asyncio.Semaphore(search_config.concurrent_fetches)

            async def fetch_with_semaphore(
                url: str,
            ) -> tuple[list[ExtractedOffer], str, dict[str, Any]]:
                async with semaphore:
                    return await asyncio.wait_for(
                        _fetch_detail_page(
                            url, browser_mgr, per_url_timeout,
                            query=query, profile=profile,
                        ),
                        timeout=per_url_timeout + 2,
                    )

            tasks = [fetch_with_semaphore(url) for url in top_urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for url, result in zip(top_urls, results):
                if isinstance(result, Exception):
                    logger.info("Detail fetch RAISED for %s: %s", url[:80], result)
                    continue
                extracted_offers, page_title, _signals = result
                match_confidence = _product_match_confidence(
                    query, page_title, profile=profile,
                    apply_antilex=search_config.enable_antilex_gate,
                )
                logger.info(
                    "Detail fetch result: url=%s offers=%d title=%r mc=%s",
                    url[:80], len(extracted_offers), (page_title or "")[:60],
                    match_confidence,
                )
                for offer in extracted_offers:
                    offer_dict = offer.to_dict()
                    offer_dict["source"] = "playwright"
                    if page_title:
                        offer_dict["page_title"] = page_title[:180]
                    if match_confidence:
                        offer_dict["match_confidence"] = match_confidence
                    all_offers.append(offer_dict)

            timing["detail_fetch_ms"] = int((time.monotonic() - t0) * 1000)

    # ── Phase 3.5: EAN cross-match ────────────────────────────────────────
    #
    # If any Playwright fetch yielded an EAN/GTIN-13, re-query discovery
    # sources with the EAN as the search term. Offers landed via the
    # EAN search are tagged with match_confidence="exact" -- they share
    # a globally unique product identifier, so SKU match is guaranteed.
    eans_found: set[str] = set()
    for offer in all_offers:
        ean = offer.get("ean") or ""
        if ean and len(ean) in (8, 12, 13, 14):
            eans_found.add(ean)

    # Skip cross-match if the user already queried by EAN (redundant).
    if eans_found and search_type != "ean" and len(eans_found) <= 3:
        t_ean = time.monotonic()
        logger.info(
            "EAN cross-match: re-querying with %d EAN(s): %s",
            len(eans_found), list(eans_found)[:3],
        )
        ean_tasks: list[Any] = []
        for ean in eans_found:
            async def _ean_searxng(e: str = ean) -> list[SearchResult]:
                try:
                    return await searxng_client.search_both(e, max_results=15)
                except Exception:
                    try:
                        return await searxng_client.search(e, max_results=15, append_price_term=False)
                    except Exception:
                        return []
            ean_tasks.append(_ean_searxng())
            if brave_client is not None and getattr(brave_client, "available", False):
                async def _ean_brave(e: str = ean) -> list[Any]:
                    try:
                        return await brave_client.search(e, max_results=10)
                    except Exception:
                        return []
                ean_tasks.append(_ean_brave())
            if serper_client is not None and getattr(serper_client, "available", False):
                async def _ean_serper(e: str = ean) -> list[Any]:
                    try:
                        return await serper_client.search(e, max_results=10)
                    except Exception:
                        return []
                ean_tasks.append(_ean_serper())

        ean_results = await asyncio.gather(*ean_tasks, return_exceptions=True)
        ean_seen_urls = {o.get("url") for o in all_offers}

        from ..price_extractor import _is_aggregator_search_url
        for res in ean_results:
            if isinstance(res, Exception) or not res:
                continue
            for r in res:
                price = getattr(r, "inline_price", None) or getattr(r, "price", None)
                url = getattr(r, "url", "")
                if not url or price is None or url in ean_seen_urls:
                    continue
                if _is_aggregator_search_url(url):
                    continue
                ean_seen_urls.add(url)
                all_offers.append({
                    "merchant": getattr(r, "domain", "") or getattr(r, "merchant", "") or "unknown",
                    "price": float(price),
                    "shipping_cost": 0.0,
                    "total_price": float(price),
                    "currency": "EUR",
                    "url": url,
                    "source": "ean-crossmatch",
                    "availability": "",
                    "match_confidence": "exact",
                    "ean": list(eans_found)[0],
                })

        timing["ean_crossmatch_ms"] = int((time.monotonic() - t_ean) * 1000)
        sources_queried.append("ean-crossmatch")

    # ── Phase 4: Aggregate + format ──────────────────────────────────────

    # Split out login-gated placeholder offers so they don't pollute
    # sorting, outlier detection, and insights.
    real_offers_raw: list[dict[str, Any]] = []
    login_gated_offers: list[dict[str, Any]] = []
    for offer in all_offers:
        if offer.get("login_required") and not offer.get("price"):
            login_gated_offers.append(offer)
        else:
            real_offers_raw.append(offer)

    # Deduplicate by merchant+price (real offers)
    seen: set[str] = set()
    unique_offers: list[dict[str, Any]] = []
    for offer in real_offers_raw:
        key = f"{offer['merchant'].lower()}:{offer['price']:.2f}"
        if key not in seen:
            seen.add(key)
            unique_offers.append(offer)

    # Deduplicate login-gated by merchant (one note per merchant is enough)
    seen_login: set[str] = set()
    dedup_login: list[dict[str, Any]] = []
    for offer in login_gated_offers:
        merchant = offer.get("merchant", "").lower()
        if merchant and merchant not in seen_login:
            seen_login.add(merchant)
            dedup_login.append(offer)
    login_gated_offers = dedup_login[:5]  # cap visible login notes

    # Sort real offers by (confidence_rank, total_price). Low-confidence
    # offers (wrong variant, brand-only match, SERP scrapes) are pushed
    # to the end so a falsely-cheap ebay listing or wrong-SKU aggregator
    # page cannot rank above a correct distributor offer. Offers without
    # a valid price are sorted last regardless of confidence.
    def _confidence_rank(offer: dict[str, Any]) -> int:
        conf = (offer.get("match_confidence") or "").lower()
        if conf == "exact":     # EAN-matched -> guaranteed same SKU
            return 0
        if conf == "high":
            return 1
        if conf == "medium":
            return 2
        if conf == "low":
            return 4
        return 3  # unknown / not judged

    def _price_key(offer: dict[str, Any]) -> float:
        price = offer.get("total_price")
        if price is None or price <= 0:
            return float("inf")
        return float(price)

    unique_offers.sort(key=lambda o: (_confidence_rank(o), _price_key(o)))

    # Enforce per-domain cap so the final list shows multiple merchants
    # rather than 12 sub-listings from the same aggregator.
    unique_offers = _enforce_per_domain_cap(
        unique_offers,
        max_per_domain=search_config.max_offers_per_domain,
    )

    # Limit results
    unique_offers = unique_offers[:max_results]

    # ── Phase 4.5: LLM validator gate ─────────────────────────────────────
    # A final LLM pass inspects the top candidates and flags ones that
    # clearly do not match the query (wrong variant, accessory, bundle).
    # Flagged offers have match_confidence forced to "low" so the next
    # sort pushes them to the end. Validated "yes" offers are promoted
    # to "high". Skipped silently when validator not configured.
    if llm_validator is not None and getattr(llm_validator, "available", False):
        t_val = time.monotonic()
        try:
            unique_offers = await llm_validator.validate(query, unique_offers)
            timing["llm_validator_ms"] = int((time.monotonic() - t_val) * 1000)
            sources_queried.append("llm-validator")
            # Re-sort after the validator may have rewritten confidences.
            unique_offers.sort(key=lambda o: (_confidence_rank(o), _price_key(o)))
        except Exception as e:
            logger.warning("LLM validator raised, skipping: %s", e)

    # v1.0 composite_confidence: combine match_confidence with
    # price_source weight. Procurement reports use this to rank trust.
    for o in unique_offers:
        o["composite_confidence"] = _composite_confidence(
            o.get("match_confidence", ""),
            o.get("price_source", ""),
        )

    # Flag outliers (annotates each offer in-place with is_outlier/outlier_reason)
    outliers_flagged, anchor_used = _flag_outliers(unique_offers, profile=profile)
    if outliers_flagged > 0 and anchor_used is not None:
        logger.info(
            "Flagged %d/%d offers as outliers (anchor=%.2f EUR)",
            outliers_flagged, len(unique_offers), anchor_used,
        )

    # v1.0 beta1: Record learned-domain stats. Only for high-confidence,
    # non-outlier offers -- noise in wins corrupts the overlay.
    if (search_config.enable_domain_discovery
            and profile is not None and profile.key != "default"):
        try:
            from ..discovery.domain_stats import instance as _stats_instance
            store = _stats_instance()
            seen_domains: set[str] = set()
            for o in unique_offers:
                if o.get("is_outlier"):
                    continue
                if (o.get("match_confidence") or "") not in ("exact", "high"):
                    continue
                d = _domain_of(o.get("url") or o.get("merchant") or "")
                if not d or d in seen_domains:
                    continue
                seen_domains.add(d)
                store.record_hit(
                    domain=d,
                    category=profile.key,
                    match_conf=o.get("composite_confidence", 0.8),
                )
        except Exception as e:  # pragma: no cover
            logger.debug("domain_stats write skipped: %s", e)

    # Compute insights (uses outlier flags to also produce filtered values)
    insights = _compute_insights(unique_offers) if len(unique_offers) >= 2 else {}

    # Surface login-gated merchants explicitly so the LLM can cite them
    if login_gated_offers:
        logger.info(
            "Login-gated: %d merchant(s) returned no public price",
            len(login_gated_offers),
        )

    total_ms = int((time.monotonic() - start_time) * 1000)
    timing["total_ms"] = total_ms

    result_data = {
        "query": query,
        "search_type": search_type,
        "offers": unique_offers,
        "insights": insights,
        "sources_queried": sources_queried,
        "timing": timing,
    }
    if login_gated_offers:
        # Surface login-gated merchants so the LLM can cite them
        # rather than silently omitting them. Two variants:
        #   - source="b2b-hint"  -> we did NOT probe, just surfaced
        #     a clickable search URL for the big DE wholesalers.
        #     Note reads "Typische B2B-Quelle, manuell pruefen".
        #   - source="playwright" (or other) -> we actually fetched
        #     the URL and the page was login-gated.
        #     Note reads "Preis nach Login".
        result_data["login_gated_merchants"] = [
            {
                "merchant": o.get("merchant", ""),
                "url": o.get("url", ""),
                "vat_status": o.get("vat_status", ""),
                "has_tier_pricing": o.get("has_tier_pricing", False),
                "source": o.get("source", "fetched"),
                "note": (
                    "Typische B2B-Quelle -- manuell mit Kundenlogin pruefen"
                    if o.get("source") == "b2b-hint"
                    else "Preis nach Login / B2B-Konditionen"
                ),
            }
            for o in login_gated_offers
        ]

    # Build MCP response
    if not unique_offers:
        response_text = (
            f"No prices found for: {query}\n\n"
            f"Searched: {', '.join(sources_queried)}\n"
            f"Time: {total_ms}ms"
        )
        result = {"content": [{"type": "text", "text": response_text}]}
    elif response_mode == "summary":
        summary = (
            f"Found {len(unique_offers)} offers for: {query}\n"
            f"Price range: {unique_offers[0]['price']:.2f} - {unique_offers[-1]['price']:.2f} EUR\n"
            f"Sources: {', '.join(sources_queried)} | Time: {total_ms}ms"
        )
        result = {"content": [{"type": "text", "text": summary}]}
    else:
        import json
        result = {
            "content": [{
                "type": "text",
                "text": json.dumps(result_data, ensure_ascii=False, indent=2),
            }],
        }

    # Cache the result
    cache.set(cache_key, result)
    return result
