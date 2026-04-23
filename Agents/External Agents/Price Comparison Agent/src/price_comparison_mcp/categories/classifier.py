"""Category classifier -- Stage 1 heuristics (alpha2 scope).

The full plan calls for a three-stage cascade:

  Stage 1  Heuristics (this module, ~0ms)
  Stage 2  EAN enrichment (alpha3, via enrichment/)
  Stage 3  LLM fallback (alpha3, via LiteLLM)

Alpha2 ships only Stage 1. When no heuristic fires, the classifier
returns `default` and upstream callers get the v2.3.5 scoring behaviour
unchanged -- zero-regression promise.

Design:
  - All heuristics are pure functions.
  - Classifier is idempotent and allocation-light; safe to call on every
    query without caching for now (caching moves to a TTL layer in alpha3
    once LLM calls are added).
  - `ClassificationResult.confidence in [0.0, 1.0]` is the classifier's
    self-reported certainty. Callers may gate on >= 0.6 before applying
    a non-default profile, to avoid aggressive miscategorisation.

Heuristic signals (in priority order):

  1. Barcode-shape hints
     - `ean_prefix_kind("isbn")`  -> book_media       confidence=0.99
     - `ean_prefix_kind("issn")`  -> book_media       confidence=0.95
     - Valid GTIN + brand-map hit -> brand's category confidence=0.85

  2. Explicit code-pattern hints
     - ISBN-10/13 valid           -> book_media       confidence=0.99
     - CAS number pattern         -> chemicals_lab    confidence=0.95
     - Schuko / regional electrical suffixes -> electronics  confidence=0.75

  3. Brand-name lookup in query
     - Bosch Professional / Fluke / OBO ... -> industrial_mro / tools_hardware
     - Nike / Adidas / Zalando-style brands -> fashion_apparel
     - LEGO / Playmobil ...       -> toys_hobby
     - Sigma-Aldrich / Merck      -> chemicals_lab
     - ... full map in _BRAND_HINTS

  4. Category keywords in query
     - "Kabelschelle" / "Netzteil"    -> industrial_mro
     - "Bohrhammer" / "Hammer"        -> tools_hardware
     - "Schuh" / "Kleid" / "Jeans"    -> fashion_apparel
     - "Buch" / "Roman" / "Taschenbuch" -> book_media
     - "Wein" / "Bier" / "Kaffee"     -> food_beverage
     - ...

If none match: return `default` with confidence=0.0.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..enrichment.ean import (
    detect_code_type,
    ean_prefix_kind,
    validate_code,
)


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    confidence: float           # 0.0 .. 1.0
    source: str                 # "heuristic:barcode" / "heuristic:brand" / ...
    details: str = ""           # human-readable explanation


# -----------------------------------------------------------------------------
# Brand -> category map. Kept small on purpose; breadth comes from
# category keywords (next layer). Extend as queries reveal gaps.
# -----------------------------------------------------------------------------

_BRAND_HINTS: dict[str, str] = {
    # --- industrial_mro / tools_hardware ---
    "bosch professional": "tools_hardware",
    "gbh": "tools_hardware",           # Bosch GBH ... tool family code
    "gsr": "tools_hardware",           # Bosch GSR ... cordless drills
    "makita": "tools_hardware",
    "metabo": "tools_hardware",
    "hilti": "tools_hardware",
    "dewalt": "tools_hardware",
    "milwaukee": "tools_hardware",
    "fein": "tools_hardware",
    "wuerth": "tools_hardware",
    "fluke": "industrial_mro",         # test equipment
    "keysight": "industrial_mro",
    "hioki": "industrial_mro",
    "obo bettermann": "industrial_mro",
    "niedax": "industrial_mro",
    "wago": "industrial_mro",
    "gira": "industrial_mro",
    "merten": "industrial_mro",
    "weidmueller": "industrial_mro",
    "phoenix contact": "industrial_mro",
    "siemens logo": "industrial_mro",
    "abb": "industrial_mro",
    "schneider electric": "industrial_mro",
    "eaton": "industrial_mro",
    "metz connect": "industrial_mro",
    "ledvance": "industrial_mro",
    "osram": "electronics",            # mostly consumer lighting now
    "dotlux": "industrial_mro",

    # --- electronics (consumer) ---
    "sony": "electronics",
    "samsung galaxy": "electronics",
    "apple": "electronics",
    "iphone": "electronics",
    "ipad": "electronics",
    "macbook": "electronics",
    "lenovo thinkpad": "electronics",
    "huawei": "electronics",
    "xiaomi": "electronics",
    "jbl": "electronics",
    "bose": "electronics",
    "sennheiser": "electronics",
    "nikon": "electronics",
    "canon eos": "electronics",
    "logitech": "electronics",

    # --- fashion_apparel ---
    "nike": "fashion_apparel",
    "adidas": "fashion_apparel",
    "puma": "fashion_apparel",
    "new balance": "fashion_apparel",
    "zalando": "fashion_apparel",
    "levi": "fashion_apparel",
    "hugo boss": "fashion_apparel",
    "tommy hilfiger": "fashion_apparel",
    "ralph lauren": "fashion_apparel",

    # --- toys_hobby ---
    "lego": "toys_hobby",
    "playmobil": "toys_hobby",
    "ravensburger": "toys_hobby",
    "hasbro": "toys_hobby",
    "mattel": "toys_hobby",

    # --- book_media ---
    "thalia": "book_media",
    "hugendubel": "book_media",
    "suhrkamp": "book_media",
    "c.h. beck": "book_media",

    # --- food_beverage ---
    "chateau": "food_beverage",        # wine
    "rothaus": "food_beverage",
    "nespresso": "food_beverage",
    "tchibo": "food_beverage",
    "lavazza": "food_beverage",

    # --- automotive ---
    "bosch mobility": "automotive",
    "continental": "automotive",
    "bilstein": "automotive",
    "sachs": "automotive",
    "febi bilstein": "automotive",

    # --- chemicals_lab ---
    "sigma-aldrich": "chemicals_lab",
    "sigma aldrich": "chemicals_lab",
    "merck millipore": "chemicals_lab",
    "carl roth": "chemicals_lab",
    "vwr": "chemicals_lab",
    "thermo fisher": "chemicals_lab",

    # --- cosmetic_pharma ---
    "loreal": "cosmetic_pharma",
    "l'oreal": "cosmetic_pharma",
    "nivea": "cosmetic_pharma",
    "dove": "cosmetic_pharma",
    "ratiopharm": "cosmetic_pharma",
    "hexal": "cosmetic_pharma",

    # --- office_supplies ---
    "leitz": "office_supplies",
    "staedtler": "office_supplies",
    "lamy": "office_supplies",
    "herlitz": "office_supplies",
    "faber-castell": "office_supplies",
    "faber castell": "office_supplies",

    # --- sports_outdoor ---
    "decathlon": "sports_outdoor",
    "trek bikes": "sports_outdoor",
    "specialized": "sports_outdoor",
    "cube bikes": "sports_outdoor",
    "canyon bicycles": "sports_outdoor",

    # --- home_garden ---
    "ikea": "home_garden",
    "gardena": "home_garden",
    "karcher": "home_garden",
    "kaercher": "home_garden",
    "miele": "home_garden",
    "liebherr": "home_garden",
    "siemens hausgeraete": "home_garden",
}


# -----------------------------------------------------------------------------
# Category keywords -- weighted by specificity. First match wins.
# Each entry: (regex pattern, category key, confidence, description)
# -----------------------------------------------------------------------------

_KEYWORD_RULES: list[tuple[re.Pattern[str], str, float, str]] = [
    # --- industrial_mro ---
    (re.compile(r"\b(kabel(schelle|kanal|halter|binder|rinne|anschluss(modul)?)|anschlussklemme|reihenklemme|installationstester|isolationstester)\b", re.IGNORECASE),
     "industrial_mro", 0.85, "cabling/installation vocab"),
    (re.compile(r"\b(schaltschrank|pg-verschraubung|erd(klemme|schelle)|nh-sicherung|differentialschutz|fi(\s|-)?schalter)\b", re.IGNORECASE),
     "industrial_mro", 0.85, "switchgear vocab"),
    (re.compile(r"\b(cat ?6a?|cat ?7|rj45|rj11|lwl|sfp\+?|rack 19\"|patchpanel)\b", re.IGNORECASE),
     "industrial_mro", 0.80, "network infra"),
    # --- tools_hardware ---
    (re.compile(r"\b(bohrhammer|akkuschrauber|winkelschleifer|saebelsaege|stichsaege|kreissaege|kapp(sage|s?aege)|bandschleifer|schlagschrauber|heissluft(pistole|fön)|nagelpistole|pressluft(nagler|tacker))\b", re.IGNORECASE),
     "tools_hardware", 0.88, "power-tool vocab"),
    (re.compile(r"\b(schraubenschluessel|maulschluessel|ratsche|inbus|steck(-|)schluessel|drehmomentschluessel|zange|hammer|meissel)\b", re.IGNORECASE),
     "tools_hardware", 0.82, "hand-tool vocab"),
    # --- electronics ---
    (re.compile(r"\b(kopfhoerer|bluetooth(-| )kopfhoerer|noise ?cancelling|soundbar|fernseher|smart(-| )?tv|oled|qled|4k ?monitor|ssd|nvme|grafikkarte|gpu|prozessor|cpu|ram(-| )?modul)\b", re.IGNORECASE),
     "electronics", 0.85, "consumer electronics"),
    # --- fashion_apparel ---
    # Two branches:
    #   (a) \b-anchored vocabulary (standalone words, typical in EN queries)
    #   (b) German compound-noun tails with LEFT prefix allowed
    #       (Winterjacke, Regenmantel, Skihose, Badkleid, Sweatshirt-free zone).
    #       Right side must be word boundary so "jackentasche" does NOT match.
    (re.compile(
        r"\b(?:sneaker|laufschuh|pumps|stiefel|anzug|bluse|hemd|jeans|rock|"
        r"bademode|bikini|badeshorts|schuhe?|t-?shirt|sweatshirt|hoodie)\b"
        r"|(?:jacke|hose|mantel|kleid|pullover)\b",
        re.IGNORECASE,
    ),
     "fashion_apparel", 0.85, "clothing vocab"),
    # --- book_media ---
    (re.compile(r"\b(buch|roman|krimi|ratgeber|sachbuch|kinderbuch|fachbuch|taschenbuch|hardcover|ebook|e-book|hoerbuch|cd-album|vinyl|blu-?ray|dvd(-box|-set|)?)\b", re.IGNORECASE),
     "book_media", 0.85, "book/media vocab"),
    # --- food_beverage ---
    (re.compile(r"\b(wein|rotwein|weisswein|roswein|sekt|champagner|cognac|whisky|whiskey|gin|rum|wodka|bier|kaffee|espresso|tee|schokolade|gebaeck|muesli|nudel|pasta|olivenoel)\b", re.IGNORECASE),
     "food_beverage", 0.85, "food/drink vocab"),
    (re.compile(r"\b(jahrgang (19|20)\d{2}|domaine|chateau|reserva|gran reserva|grand cru|brut|extra(-| )dry)\b", re.IGNORECASE),
     "food_beverage", 0.88, "wine vocab"),
    # --- automotive ---
    (re.compile(r"\b(bremsscheibe|bremsbelag|oellfilter|luftfilter|pollenfilter|zuendkerze|zahnriemen|keilrippenriemen|kupplung|stossdaempfer|federbein|radlager)\b", re.IGNORECASE),
     "automotive", 0.88, "automotive parts"),
    (re.compile(r"\b(reifen|winterreifen|sommerreifen|ganzjahresreifen|motoroel (5w|10w|15w|0w)-?\d{0,2}|auto ?batterie|scheibenwischer)\b", re.IGNORECASE),
     "automotive", 0.82, "tires/fluids"),
    # --- chemicals_lab ---
    (re.compile(r"\b(cas[-: ]?\d{2,7}-\d{2}-\d|sicherheitsdatenblatt|sds|coa|hplc|gc-ms|uv-vis|titration|puffer(-| )?loesung|analysenreinheit|p\.?a\.?)\b", re.IGNORECASE),
     "chemicals_lab", 0.92, "lab vocab + CAS"),
    (re.compile(r"\b(methanol|ethanol|aceton|acetonitril|isopropanol|chloroform|dichlormethan|dmso|tetrahydrofuran|thf|hexan|toluol|natriumchlorid|kaliumhydroxid)\b", re.IGNORECASE),
     "chemicals_lab", 0.85, "common lab chemicals"),
    # --- cosmetic_pharma ---
    (re.compile(r"\b(shampoo|duschgel|conditioner|bodylotion|haarspray|haarfarbe|haarkur|gesichtscreme|augencreme|tagescreme|nachtcreme|sonnencreme|lippenstift|mascara|lidschatten|nagellack|parfum|eau de (parfum|toilette|cologne))\b", re.IGNORECASE),
     "cosmetic_pharma", 0.85, "cosmetics vocab"),
    (re.compile(r"\b(tablette|kapsel|pastille|salbe|tropfen|sirup|nasenspray|augentropfen|vitamin [a-z]\d?|ibuprofen|paracetamol|aspirin|nasenspray|schnupfen|halsweh)\b", re.IGNORECASE),
     "cosmetic_pharma", 0.82, "OTC pharma vocab"),
    # --- office_supplies ---
    (re.compile(r"\b(kugelschreiber|kuli|bleistift|filzstift|marker|textmarker|ordner|klammer|locher|tacker|papier a[34]|druckerpapier|kopierpapier|tintenpatrone|toner(-|)?kassette)\b", re.IGNORECASE),
     "office_supplies", 0.85, "office vocab"),
    # --- sports_outdoor ---
    (re.compile(r"\b(fahrrad|e-?bike|mountainbike|rennrad|crossbike|wanderschuh|trekking(-| )?schuh|zelt|schlafsack|rucksack|isomatte|kletter(-| )?ausruestung|wander(-| )?stock)\b", re.IGNORECASE),
     "sports_outdoor", 0.85, "sport/outdoor vocab"),
    # --- home_garden ---
    (re.compile(r"\b(sofa|sessel|schrank|regal|tisch|stuhl|bett|matratze|teppich|vorhang|gardine|kissen(-|)?(bezug|huelle)|bettdecke|bettbezug|kochtopf|pfanne|geschirr)\b", re.IGNORECASE),
     "home_garden", 0.82, "home/furniture vocab"),
    (re.compile(r"\b(rasenmaeher|heckenschere|motorsense|gartenschlauch|hochdruck(-| )?reiniger|gartenmoebel|gartenbank)\b", re.IGNORECASE),
     "home_garden", 0.85, "garden vocab"),
    # --- toys_hobby ---
    (re.compile(r"\b(spielzeug|brettspiel|puzzle|playmobil|lego|kuscheltier|plueschtier|kartenspiel|modell(-|)?(bausatz|auto|eisenbahn|schiff|flugzeug))\b", re.IGNORECASE),
     "toys_hobby", 0.85, "toys vocab"),
]


# -----------------------------------------------------------------------------
# Structural patterns (CAS, ISBN, OEM)
# -----------------------------------------------------------------------------

_CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")           # e.g. 67-56-1 (methanol)
_OEM_RE = re.compile(r"\b[0-9]{3,5}[.\- ][0-9]{3,5}[.\- ][0-9]{2,5}\b")


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def classify(query: str) -> ClassificationResult:
    """Return a category for `query` based on cheap heuristics only.

    Returns category="default" with confidence=0.0 when no heuristic
    fires -- callers should treat that as "use legacy behaviour".
    """
    if not query or not query.strip():
        return ClassificationResult("default", 0.0, "heuristic:empty")

    q = query.strip()
    q_low = q.lower()

    # 1) Barcode shape -- ISBN/ISSN dominate
    kind = ean_prefix_kind(q)
    if kind == "isbn":
        return ClassificationResult(
            "book_media", 0.99, "heuristic:barcode",
            "ISBN Bookland prefix (978/979)",
        )
    if kind == "issn":
        return ClassificationResult(
            "book_media", 0.95, "heuristic:barcode",
            "ISSN periodical prefix (977)",
        )
    # Any other valid GTIN: no strong category hint on its own.
    # (The enrichment stage in alpha3 will resolve the EAN to a product name.)

    # 2) Explicit structural patterns
    if detect_code_type(q) == "isbn10":
        return ClassificationResult(
            "book_media", 0.99, "heuristic:isbn10",
            "ISBN-10 detected",
        )
    if _CAS_RE.search(q):
        return ClassificationResult(
            "chemicals_lab", 0.95, "heuristic:cas",
            "CAS number pattern",
        )
    # OEM pattern alone is weak -- only trust it alongside an automotive
    # brand hint (handled below via brand-map).

    # 3) Brand-name hits (longest-match first so "bosch professional"
    #    wins over "bosch")
    for brand in sorted(_BRAND_HINTS, key=len, reverse=True):
        if brand in q_low:
            return ClassificationResult(
                _BRAND_HINTS[brand], 0.85, "heuristic:brand",
                f"brand '{brand}' in query",
            )

    # 4) Category-keyword hits
    for pattern, cat, conf, desc in _KEYWORD_RULES:
        if pattern.search(q):
            return ClassificationResult(cat, conf, "heuristic:keyword", desc)

    # 5) Nothing matched
    return ClassificationResult("default", 0.0, "heuristic:none", "no signal")
