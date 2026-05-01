"""Tool: parse_articles -- split a free-form article list into a structured
JSON array, flagging B2B net-price markers per line.

Pure regex, no network, no LLM. Deterministic for a given input.
"""

from __future__ import annotations

import re
from typing import Any

# Markers that indicate a line is a B2B net-price contract item, not a
# publicly priced article. When any of these appears in the line we set
# is_b2b_netto=true so the downstream price phase can skip the line.
B2B_NETTO_PATTERN = re.compile(
    r"\b(?:Nettoartikel|Nettoangebotspreise|Nettoangebotsartikel|"
    r"BRUTTOARTIKEL|NLAG)\b",
    flags=re.IGNORECASE,
)


def parse_articles(arguments: dict[str, Any]) -> dict[str, Any]:
    """Parse a free-form article list (one item per line) into a JSON array.

    Args:
        arguments: dict with key:
            articles_text: str -- the raw multi-line article list.

    Returns:
        dict with keys:
            items: list of {position, raw_code, is_b2b_netto}
            count: int -- number of non-empty lines parsed.
    """
    articles_text = arguments.get("articles_text", "")
    if not isinstance(articles_text, str):
        raise ValueError(
            f"articles_text must be a string, got {type(articles_text).__name__}"
        )

    items: list[dict[str, Any]] = []
    position = 0
    for raw_line in articles_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        position += 1
        items.append(
            {
                "position": position,
                "raw_code": line,
                "is_b2b_netto": bool(B2B_NETTO_PATTERN.search(line)),
            }
        )

    return {"items": items, "count": len(items)}
