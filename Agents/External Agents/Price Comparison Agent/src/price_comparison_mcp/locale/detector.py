"""Lightweight locale detection for product queries.

The hot path wants a 2-letter ISO code without a 40ms langdetect call
every time. We use a cheap character-class heuristic first and only
reach for langdetect when the heuristic is inconclusive.

Returned codes follow ISO 639-1 (2-letter). Supported languages for
the Price Comparison Agent's scoring overlays are: de, en, fr, es,
it, nl. Anything else returns the fallback.

API:
  detect_locale(query, fallback="de") -> str
"""
from __future__ import annotations

import re


# -----------------------------------------------------------------------------
# Cheap character-class heuristics (first pass)
# -----------------------------------------------------------------------------

_DE_MARKERS = re.compile(r"[äöüÄÖÜß]")
# FR-only chars (NOT shared with Italian). Presence = definitive French.
_FR_UNIQUE_MARKERS = re.compile(r"[çâêëîïôûÿœæ]", re.IGNORECASE)
_ES_MARKERS = re.compile(r"[ñ¡¿]", re.IGNORECASE)
# Chars common to FR+IT (can't disambiguate on char class alone)
_FR_IT_SHARED = re.compile(r"[àèéìíòóù]", re.IGNORECASE)

# Stop words per language, used for voting when char class is inconclusive.
_DE_STOPWORDS = {
    "der", "die", "das", "und", "mit", "für", "fur", "kaufen", "preis",
    "angebot", "ohne", "neue", "neu", "groesse", "größe",
}
_EN_STOPWORDS = {
    "the", "and", "for", "with", "size", "price", "buy", "cheap", "online",
    "deal", "store", "shop", "new",
}
_FR_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "avec", "pour", "sans", "est",
    "et", "ou", "mais", "pas", "cher", "prix", "acheter", "taille", "neuf",
    "portable", "cette",
}
_IT_STOPWORDS = {
    "il", "lo", "la", "le", "gli", "un", "una", "con", "per", "senza",
    "ma", "prezzo", "comprare", "nuovo", "nuova", "taglia", "migliore",
    "espresso", "macinato",
}
_ES_STOPWORDS = {
    "el", "los", "las", "con", "para", "sin", "pero", "precio", "comprar",
    "barato", "nuevo", "nueva", "talla",
}


def _token_set(query: str) -> set[str]:
    return set(re.findall(r"[a-zäöüßàâçéèêëîïôûùñÿœæ]{2,}", query.lower()))


def detect_locale(query: str | None, fallback: str = "de") -> str:
    """Return a 2-letter ISO code: de/en/fr/es/it, or `fallback`.

    Algorithm (cheapest first, each rule short-circuits on hit):
      1. German-unique chars (ä/ö/ü/ß) -> de
      2. Spanish-unique chars (n-tilde, inverted punctuation) -> es
      3. French-unique chars (c-cedilla, circumflex etc., NOT shared with IT) -> fr
      4. FR/IT shared chars present AND no DE/ES/FR-unique marker:
         - stopword voting {fr, it} to disambiguate
      5. Stopword voting {de, en, fr, it, es}
      6. Fallback
    """
    if not query or not query.strip():
        return fallback
    q = query.strip()

    # Rule 1: DE-unique chars
    if _DE_MARKERS.search(q):
        return "de"

    # Rule 2: ES-unique chars
    if _ES_MARKERS.search(q):
        return "es"

    # Rule 3: FR-unique chars (not shared with IT)
    if _FR_UNIQUE_MARKERS.search(q):
        return "fr"

    toks = _token_set(q)

    # Rule 4: FR/IT shared chars -> disambiguate by stopwords
    if _FR_IT_SHARED.search(q):
        fr_hits = len(toks & _FR_STOPWORDS)
        it_hits = len(toks & _IT_STOPWORDS)
        if fr_hits > it_hits:
            return "fr"
        if it_hits > fr_hits:
            return "it"
        # tie-breaker: more FR stopwords in general in our vocabulary,
        # and French is more common in DACH-area procurement data
        return "fr" if fr_hits > 0 else "it"

    # Rule 5: global stopword vote
    de_hits = len(toks & _DE_STOPWORDS)
    en_hits = len(toks & _EN_STOPWORDS)
    fr_hits = len(toks & _FR_STOPWORDS)
    it_hits = len(toks & _IT_STOPWORDS)
    es_hits = len(toks & _ES_STOPWORDS)

    scores = {"de": de_hits, "en": en_hits, "fr": fr_hits, "it": it_hits, "es": es_hits}
    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score > 0:
        return best_lang

    # Rule 6: fall back
    return fallback


# -----------------------------------------------------------------------------
# TLD mapping (used by aggregator-URL construction downstream)
# -----------------------------------------------------------------------------

_TLD_MAP: dict[tuple[str, str], str] = {
    ("amazon", "de"): "amazon.de",
    ("amazon", "en"): "amazon.com",
    ("amazon", "fr"): "amazon.fr",
    ("amazon", "es"): "amazon.es",
    ("amazon", "it"): "amazon.it",
    ("amazon", "nl"): "amazon.nl",
    ("ebay", "de"): "ebay.de",
    ("ebay", "en"): "ebay.com",
    ("ebay", "fr"): "ebay.fr",
    ("idealo", "de"): "idealo.de",
    ("idealo", "en"): "idealo.co.uk",
    ("idealo", "fr"): "idealo.fr",
    ("idealo", "it"): "idealo.it",
    ("idealo", "es"): "idealo.es",
}


def tld_for(aggregator: str, locale: str, fallback: str = "de") -> str:
    """Return the preferred TLD for an aggregator given a locale.

    Falls back to `aggregator.<fallback>` if the exact pair is unmapped.
    """
    key = (aggregator.lower(), locale.lower())
    if key in _TLD_MAP:
        return _TLD_MAP[key]
    return f"{aggregator}.{fallback}"
