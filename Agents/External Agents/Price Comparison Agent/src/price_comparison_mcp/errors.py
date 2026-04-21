"""Structured error taxonomy for programmatic error handling by orchestrators.

Each ToolError carries:
- code:     Machine-readable string (e.g. "NETWORK_TIMEOUT", "NO_PRICES_FOUND").
- category: Broad classification for routing decisions.
- message:  Human-readable detail.
- retryable: Whether the orchestrator should consider retrying.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """Broad error categories for orchestrator routing."""

    NETWORK = "network"
    BOT_PROTECTION = "bot_protection"
    NOT_FOUND = "not_found"
    VALIDATION = "validation"
    EXTRACTION = "extraction"
    SERVER = "server"
    INTERNAL = "internal"


class ErrorCode(str, Enum):
    """Machine-readable error codes."""

    # Network
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    CONNECTION_RESET = "CONNECTION_RESET"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    DNS_RESOLUTION_FAILED = "DNS_RESOLUTION_FAILED"

    # Bot protection
    BOT_BLOCKED = "BOT_BLOCKED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"

    # Not found
    HTTP_404 = "HTTP_404"
    HTTP_410 = "HTTP_410"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    NO_PRICES_FOUND = "NO_PRICES_FOUND"

    # Validation
    INVALID_URL = "INVALID_URL"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    INVALID_PARAMETER = "INVALID_PARAMETER"

    # Server errors
    HTTP_5XX = "HTTP_5XX"
    SEARXNG_UNAVAILABLE = "SEARXNG_UNAVAILABLE"
    SERPAPI_ERROR = "SERPAPI_ERROR"

    # Extraction
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    EXTRACTION_TIMEOUT = "EXTRACTION_TIMEOUT"

    # Internal
    BROWSER_CRASHED = "BROWSER_CRASHED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Mapping from error code to (category, retryable)
_ERROR_META: dict[ErrorCode, tuple[ErrorCategory, bool]] = {
    ErrorCode.NETWORK_TIMEOUT: (ErrorCategory.NETWORK, True),
    ErrorCode.CONNECTION_RESET: (ErrorCategory.NETWORK, True),
    ErrorCode.CONNECTION_REFUSED: (ErrorCategory.NETWORK, True),
    ErrorCode.DNS_RESOLUTION_FAILED: (ErrorCategory.NETWORK, False),
    ErrorCode.BOT_BLOCKED: (ErrorCategory.BOT_PROTECTION, False),
    ErrorCode.CAPTCHA_REQUIRED: (ErrorCategory.BOT_PROTECTION, False),
    ErrorCode.LOGIN_REQUIRED: (ErrorCategory.BOT_PROTECTION, False),
    ErrorCode.HTTP_404: (ErrorCategory.NOT_FOUND, False),
    ErrorCode.HTTP_410: (ErrorCategory.NOT_FOUND, False),
    ErrorCode.ELEMENT_NOT_FOUND: (ErrorCategory.NOT_FOUND, False),
    ErrorCode.NO_PRICES_FOUND: (ErrorCategory.NOT_FOUND, False),
    ErrorCode.INVALID_URL: (ErrorCategory.VALIDATION, False),
    ErrorCode.SSRF_BLOCKED: (ErrorCategory.VALIDATION, False),
    ErrorCode.INVALID_PARAMETER: (ErrorCategory.VALIDATION, False),
    ErrorCode.HTTP_5XX: (ErrorCategory.SERVER, True),
    ErrorCode.SEARXNG_UNAVAILABLE: (ErrorCategory.SERVER, True),
    ErrorCode.SERPAPI_ERROR: (ErrorCategory.SERVER, False),
    ErrorCode.EXTRACTION_FAILED: (ErrorCategory.EXTRACTION, False),
    ErrorCode.EXTRACTION_TIMEOUT: (ErrorCategory.NETWORK, False),
    ErrorCode.BROWSER_CRASHED: (ErrorCategory.INTERNAL, True),
    ErrorCode.INTERNAL_ERROR: (ErrorCategory.INTERNAL, False),
}


def tool_error(code: ErrorCode, message: str) -> dict[str, Any]:
    """Build a structured MCP tool error result."""
    category, retryable = _ERROR_META[code]
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
        "error_code": code.value,
        "error_category": category.value,
        "retryable": retryable,
    }


def classify_playwright_error(error_msg: str) -> ErrorCode:
    """Classify a Playwright/network error string into an ErrorCode."""
    lower = error_msg.lower()

    if "timeout" in lower:
        return ErrorCode.NETWORK_TIMEOUT
    if "net::err_connection_reset" in lower:
        return ErrorCode.CONNECTION_RESET
    if "net::err_connection_refused" in lower:
        return ErrorCode.CONNECTION_REFUSED
    if "net::err_name_not_resolved" in lower:
        return ErrorCode.DNS_RESOLUTION_FAILED
    if "net::err_aborted" in lower:
        return ErrorCode.CONNECTION_RESET
    if "captcha" in lower:
        return ErrorCode.CAPTCHA_REQUIRED
    if "login required" in lower:
        return ErrorCode.LOGIN_REQUIRED
    if "page crashed" in lower or "browser has been closed" in lower:
        return ErrorCode.BROWSER_CRASHED

    return ErrorCode.INTERNAL_ERROR
