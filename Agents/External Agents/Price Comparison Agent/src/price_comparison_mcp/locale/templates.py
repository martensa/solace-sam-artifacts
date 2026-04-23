"""Per-(category, locale) query expansion templates.

When the classifier identifies a category AND the locale detector
returns a language, we look up the appropriate query-expansion
template set here. The order of fall-back:

  (category, locale)  ->  (category, en)  ->  (default, locale)  ->  (default, en)

The category profile itself carries a `query_expansions` list --
that's the *primary* source. This module exists for locales other
than the profile's native language (German-first today). In alpha2
we only add EN templates so non-German queries still work; FR/ES/IT
land in a later alpha.

Every template contains "{q}" which is substituted with the raw query.
"""
from __future__ import annotations

from collections.abc import Sequence


# Keyed by (category_key, locale). Values are tuples of template strings.
TEMPLATES: dict[tuple[str, str], tuple[str, ...]] = {
    # Default fallbacks per locale
    ("default", "de"): ("{q} Preis kaufen",),
    ("default", "en"): ("{q} buy price",),
    ("default", "fr"): ("{q} prix acheter",),
    ("default", "es"): ("{q} precio comprar",),
    ("default", "it"): ("{q} prezzo comprare",),

    # Electronics
    ("electronics", "de"): ("{q} Preis kaufen", "{q} Datenblatt", "{q} Test"),
    ("electronics", "en"): ("{q} buy price", "{q} datasheet", "{q} review"),

    # Industrial / MRO
    ("industrial_mro", "de"): ("{q} Artikelnummer Preis", "{q} Datenblatt", '"{q}"'),
    ("industrial_mro", "en"): ("{q} part number price", "{q} datasheet", '"{q}"'),

    # Tools / Hardware
    ("tools_hardware", "de"): ("{q} Preis", "{q} Artikelnummer", "{q} Zubehoer"),
    ("tools_hardware", "en"): ("{q} price", "{q} part number", "{q} accessory"),

    # Fashion
    ("fashion_apparel", "de"): ("{q} Groesse kaufen", "{q} online shop", "{q} Preis"),
    ("fashion_apparel", "en"): ("{q} size buy online", "{q} shop", "{q} price"),

    # Books / Media
    ("book_media", "de"): ("{q} ISBN", "{q} Buch kaufen", "{q} Taschenbuch", "{q} Gebraucht"),
    ("book_media", "en"): ("{q} ISBN", "{q} buy book", "{q} paperback", "{q} used"),

    # Food / Beverage
    ("food_beverage", "de"): ("{q} kaufen", "{q} Angebot", "{q} Flasche"),
    ("food_beverage", "en"): ("{q} buy", "{q} offer", "{q} bottle"),

    # Automotive
    ("automotive", "de"): ("{q} OEM Nummer", "{q} Ersatzteil Preis", "{q} kaufen"),
    ("automotive", "en"): ("{q} OEM number", "{q} spare part price", "{q} buy"),

    # Chemicals / Lab
    ("chemicals_lab", "de"): ("{q} CAS Nummer", "{q} Sicherheitsdatenblatt", "{q} SDS", "{q} buy"),
    ("chemicals_lab", "en"): ("{q} CAS number", "{q} SDS", "{q} safety data sheet", "{q} buy"),

    # Cosmetic / Pharma
    ("cosmetic_pharma", "de"): ("{q} Preis Apotheke", "{q} kaufen", "{q} PZN"),
    ("cosmetic_pharma", "en"): ("{q} pharmacy price", "{q} buy"),

    # Office
    ("office_supplies", "de"): ("{q} Buerobedarf Preis", "{q} Artikelnummer"),
    ("office_supplies", "en"): ("{q} office supplies price", "{q} part number"),

    # Sports / Outdoor
    ("sports_outdoor", "de"): ("{q} Groesse kaufen", "{q} online shop", "{q} Test"),
    ("sports_outdoor", "en"): ("{q} size buy online", "{q} shop", "{q} review"),

    # Home / Garden
    ("home_garden", "de"): ("{q} kaufen", "{q} Preis"),
    ("home_garden", "en"): ("{q} buy", "{q} price"),

    # Toys / Hobby
    ("toys_hobby", "de"): ("{q} kaufen", "{q} Preis", "{q} Set"),
    ("toys_hobby", "en"): ("{q} buy", "{q} price", "{q} set"),
}


def expand(query: str, category: str = "default", locale: str = "de") -> tuple[str, ...]:
    """Return concrete expanded queries for (category, locale).

    Follows the fallback chain:
        (category, locale) -> (category, "en") -> ("default", locale) -> ("default", "en")

    Each template's "{q}" placeholder is replaced with `query` stripped.
    """
    q = query.strip()
    for k in [(category, locale), (category, "en"), ("default", locale), ("default", "en")]:
        tmpl = TEMPLATES.get(k)
        if tmpl:
            return tuple(t.format(q=q) for t in tmpl)
    return (q,)  # ultimate fallback: bare query


def expand_single_legacy(query: str) -> str:
    """Legacy single-expansion helper (v2.3.5-equivalent).

    Used by the pipeline when the category system is disabled
    (PRICE_ENABLE_CATEGORIES=false). Mirrors the old "{q} Preis kaufen"
    behaviour exactly.
    """
    return f"{query.strip()} Preis kaufen"
