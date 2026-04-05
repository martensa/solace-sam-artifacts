"""Tests for response.py -- response builder and mode validation."""

from __future__ import annotations

import pytest

from web_scraper_mcp.response import build_response, validate_response_mode

# ---------------------------------------------------------------------------
# validate_response_mode
# ---------------------------------------------------------------------------

class TestValidateResponseMode:

    @pytest.mark.parametrize("mode", ["full", "summary"])
    def test_valid_modes(self, mode: str) -> None:
        assert validate_response_mode(mode) is None

    @pytest.mark.parametrize("mode", ["reference", "compact", "", "FULL", "Summary"])
    def test_invalid_modes(self, mode: str) -> None:
        result = validate_response_mode(mode)
        assert result is not None
        assert "response_mode" in result


# ---------------------------------------------------------------------------
# build_response -- full mode
# ---------------------------------------------------------------------------

class TestBuildResponseFull:

    def test_full_includes_metadata_and_payload(self) -> None:
        result = build_response(
            response_mode="full",
            metadata="| Field | Value |\n|-------|-------|\n| URL | https://example.com |",
            next_step="Content available.",
            payload_items=[{"type": "text", "text": "<html>page</html>"}],
        )
        content = result["content"]
        assert len(content) == 2
        # First item: metadata + next_step
        assert "example.com" in content[0]["text"]
        assert "Content available." in content[0]["text"]
        # Second item: payload
        assert content[1]["type"] == "text"
        assert "<html>" in content[1]["text"]

    def test_full_with_image_payload(self) -> None:
        result = build_response(
            response_mode="full",
            metadata="| Field | Value |",
            next_step="Image ready.",
            payload_items=[{"type": "image", "data": "base64data", "mimeType": "image/png"}],
        )
        content = result["content"]
        assert len(content) == 2
        assert content[1]["type"] == "image"
        assert content[1]["data"] == "base64data"

    def test_full_with_multiple_payload_items(self) -> None:
        result = build_response(
            response_mode="full",
            metadata="meta",
            next_step="done",
            payload_items=[
                {"type": "text", "text": "part1"},
                {"type": "text", "text": "part2"},
            ],
        )
        assert len(result["content"]) == 3


# ---------------------------------------------------------------------------
# build_response -- summary mode
# ---------------------------------------------------------------------------

class TestBuildResponseSummary:

    def test_summary_excludes_payload(self) -> None:
        result = build_response(
            response_mode="summary",
            metadata="| Field | Value |\n|-------|-------|\n| Size | 1 MB |",
            next_step="File downloaded.",
            payload_items=[{"type": "text", "text": "SHOULD NOT APPEAR"}],
        )
        content = result["content"]
        assert len(content) == 1
        assert "SHOULD NOT APPEAR" not in content[0]["text"]
        assert "File downloaded." in content[0]["text"]
        assert "1 MB" in content[0]["text"]

    def test_summary_no_isError_flag(self) -> None:
        result = build_response(
            response_mode="summary",
            metadata="meta",
            next_step="ok",
            payload_items=[],
        )
        assert "isError" not in result


# ---------------------------------------------------------------------------
# build_response -- edge cases
# ---------------------------------------------------------------------------

class TestBuildResponseEdgeCases:

    def test_empty_payload_items(self) -> None:
        result = build_response(
            response_mode="full",
            metadata="meta",
            next_step="hint",
            payload_items=[],
        )
        assert len(result["content"]) == 1

    def test_metadata_trailing_newlines_stripped(self) -> None:
        result = build_response(
            response_mode="full",
            metadata="line1\n\n\n",
            next_step="hint",
            payload_items=[],
        )
        text = result["content"][0]["text"]
        # Should not have excessive newlines between metadata and next_step
        assert "\n\n\n\n" not in text
