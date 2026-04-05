"""Tests for validation.py -- URL validation and SSRF protection."""

from __future__ import annotations

import ipaddress

import pytest

from web_scraper_mcp.validation import (
    ALLOWED_PORTS,
    _is_dangerous_ip,
    validate_output_format,
    validate_timeout,
    validate_url,
    validate_viewport,
)

# ---------------------------------------------------------------------------
# validate_url -- valid cases
# ---------------------------------------------------------------------------

class TestValidateUrlValid:
    """URLs that should pass validation."""

    @pytest.mark.parametrize("url", [
        "https://www.example.com",
        "https://example.com/path/to/page",
        "http://example.com:80/page",
        "https://example.com:443/page",
        "https://example.com:8080/page",
        "https://example.com:8443/api/v1",
        "https://shop.example.com/product?id=123&lang=de",
        "https://example.com/file.pdf",
    ])
    def test_valid_urls_pass(self, url: str) -> None:
        assert validate_url(url) is None

    def test_url_without_port_passes(self) -> None:
        """URLs without explicit port should always pass port check."""
        assert validate_url("https://example.com/page") is None


# ---------------------------------------------------------------------------
# validate_url -- scheme validation
# ---------------------------------------------------------------------------

class TestValidateUrlScheme:

    @pytest.mark.parametrize("url", [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<h1>hi</h1>",
        "",
    ])
    def test_blocked_schemes(self, url: str) -> None:
        result = validate_url(url)
        assert result is not None

    def test_empty_string(self) -> None:
        assert validate_url("") is not None

    def test_none_rejected(self) -> None:
        assert validate_url(None) is not None  # type: ignore[arg-type]

    def test_too_long_url(self) -> None:
        url = "https://example.com/" + "a" * 4100
        assert validate_url(url) is not None


# ---------------------------------------------------------------------------
# validate_url -- SSRF protection
# ---------------------------------------------------------------------------

class TestValidateUrlSSRF:

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/admin",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.1/private",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/admin",
        "http://localhost/admin",
        "http://metadata.google.internal/computeMetadata/v1/",
    ])
    def test_private_ips_blocked(self, url: str) -> None:
        result = validate_url(url)
        assert result is not None

    def test_blocked_hostname_case_insensitive(self) -> None:
        assert validate_url("http://LOCALHOST/admin") is not None

    @pytest.mark.parametrize("port", [6379, 9200, 27017, 3306, 5432, 11211, 22])
    def test_dangerous_ports_blocked(self, port: int) -> None:
        url = f"https://example.com:{port}/path"
        result = validate_url(url)
        assert result is not None
        assert str(port) in result

    @pytest.mark.parametrize("port", sorted(ALLOWED_PORTS))
    def test_allowed_ports_pass(self, port: int) -> None:
        url = f"https://example.com:{port}/path"
        assert validate_url(url) is None


# ---------------------------------------------------------------------------
# _is_dangerous_ip
# ---------------------------------------------------------------------------

class TestIsDangerousIp:

    def test_loopback_v4(self) -> None:
        assert _is_dangerous_ip(ipaddress.ip_address("127.0.0.1")) is True

    def test_loopback_v6(self) -> None:
        assert _is_dangerous_ip(ipaddress.ip_address("::1")) is True

    def test_private_v4(self) -> None:
        assert _is_dangerous_ip(ipaddress.ip_address("10.0.0.1")) is True
        assert _is_dangerous_ip(ipaddress.ip_address("192.168.0.1")) is True

    def test_carrier_grade_nat(self) -> None:
        assert _is_dangerous_ip(ipaddress.ip_address("100.64.0.1")) is True
        assert _is_dangerous_ip(ipaddress.ip_address("100.127.255.254")) is True

    def test_ipv6_mapped_ipv4_private(self) -> None:
        # ::ffff:127.0.0.1 should be blocked
        addr = ipaddress.ip_address("::ffff:127.0.0.1")
        assert _is_dangerous_ip(addr) is True

    def test_ipv6_mapped_ipv4_public(self) -> None:
        # ::ffff:8.8.8.8 should NOT be blocked
        addr = ipaddress.ip_address("::ffff:8.8.8.8")
        assert _is_dangerous_ip(addr) is False

    def test_public_ip_allowed(self) -> None:
        assert _is_dangerous_ip(ipaddress.ip_address("8.8.8.8")) is False
        assert _is_dangerous_ip(ipaddress.ip_address("1.1.1.1")) is False


# ---------------------------------------------------------------------------
# validate_timeout
# ---------------------------------------------------------------------------

class TestValidateTimeout:

    def test_valid_range(self) -> None:
        assert validate_timeout(5) is None
        assert validate_timeout(30) is None
        assert validate_timeout(120) is None

    def test_too_low(self) -> None:
        assert validate_timeout(4) is not None
        assert validate_timeout(0) is not None

    def test_too_high(self) -> None:
        assert validate_timeout(121) is not None

    def test_float_accepted(self) -> None:
        assert validate_timeout(30.5) is None

    def test_string_rejected(self) -> None:
        assert validate_timeout("30") is not None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# validate_viewport
# ---------------------------------------------------------------------------

class TestValidateViewport:

    def test_valid_viewport(self) -> None:
        assert validate_viewport(1920, 1080) is None

    def test_minimum_valid(self) -> None:
        assert validate_viewport(320, 240) is None

    def test_maximum_valid(self) -> None:
        assert validate_viewport(3840, 2160) is None

    def test_width_too_small(self) -> None:
        assert validate_viewport(319, 1080) is not None

    def test_height_too_large(self) -> None:
        assert validate_viewport(1920, 2161) is not None

    def test_non_integer_rejected(self) -> None:
        assert validate_viewport(1920.5, 1080) is not None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# validate_output_format
# ---------------------------------------------------------------------------

class TestValidateOutputFormat:

    @pytest.mark.parametrize("fmt", ["png", "jpg", "webp"])
    def test_valid_formats(self, fmt: str) -> None:
        assert validate_output_format(fmt) is None

    @pytest.mark.parametrize("fmt", ["gif", "bmp", "tiff", "svg", "PNG", ""])
    def test_invalid_formats(self, fmt: str) -> None:
        assert validate_output_format(fmt) is not None
