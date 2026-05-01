"""Tool: render_procurement_report -- render the final German Markdown
procurement brief from a merged-results object.

Pure Jinja2 template. No LLM => deterministic structure: every position
gets the exact same layout, the image embed line is never omitted,
field counts are correct, totals sum correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jinja2 import ChainableUndefined, Environment, select_autoescape


def _de_money(value: Any) -> str:
    """Format a number in German locale: 1234.56 -> '1.234,56'."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "-"
    s = f"{n:,.2f}"
    # Convert "1,234.56" -> "1.234,56"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _safe(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


REPORT_TEMPLATE = """\
# Procurement Article Research -- Bericht

_Erzeugt: {{ timestamp_iso }}_

---

## Zusammenfassung

| Metrik | Wert |
|--------|-----:|
| Gesamt | {{ summary.total }} |
| Erfolgreich (Preis + EAN) | {{ summary.success }} |
| B2B Nettoartikel (uebersprungen) | {{ summary.skipped_b2b }} |
| Ohne verlaessliche Preise | {{ summary.no_results }} |
| Fehlgeschlagen | {{ summary.failed }} |
| Anomalien | {{ summary.anomalies_count }} |

{% if anomalies %}
> WARNUNG -- Datenintegritaets-Anomalien erkannt:
{% for a in anomalies %}
> - **Position {{ a.position }}** -- {{ a.type }}: erwartet `{{ a.expected | safe_str }}`, tatsaechlich `{{ a.actual | safe_str }}`. Bitte manuell pruefen.
{% endfor %}
{% endif %}

---

## Positionen

{% for item in items %}
### Position {{ item.position }} -- {{ item.product_name | safe_str(item.raw_code) }}
{% if item.image_artifact_ref %}

«artifact_return:{{ item.image_artifact_ref }}»
{% endif %}

| Feld | Wert |
|------|------|
| Position | {{ item.position }} |
| Bezeichnung | {{ item.product_name | safe_str }} |
| Hersteller | {{ item.manufacturer | safe_str }} |
| Artikelnummer | {{ item.article_number | safe_str }} |
| Kategorie | {{ item.category | safe_str }} |
| EAN | {{ item.ean | safe_str }} (Quelle: {{ item.ean_source | safe_str }}) |
| Beschreibung | {{ item.short_description | safe_str }} |
| Bildquelle | {{ item.image_url | safe_str }} |
| Bester Preis | {% if item.price_status_reliable == 'success' %}**{{ item.cheapest_reliable.total_price | de_money }} {{ item.cheapest_reliable.currency | safe_str("EUR") }}** bei {{ item.cheapest_reliable.merchant | safe_str }}{% elif item.price_status_reliable == 'no_reliable_offers' and item.indicative_cheapest %}_indikativ {{ item.indicative_cheapest.total_price | de_money }} {{ item.indicative_cheapest.currency | safe_str("EUR") }} bei {{ item.indicative_cheapest.merchant | safe_str }}_ (manuell pruefen, {{ item.discarded_offer_count }} Angebot(e) wegen EAN/Variant-Mismatch verworfen){% elif item.price_status_reliable == 'no_reliable_offers' %}_kein verlaessliches Angebot ({{ item.discarded_offer_count }} Treffer als Suchseite/Outlier verworfen)_{% elif item.price_status_reliable == 'no_results' %}_keine Treffer_{% elif item.price_status_reliable == 'skipped_b2b_netto' %}_B2B-Netto: nicht oeffentlich_{% else %}-{% endif %} |
| Bereinigte Spanne | {% set f = (item.insights or {}).get('filtered') if item.insights is mapping else none %}{% if f and f.get('min_price') is not none %}{{ f.min_price | de_money }} / {{ f.median_price | de_money }} / {{ f.max_price | de_money }} EUR{% elif item.insights and item.insights.get('min_price') is not none %}{{ item.insights.min_price | de_money }} / {{ item.insights.get('median_price') | de_money }} / {{ item.insights.max_price | de_money }} EUR{% else %}-{% endif %} |
| URL | {% if item.cheapest_reliable %}{{ item.cheapest_reliable.url | safe_str }}{% elif item.indicative_cheapest %}{{ item.indicative_cheapest.url | safe_str }} _(indikativ)_{% else %}-{% endif %} |
| Angebote (verlaesslich/verworfen) | {{ item.reliable_offer_count }} / {{ item.discarded_offer_count }} |
| Status | {{ item.price_status_reliable }} |

{% if item.next_actions and item.reliable_offer_count == 0 %}
**Naechste Schritte:**
{% for action in item.next_actions %}
- {{ action }}
{% endfor %}
{% endif %}

{% endfor %}
---

## Methodik

| Schritt | Komponente |
|---------|------------|
| Verifikation | ArticleVerificationAgent (Web Search) |
| EAN-Lookup | EANSearchAgent (ean-search.org) |
| Bilder | WebScraperAgent (Playwright + Image Search) |
| Bild-Verifikation | ProcurementHelperAgent (S3 HEAD) |
| Preise | PriceComparisonAgent (Multi-Source, 70+ Portale) |
| Aggregation | ProcurementHelperAgent (deterministisch) |
| Rendering | ProcurementHelperAgent (Jinja2-Template) |

_Workflow: ProcurementArticleResearch v2.3.2_
"""


def _build_env() -> Environment:
    # ChainableUndefined lets us walk into nested dict keys that may or
    # may not exist (e.g. item.insights.filtered.min_price) without
    # raising. Missing leaves render as empty string, and we explicitly
    # guard with `is defined` / `if x` in the template before using
    # values. StrictUndefined was too aggressive: PCA's insights dict
    # has a `filtered` sub-key only when outliers were detected, and a
    # bare `if item.insights and item.insights.filtered` still raises
    # under StrictUndefined because key access happens before the AND
    # short-circuit fully resolves.
    env = Environment(
        autoescape=select_autoescape(default=False),
        undefined=ChainableUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    env.filters["de_money"] = _de_money
    env.filters["safe_str"] = _safe
    return env


def render_procurement_report(arguments: dict[str, Any]) -> dict[str, Any]:
    """Render the final Markdown report.

    Args:
        arguments: dict with keys:
            merged_data: dict (output of merge_procurement_data) --
              must have summary, items, anomalies.

    Returns:
        dict with keys:
            markdown: str -- the rendered procurement brief.
            length: int -- character count.
    """
    merged = arguments.get("merged_data", {})
    if not isinstance(merged, dict):
        raise ValueError("merged_data must be an object")

    summary = merged.get("summary", {}) or {}
    items = merged.get("items", []) or []
    anomalies = merged.get("anomalies", []) or []

    # Defensive defaults so the template does not raise on missing keys.
    for key in ("total", "success", "skipped_b2b", "no_results", "failed", "anomalies_count"):
        summary.setdefault(key, 0)

    env = _build_env()
    tmpl = env.from_string(REPORT_TEMPLATE)
    markdown = tmpl.render(
        timestamp_iso=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        summary=summary,
        items=items,
        anomalies=anomalies,
    )
    return {"markdown": markdown, "length": len(markdown)}
