"""Tests for server.py -- MCP protocol handling, tool dispatch, truncation."""

from __future__ import annotations

from web_scraper_mcp.errors import ErrorCode, tool_error
from web_scraper_mcp.server import (
    TOOL_HANDLERS,
    TOOLS,
    _truncate_response,
    _validate_tool_arguments,
    error_response,
    result_response,
)

# ---------------------------------------------------------------------------
# Tool registry consistency
# ---------------------------------------------------------------------------

class TestToolRegistry:

    def test_all_tools_have_handlers(self) -> None:
        tool_names = {t["name"] for t in TOOLS}
        handler_names = set(TOOL_HANDLERS.keys())
        assert tool_names == handler_names

    def test_all_tools_have_input_schema(self) -> None:
        for tool in TOOLS:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_all_tools_have_response_mode(self) -> None:
        for tool in TOOLS:
            props = tool["inputSchema"]["properties"]
            assert "response_mode" in props
            assert props["response_mode"]["enum"] == ["full", "summary"]

    def test_tool_count(self) -> None:
        assert len(TOOLS) == 5


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

class TestJsonRpcHelpers:

    def test_result_response_format(self) -> None:
        resp = result_response(1, {"tools": []})
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert "result" in resp

    def test_error_response_format(self) -> None:
        resp = error_response(2, -32601, "Not found")
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 2
        assert resp["error"]["code"] == -32601

    def test_tool_error_result(self) -> None:
        result = tool_error(ErrorCode.INTERNAL_ERROR, "Something failed")
        assert result["isError"] is True
        assert result["content"][0]["text"] == "Something failed"
        assert result["error_code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# _truncate_response
# ---------------------------------------------------------------------------

class TestTruncateResponse:

    def test_short_response_unchanged(self) -> None:
        result = {"content": [{"type": "text", "text": "hello"}]}
        assert _truncate_response(result) == result

    def test_long_text_truncated(self) -> None:
        # Create response with text exceeding the limit
        from web_scraper_mcp.config import MCP_MAX_RESPONSE_CHARS
        long_text = "x" * (MCP_MAX_RESPONSE_CHARS + 1000)
        result = {"content": [{"type": "text", "text": long_text}]}
        truncated = _truncate_response(result)
        text = truncated["content"][0]["text"]
        assert len(text) < len(long_text)
        assert "TRUNCATED" in text

    def test_image_items_preserved(self) -> None:
        result = {
            "content": [
                {"type": "text", "text": "meta"},
                {"type": "image", "data": "base64stuff", "mimeType": "image/png"},
            ]
        }
        truncated = _truncate_response(result)
        assert truncated["content"][1]["type"] == "image"

    def test_no_content_key_handled(self) -> None:
        result = {"other": "data"}
        assert _truncate_response(result) == result


# ---------------------------------------------------------------------------
# _validate_tool_arguments
# ---------------------------------------------------------------------------

class TestValidateToolArguments:

    def test_valid_fetch_args(self) -> None:
        err = _validate_tool_arguments("fetch_protected_webpage", {
            "url": "https://example.com",
            "timeout_seconds": 30,
        })
        assert err is None

    def test_missing_url_rejected(self) -> None:
        err = _validate_tool_arguments("fetch_protected_webpage", {})
        assert err is not None
        assert "url" in err.lower()

    def test_invalid_url_rejected(self) -> None:
        err = _validate_tool_arguments("fetch_protected_webpage", {
            "url": "ftp://evil.com",
        })
        assert err is not None

    def test_invalid_response_mode_rejected(self) -> None:
        err = _validate_tool_arguments("fetch_protected_webpage", {
            "url": "https://example.com",
            "response_mode": "reference",
        })
        assert err is not None

    def test_valid_search_args(self) -> None:
        err = _validate_tool_arguments("search_and_download_image", {
            "search_query": "OSRAM LED bulb",
        })
        assert err is None

    def test_short_search_query_rejected(self) -> None:
        err = _validate_tool_arguments("search_and_download_image", {
            "search_query": "a",
        })
        assert err is not None

    def test_invalid_timeout_rejected(self) -> None:
        err = _validate_tool_arguments("fetch_protected_webpage", {
            "url": "https://example.com",
            "timeout_seconds": 999,
        })
        assert err is not None

    def test_invalid_max_width_rejected(self) -> None:
        err = _validate_tool_arguments("download_product_image", {
            "url": "https://example.com/img.jpg",
            "max_width": 10000,
        })
        assert err is not None

    def test_valid_screenshot_args(self) -> None:
        err = _validate_tool_arguments("screenshot_webpage", {
            "url": "https://example.com",
            "viewport_width": 1920,
            "viewport_height": 1080,
        })
        assert err is None

    def test_invalid_viewport_rejected(self) -> None:
        err = _validate_tool_arguments("screenshot_webpage", {
            "url": "https://example.com",
            "viewport_width": 100,
        })
        assert err is not None

    def test_download_file_valid(self) -> None:
        err = _validate_tool_arguments("download_file", {
            "url": "https://example.com/doc.pdf",
        })
        assert err is None

    def test_unknown_tool_passes(self) -> None:
        """Unknown tools should not fail validation (handler check is separate)."""
        err = _validate_tool_arguments("nonexistent_tool", {"url": "https://x.com"})
        assert err is None
