"""Tool: merge_procurement_data -- canonical join of upstream phase
outputs into a single procurement dataset, with deterministic
offer-quality filtering, image-filename heuristic validation, and
integrity checks.

Replaces the LLM-based merge_results node, eliminating:
  - LLM swapping positions between iterations
  - LLM picking wrong offer as "cheapest"
  - LLM forgetting fields
  - LLM mis-counting summary stats
  - LLM emitting hallucinated image_artifact_ref names

The function expects parse_articles[] as the canonical source-of-truth
for position and raw_code. Downstream arrays (verify, ean, image,
price) are joined by ARRAY INDEX (SAM map nodes preserve order).

Image-filename heuristic: SAM's MCP framework writes artifacts with
a deterministic pattern <tool>_<content_type>_<index>_<8hex>.<ext>
(see mcp_content_processor.py:277 in the SAM source). When the
WebScraperAgent's LLM hallucinates a filename (e.g. "image_search_
result.png" or "analysis_results.json"), it does not match this
pattern. We reject non-conforming names without needing an S3 HEAD.

Offer-quality filter rules (an offer is RELIABLE iff ALL hold):
  - is_outlier is not true
  - llm_verdict is not "no"
  - match_confidence in {high, exact}
  - composite_confidence >= 0.5
  - url does NOT contain aggregator search patterns
"""

from __future__ import annotations

import re
from typing import Any

# Framework pattern for `search_and_download_image` artifacts:
#   search_and_download_image_image_<index>_<8hex>.<ext>
# Anything else is a hallucination.
IMAGE_ARTIFACT_PATTERN = re.compile(
    r"^search_and_download_image_image_\d+_[0-9a-f]{8}\."
    r"(png|jpg|jpeg|webp|gif|svg)$",
    flags=re.IGNORECASE,
)

# URL substrings that indicate an aggregator search/listing page rather
# than a concrete product-detail page. Offers from such URLs are
# unreliable price signals.
SEARCH_URL_PATTERNS = (
    "/search",
    "?search=",
    "&search=",
    "?q=",
    "&q=",
    "/suche",
    "?searchterm=",
    "&searchterm=",
    "?text=",
    "&text=",
)

ACCEPTED_MATCH_CONFIDENCE = {"high", "exact"}


def _is_reliable(offer: dict[str, Any]) -> tuple[bool, str | None]:
    """Return (is_reliable, discard_reason)."""
    if offer.get("is_outlier") is True:
        return False, "outlier"
    if offer.get("llm_verdict") == "no":
        return False, "llm_verdict_no"
    mc = (offer.get("match_confidence") or "").lower()
    if mc and mc not in ACCEPTED_MATCH_CONFIDENCE:
        return False, f"match_confidence_{mc}"
    cc = offer.get("composite_confidence")
    if isinstance(cc, (int, float)) and cc < 0.5:
        return False, "composite_confidence_low"
    url = (offer.get("url") or "").lower()
    if url:
        for pat in SEARCH_URL_PATTERNS:
            if pat in url:
                return False, f"search_url:{pat}"
    return True, None


def _flatten_price_results(price_results: list[Any]) -> dict[int, dict[str, Any]]:
    """Convert search_prices.output.results (a list of batch results,
    each containing items[]) into a dict keyed by position.
    """
    by_position: dict[int, dict[str, Any]] = {}
    for batch in price_results:
        if not isinstance(batch, dict):
            continue
        for entry in batch.get("items", []):
            if not isinstance(entry, dict):
                continue
            pos = entry.get("position")
            if isinstance(pos, int):
                by_position[pos] = entry
    return by_position


def merge_procurement_data(arguments: dict[str, Any]) -> dict[str, Any]:
    """Build the merged procurement object.

    Args:
        arguments: dict with keys:
            parse_articles: list -- CANONICAL source of position+raw_code+is_b2b_netto.
            verify_articles: list -- AVA outputs, joined by index.
            verify_eans: list -- EAN outputs, joined by index.
            search_images: list -- image outputs, joined by index. Each may
              optionally have an `_artifact_validated` bool added by the
              validate_image_artifacts step. Hallucinated refs (validated=false)
              are nulled out.
            search_prices: list -- price-batch outputs (one per chunk),
              joined into items by position field.

    Returns:
        dict with keys: summary, items, anomalies.
    """
    parse_articles = arguments.get("parse_articles", []) or []
    verify_articles = arguments.get("verify_articles", []) or []
    verify_eans = arguments.get("verify_eans", []) or []
    search_images = arguments.get("search_images", []) or []
    search_prices = arguments.get("search_prices", []) or []

    if not isinstance(parse_articles, list):
        raise ValueError("parse_articles must be a list")

    price_by_pos = _flatten_price_results(search_prices)

    items: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []

    success_count = 0
    skipped_b2b = 0
    no_results_count = 0
    failed_count = 0

    for i, parse_item in enumerate(parse_articles):
        position = parse_item.get("position", i + 1)
        raw_code = parse_item.get("raw_code", "")
        is_b2b_netto = bool(parse_item.get("is_b2b_netto", False))

        # ---- VERIFY (with integrity check) ----
        # Hard mismatch on raw_code -> nullify content (cross-iteration
        # contamination, the verify result is for a DIFFERENT article).
        # AVA's output schema no longer carries a `position` field
        # (workflow is canonical), so we no longer track position
        # mismatches.
        verify_item = verify_articles[i] if i < len(verify_articles) else {}
        if not isinstance(verify_item, dict):
            verify_item = {}
        v_raw = verify_item.get("raw_code")
        raw_code_ok = True
        if v_raw is not None and v_raw != raw_code:
            anomalies.append({
                "position": position,
                "type": "raw_code_mismatch",
                "expected": raw_code,
                "actual": str(v_raw),
            })
            raw_code_ok = False

        if raw_code_ok:
            product_name = verify_item.get("product_name")
            manufacturer = verify_item.get("manufacturer")
            article_number = verify_item.get("article_number")
            category = verify_item.get("category")
            short_description = verify_item.get("short_description")
            confidence = verify_item.get("confidence", "none")
        else:
            product_name = None
            manufacturer = None
            article_number = None
            category = None
            short_description = None
            confidence = "none"

        # ---- EAN ----
        ean_item = verify_eans[i] if i < len(verify_eans) else {}
        if not isinstance(ean_item, dict):
            ean_item = {}
        ean = ean_item.get("ean")
        ean_source = ean_item.get("ean_source") or "not_found"
        ean_raw = ean_item.get("raw")

        # ---- IMAGE (with hallucination guard) ----
        image_item = search_images[i] if i < len(search_images) else {}
        if not isinstance(image_item, dict):
            image_item = {}
        image_artifact_ref = image_item.get("image_artifact_ref")
        image_url = image_item.get("image_url")
        image_note = image_item.get("note")

        # Heuristic hallucination guard: if a non-empty image_artifact_ref
        # does NOT match the SAM framework filename pattern, the LLM
        # invented it. Null it out and surface an anomaly. We rely on
        # the deterministic framework pattern instead of an S3 HEAD,
        # which avoids the recurring "validate_image_artifacts" node
        # crash without losing the safety net.
        if image_artifact_ref:
            if not IMAGE_ARTIFACT_PATTERN.match(str(image_artifact_ref).strip()):
                anomalies.append({
                    "position": position,
                    "type": "hallucinated_image_artifact",
                    "expected": (
                        "filename matching framework pattern "
                        "search_and_download_image_image_N_HEX.EXT"
                    ),
                    "actual": str(image_artifact_ref),
                })
                image_artifact_ref = None
                image_note = ((image_note or "") + " [hallucinated_artifact]").strip()

        # If a caller-supplied S3 validation flag is present, honour it
        # as a stronger signal (used when the calling node has S3 access).
        # Optional; keeps backward-compat with prior workflows that ran
        # a separate validate_image_artifacts map step.
        if (
            "_artifact_validated" in image_item
            and image_item["_artifact_validated"] is False
            and image_artifact_ref
        ):
            anomalies.append({
                "position": position,
                "type": "image_artifact_not_in_s3",
                "expected": None,
                "actual": str(image_artifact_ref),
            })
            image_artifact_ref = None
            image_note = ((image_note or "") + " [s3_not_found]").strip()

        # ---- PRICE ----
        price_entry = price_by_pos.get(position) if not is_b2b_netto else None
        offers_raw: list[dict[str, Any]] = []
        insights: dict[str, Any] = {}
        next_actions: list[Any] = []

        if isinstance(price_entry, dict):
            offers_raw = price_entry.get("offers") or []
            if not isinstance(offers_raw, list):
                offers_raw = []
            insights = price_entry.get("insights") or {}
            if not isinstance(insights, dict):
                insights = {}
            next_actions = price_entry.get("next_actions") or []
            if not isinstance(next_actions, list):
                next_actions = []

        # ---- OFFER QUALITY GATE ----
        reliable: list[dict[str, Any]] = []
        discarded: list[dict[str, Any]] = []
        for offer in offers_raw:
            if not isinstance(offer, dict):
                continue
            ok, reason = _is_reliable(offer)
            if ok:
                reliable.append(offer)
            else:
                offer_with_reason = dict(offer)
                offer_with_reason["_discard_reason"] = reason
                discarded.append(offer_with_reason)

        cheapest_reliable: dict[str, Any] | None = None
        if reliable:
            cheapest = min(reliable, key=lambda o: float(o.get("total_price") or float("inf")))
            cheapest_reliable = {
                "merchant": cheapest.get("merchant"),
                "total_price": cheapest.get("total_price"),
                "currency": cheapest.get("currency", "EUR"),
                "url": cheapest.get("url"),
                "match_confidence": cheapest.get("match_confidence"),
                "composite_confidence": cheapest.get("composite_confidence"),
            }

        # ---- indicative_cheapest ----
        # Best-effort fallback when reliability gate rejected ALL offers
        # but we still have public-detail-page data the user might want
        # to inspect manually. Picks the lowest-price discarded offer
        # that has a non-search URL and a valid total_price. Always
        # carried alongside `cheapest_reliable` -- the template decides
        # which to show. The user-visible label clarifies that this is
        # an UNCHECKED hint, not a verified offer.
        indicative_cheapest: dict[str, Any] | None = None
        if cheapest_reliable is None and discarded:
            usable = []
            for offer in discarded:
                tp = offer.get("total_price")
                if not isinstance(tp, (int, float)) or tp <= 0:
                    continue
                url = (offer.get("url") or "").lower()
                if any(p in url for p in SEARCH_URL_PATTERNS):
                    continue
                # Skip offers we already know are outliers/llm-vetoed.
                reason = offer.get("_discard_reason") or ""
                if reason in ("outlier", "llm_verdict_no"):
                    continue
                usable.append(offer)
            if usable:
                cheapest_indicative = min(
                    usable,
                    key=lambda o: float(o.get("total_price") or float("inf")),
                )
                indicative_cheapest = {
                    "merchant": cheapest_indicative.get("merchant"),
                    "total_price": cheapest_indicative.get("total_price"),
                    "currency": cheapest_indicative.get("currency", "EUR"),
                    "url": cheapest_indicative.get("url"),
                    "match_confidence": cheapest_indicative.get("match_confidence"),
                    "composite_confidence": cheapest_indicative.get("composite_confidence"),
                    "discard_reason": cheapest_indicative.get("_discard_reason"),
                }

        # ---- price_status_reliable ----
        if is_b2b_netto:
            price_status_reliable = "skipped_b2b_netto"
        elif not offers_raw:
            price_status_reliable = "no_results"
        elif cheapest_reliable is not None:
            price_status_reliable = "success"
        else:
            price_status_reliable = "no_reliable_offers"

        # ---- summary counters ----
        if is_b2b_netto:
            skipped_b2b += 1
        elif (
            price_status_reliable == "success"
            and ean_source in ("verified", "found_validated")
        ):
            success_count += 1
        elif price_status_reliable in ("no_results", "no_reliable_offers"):
            no_results_count += 1
        elif confidence == "none":
            failed_count += 1

        items.append({
            "position": position,
            "raw_code": raw_code,
            "is_b2b_netto": is_b2b_netto,
            "product_name": product_name,
            "manufacturer": manufacturer,
            "article_number": article_number,
            "category": category,
            "short_description": short_description,
            "confidence": confidence,
            "ean": ean,
            "ean_source": ean_source,
            "ean_raw": ean_raw,
            "image_artifact_ref": image_artifact_ref,
            "image_url": image_url,
            "image_note": image_note,
            "offers_raw": offers_raw,
            "insights": insights,
            "next_actions": next_actions,
            "cheapest_reliable": cheapest_reliable,
            "indicative_cheapest": indicative_cheapest,
            "reliable_offer_count": len(reliable),
            "discarded_offer_count": len(discarded),
            "discarded_offers_sample": discarded[:3],
            "price_status_reliable": price_status_reliable,
        })

    summary = {
        "total": len(items),
        "success": success_count,
        "skipped_b2b": skipped_b2b,
        "no_results": no_results_count,
        "failed": failed_count,
        "anomalies_count": len(anomalies),
    }

    return {"summary": summary, "items": items, "anomalies": anomalies}
