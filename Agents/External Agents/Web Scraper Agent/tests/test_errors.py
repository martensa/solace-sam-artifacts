"""Tests for errors.py -- structured error taxonomy."""

from __future__ import annotations

import pytest

from web_scraper_mcp.errors import (
    _ERROR_META,
    ErrorCategory,
    ErrorCode,
    classify_playwright_error,
    tool_error,
)

# ---------------------------------------------------------------------------
# tool_error() output structure
# ---------------------------------------------------------------------------

class TestToolError:

    def test_has_required_fields(self) -> None:
        result = tool_error(ErrorCode.NETWORK_TIMEOUT, "timed out")
        assert result["isError"] is True
        assert result["error_code"] == "NETWORK_TIMEOUT"
        assert result["error_category"] == "network"
        assert isinstance(result["retryable"], bool)
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "timed out"

    def test_retryable_codes(self) -> None:
        retryable = [c for c, (_, r) in _ERROR_META.items() if r]
        for code in retryable:
            result = tool_error(code, "msg")
            assert result["retryable"] is True, f"{code} should be retryable"

    def test_non_retryable_codes(self) -> None:
        non_retryable = [c for c, (_, r) in _ERROR_META.items() if not r]
        for code in non_retryable:
            result = tool_error(code, "msg")
            assert result["retryable"] is False, f"{code} should not be retryable"

    def test_all_error_codes_have_metadata(self) -> None:
        for code in ErrorCode:
            assert code in _ERROR_META, f"{code} missing from _ERROR_META"

    def test_all_categories_are_valid(self) -> None:
        for code, (category, _) in _ERROR_META.items():
            assert isinstance(category, ErrorCategory)


# ---------------------------------------------------------------------------
# classify_playwright_error()
# ---------------------------------------------------------------------------

class TestClassifyPlaywrightError:

    @pytest.mark.parametrize("msg, expected", [
        ("Timeout 30000ms exceeded", ErrorCode.NETWORK_TIMEOUT),
        ("net::ERR_CONNECTION_RESET", ErrorCode.CONNECTION_RESET),
        ("net::ERR_CONNECTION_REFUSED at https://x.com", ErrorCode.CONNECTION_REFUSED),
        ("net::ERR_NAME_NOT_RESOLVED", ErrorCode.DNS_RESOLUTION_FAILED),
        ("net::ERR_ABORTED", ErrorCode.CONNECTION_RESET),
        ("CAPTCHA detected on page", ErrorCode.CAPTCHA_REQUIRED),
        ("Login required to access this page", ErrorCode.LOGIN_REQUIRED),
        ("Navigation failed because page crashed", ErrorCode.BROWSER_CRASHED),
        ("browser has been closed", ErrorCode.BROWSER_CRASHED),
        ("some unknown error xyz", ErrorCode.INTERNAL_ERROR),
    ])
    def test_classification(self, msg: str, expected: ErrorCode) -> None:
        assert classify_playwright_error(msg) == expected

    def test_case_insensitive(self) -> None:
        assert classify_playwright_error("TIMEOUT exceeded") == ErrorCode.NETWORK_TIMEOUT
        assert classify_playwright_error("Net::ERR_CONNECTION_RESET") == ErrorCode.CONNECTION_RESET


# ---------------------------------------------------------------------------
# ErrorCode / ErrorCategory enum values
# ---------------------------------------------------------------------------

class TestEnums:

    def test_error_codes_are_uppercase(self) -> None:
        for code in ErrorCode:
            assert code.value == code.value.upper()

    def test_categories_are_lowercase(self) -> None:
        for cat in ErrorCategory:
            assert cat.value == cat.value.lower()
