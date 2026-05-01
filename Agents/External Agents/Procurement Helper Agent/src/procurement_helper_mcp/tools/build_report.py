"""Tool: build_procurement_report -- single deterministic call that
performs the entire post-pipeline aggregation:

  1. merge_procurement_data (canonical join, integrity check,
     heuristic image-filename hallucination guard, offer-quality gate)
  2. render_procurement_report (Jinja2 Markdown)

Combining these into one tool eliminates the separate validate-image
map node and the merge -> compile_report two-step that previously
required two LLM round-trips. Net effect: one HelperAgent invocation
at the end of the workflow does ALL deterministic post-processing,
shrinking the LLM-routing surface (and thus hallucination surface)
to a single call.
"""

from __future__ import annotations

from typing import Any

from .merge_data import merge_procurement_data
from .render_report import render_procurement_report


def build_procurement_report(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run merge + render in a single deterministic Python call.

    Args:
        arguments: dict with keys:
            parse_articles: list (required) -- canonical position/raw_code source.
            verify_articles: list -- AVA outputs.
            verify_eans: list -- EAN outputs.
            search_images: list -- image outputs (image_artifact_ref names
                are validated against the SAM framework pattern; hallucinated
                names are nulled and an anomaly is recorded).
            search_prices: list -- price-batch outputs.

    Returns:
        dict with keys:
            markdown: str -- the rendered German procurement Markdown brief.
            length: int -- character count.
            summary: object -- the merge summary counts.
            items: list -- per-position merged data.
            anomalies: list -- integrity anomalies surfaced during merge.
    """
    import logging

    logger = logging.getLogger("procurement-helper-mcp.build_report")

    merged = merge_procurement_data(
        {
            "parse_articles": arguments.get("parse_articles", []),
            "verify_articles": arguments.get("verify_articles", []),
            "verify_eans": arguments.get("verify_eans", []),
            "search_images": arguments.get("search_images", []),
            "search_prices": arguments.get("search_prices", []),
        }
    )

    # Defensive: if Jinja rendering fails for any reason (template
    # regression, unexpected upstream shape), fall back to a minimal
    # JSON-formatted markdown so the workflow can still produce a
    # response artifact and the user gets *something* actionable rather
    # than a hard node failure. The merge data is still complete.
    try:
        rendered = render_procurement_report({"merged_data": merged})
        markdown = rendered["markdown"]
        length = rendered["length"]
    except Exception as e:
        logger.exception("render_procurement_report failed; emitting fallback")
        merged.setdefault("anomalies", []).append({
            "position": 0,
            "type": "render_template_error",
            "expected": "successful Jinja2 render",
            "actual": f"{type(e).__name__}: {e}",
        })
        merged["summary"]["anomalies_count"] = len(merged["anomalies"])
        # Minimal fallback markdown -- at least summary + per-position
        # raw codes, no fancy tables.
        lines = [
            "# Procurement Article Research -- Bericht (Fallback)",
            "",
            "_Hinweis: Markdown-Renderer fehlgeschlagen, einfache Fallback-Ausgabe._",
            "",
            "## Zusammenfassung",
            "",
            f"- Gesamt: {merged['summary'].get('total', 0)}",
            f"- Erfolgreich: {merged['summary'].get('success', 0)}",
            f"- B2B Nettoartikel: {merged['summary'].get('skipped_b2b', 0)}",
            f"- Ohne verlaessliche Preise: {merged['summary'].get('no_results', 0)}",
            f"- Fehlgeschlagen: {merged['summary'].get('failed', 0)}",
            f"- Anomalien: {merged['summary'].get('anomalies_count', 0)}",
            "",
            "## Positionen",
            "",
        ]
        for it in merged.get("items", []):
            lines.append(
                f"- **Pos {it.get('position')}**: "
                f"{it.get('product_name') or it.get('raw_code') or '-'} "
                f"(Status: {it.get('price_status_reliable', '-')})"
            )
        lines.append("")
        markdown = "\n".join(lines)
        length = len(markdown)

    return {
        "markdown": markdown,
        "length": length,
        "summary": merged["summary"],
        "items": merged["items"],
        "anomalies": merged["anomalies"],
    }
