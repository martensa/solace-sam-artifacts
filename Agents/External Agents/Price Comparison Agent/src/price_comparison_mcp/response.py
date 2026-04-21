"""Response builder with response mode support for multi-agent workflows.

Supports two response modes:

- full:    Returns metadata + inline payload.
- summary: Returns metadata only.

Artifact storage is handled by the SAM framework via artifact_handling_mode.
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
    """Build an MCP tool response respecting the requested response mode."""
    meta_block = metadata.rstrip("\n") + "\n\n" + next_step

    if response_mode == "summary":
        return {"content": [{"type": "text", "text": meta_block}]}

    items: list[dict[str, Any]] = [{"type": "text", "text": meta_block}]
    items.extend(payload_items)
    return {"content": items}
