"""Tool: render_failure_summary -- render a German Markdown partial-status
report when the workflow aborts mid-run.

The on_exit.on_failure handler in the procurement workflow invokes this
tool with whatever upstream outputs were captured before the crash. The
output is honest about which phases ran and which did not, so the user
gets actionable triage instead of a cryptic error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jinja2 import Environment, StrictUndefined, select_autoescape


FAILURE_TEMPLATE = """\
# Procurement Article Research -- Teilbericht (Workflow abgebrochen)

_Erzeugt: {{ timestamp_iso }}_

> **WARNUNG**: Der Workflow ist mid-run abgebrochen. Dieser Teilbericht
> zeigt nur die Phasen, die erfolgreich abgeschlossen wurden.

---

## Status pro Phase

| Phase | Anzahl Eintraege |
|-------|-----------------:|
| Eingabe (parse_articles) | {{ parse_count }} |
| Verifikation (verify_articles) | {{ verify_count }} |
| EAN-Lookup (verify_eans) | {{ ean_count }} |
| Bildsuche (search_images) | {{ image_count }} |
| Preisvergleich (search_prices, batches) | {{ price_count }} |

{% if failure_node %}
> **Fehlgeschlagener Node:** `{{ failure_node }}`
{% endif %}
{% if failure_message %}
> **Fehlermeldung:** {{ failure_message }}
{% endif %}

---

{% if verified_items %}
## Verifizierte Artikel (vor Abbruch)

| Pos | Bezeichnung | Hersteller | Konfidenz |
|----:|-------------|------------|-----------|
{% for item in verified_items %}
| {{ item.position }} | {{ item.product_name or "-" }} | {{ item.manufacturer or "-" }} | {{ item.confidence or "none" }} |
{% endfor %}
{% endif %}

---

## Empfohlene naechste Schritte

- Workflow-Logs pruefen: `kubectl logs -n sam-solace-lab-workflows -l app=sam-procurement-workflow --tail=500`
- Fehlende Phasen manuell ueber die zugehoerigen Agents triggern
- Bei wiederholtem Abbruch in derselben Phase: Tool-Verfuegbarkeit + Schema pruefen
"""


def render_failure_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    """Render a partial-status Markdown report on workflow failure.

    Args:
        arguments: dict with keys (all optional):
            parse_articles: list -- parse_articles output.
            verify_articles: list -- verify_articles output.
            verify_eans: list -- ean output.
            search_images: list -- image output.
            search_prices: list -- price output (per-batch).
            failure_node: str -- the node id that failed.
            failure_message: str -- the error message.

    Returns:
        dict with keys: markdown, length.
    """
    parse_articles = arguments.get("parse_articles") or []
    verify_articles = arguments.get("verify_articles") or []
    verify_eans = arguments.get("verify_eans") or []
    search_images = arguments.get("search_images") or []
    search_prices = arguments.get("search_prices") or []

    verified_items = [
        v for v in verify_articles if isinstance(v, dict)
    ][:50]  # cap visual length

    env = Environment(
        autoescape=select_autoescape(default=False),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    tmpl = env.from_string(FAILURE_TEMPLATE)
    md = tmpl.render(
        timestamp_iso=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        parse_count=len(parse_articles) if isinstance(parse_articles, list) else 0,
        verify_count=len(verify_articles) if isinstance(verify_articles, list) else 0,
        ean_count=len(verify_eans) if isinstance(verify_eans, list) else 0,
        image_count=len(search_images) if isinstance(search_images, list) else 0,
        price_count=len(search_prices) if isinstance(search_prices, list) else 0,
        verified_items=verified_items,
        failure_node=arguments.get("failure_node"),
        failure_message=arguments.get("failure_message"),
    )
    return {"markdown": md, "length": len(md)}
