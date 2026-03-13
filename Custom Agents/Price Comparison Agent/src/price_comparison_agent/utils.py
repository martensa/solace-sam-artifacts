"""Hilfsfunktionen fuer den Preisvergleich-Agenten."""

import re
import unicodedata
from typing import Optional


def is_valid_ean(value: str) -> bool:
    """Prueft ob der Wert eine gueltige EAN-8, EAN-13 oder GTIN ist."""
    cleaned = re.sub(r"[\s\-]", "", value)
    if not cleaned.isdigit():
        return False
    if len(cleaned) not in (8, 12, 13, 14):
        return False
    return _check_ean_digit(cleaned)


def _check_ean_digit(digits: str) -> bool:
    """Prueft die EAN-Pruefziffer."""
    total = 0
    for i, digit in enumerate(digits[:-1]):
        n = int(digit)
        if len(digits) % 2 == 0:
            total += n * (3 if i % 2 == 0 else 1)
        else:
            total += n * (1 if i % 2 == 0 else 3)
    check = (10 - (total % 10)) % 10
    return check == int(digits[-1])


def normalize_ean(value: str) -> str:
    """Bereinigt eine EAN-Eingabe (entfernt Leerzeichen und Bindestriche)."""
    return re.sub(r"[\s\-]", "", value.strip())


def detect_search_type(query: str) -> str:
    """Erkennt automatisch ob eine Suchanfrage eine EAN oder ein Name ist."""
    cleaned = re.sub(r"[\s\-]", "", query.strip())
    if cleaned.isdigit() and len(cleaned) in (8, 12, 13, 14):
        return "ean"
    return "name"


def format_price(price: float) -> str:
    """Formatiert einen Preis als deutschen Waehrungsstring."""
    return f"{price:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def sanitize_product_name(name: str) -> str:
    """Bereinigt einen Produktnamen fuer die Suche."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_idealo_search_url(query: str) -> str:
    """Erstellt die Idealo-Such-URL."""
    import urllib.parse
    encoded = urllib.parse.quote_plus(query)
    return f"https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q={encoded}"


def build_geizhals_search_url(query: str) -> str:
    """Erstellt die Geizhals-Such-URL."""
    import urllib.parse
    encoded = urllib.parse.quote_plus(query)
    return f"https://geizhals.de/?fs={encoded}&in=&pg=1&sale=1&sort=p"


def truncate_text(text: str, max_length: int = 100) -> str:
    """Kuerzt einen Text auf maximale Laenge."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def calculate_savings(
    reference_price: float, alternative_price: float
) -> tuple[float, float]:
    """Berechnet absolute und prozentuale Ersparnis."""
    savings = reference_price - alternative_price
    savings_percent = (savings / reference_price * 100) if reference_price > 0 else 0.0
    return round(savings, 2), round(savings_percent, 1)


COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/webp,*/*;q=0.8"
    ),
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
}
