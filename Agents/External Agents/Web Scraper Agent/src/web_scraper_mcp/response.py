"""Response builder with response mode support for multi-agent workflows.

Supports two response modes:

- full:    Returns metadata + inline payload (base64, HTML, text).
           Use at the end of a chain when the content is needed directly.
- summary: Returns metadata only (URL, MIME type, size, filename).
           Use in mid-chain steps where only metadata is passed forward.

Artifact storage (reference mode) is handled by the SAM framework via
artifact_handling_mode and artifact_service configuration, not by this module.
"""

from __future__ import annotations

from typing import Any

RESPONSE_MODES = ("full", "summary")


def validate_response_mode(mode: str) -> str | None:
    """Return an error message if mode is invalid, None otherwise."""
    if mode not in RESPONSE_MODES:
        return f"response_mode must be one of: {', '.join(RESPONSE_MODES)}"
    return None


def build_response(
    response_mode: str,
    metadata: str,
    next_step: str,
    payload_items: list[dict[str, Any]],
) -> dict:
    """Build an MCP tool response respecting the requested response mode.

    Args:
        response_mode: One of "full" or "summary".
        metadata: Markdown table with structured metadata.
        next_step: One-line hint about what can be done with the output.
        payload_items: MCP content items for full mode (image, resource, text).

    Returns:
        MCP tool result dict with "content" list.
    """
    meta_block = metadata.rstrip("\n") + "\n\n" + next_step

    # -- summary mode: metadata and hint only, no payload --
    if response_mode == "summary":
        return {"content": [{"type": "text", "text": meta_block}]}

    # -- full mode: metadata + inline payload --
    items: list[dict[str, Any]] = [{"type": "text", "text": meta_block}]
    items.extend(payload_items)
    return {"content": items}
