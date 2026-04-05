"""Input validation and SSRF protection for the Web Scraper MCP Server."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Allowed URL schemes
ALLOWED_SCHEMES = {"http", "https"}

# Allowed destination ports (common web ports only)
ALLOWED_PORTS = {80, 443, 8080, 8443}

# Output formats
ALLOWED_FORMATS = {"png", "jpg", "webp"}

# Blocked hostnames (metadata endpoints, localhost, etc.)
BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",  # AWS/GCP metadata
    "100.100.100.200",  # Alibaba metadata
    "fd00:ec2::254",    # AWS IPv6 metadata
}


def _is_dangerous_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP address should be blocked (SSRF protection)."""
    # Check IPv6-mapped IPv4 addresses first (e.g. ::ffff:127.0.0.1).
    # Python marks all ::ffff:x.x.x.x as is_reserved, so we must check the
    # mapped IPv4 address instead of the IPv6 wrapper.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return _is_dangerous_ip(addr.ipv4_mapped)
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return True
    # Block carrier-grade NAT range (100.64.0.0/10)
    if isinstance(addr, ipaddress.IPv4Address):
        if addr in ipaddress.IPv4Network("100.64.0.0/10"):
            return True
    return False


def validate_url(url: str) -> str | None:
    """Validate URL for safety. Returns error message or None if valid."""
    if not url or not isinstance(url, str):
        return "URL must be a non-empty string"

    if len(url) > 4096:
        return "URL exceeds maximum length (4096 characters)"

    try:
        parsed = urlparse(url)
    except Exception:
        return f"Invalid URL: {url[:100]}"

    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"Only http/https URLs are allowed, got: {parsed.scheme or '(empty)'}"

    if not parsed.netloc:
        return "Invalid URL: missing hostname"

    hostname = parsed.hostname
    if not hostname:
        return "Invalid URL: missing hostname"

    # Port validation -- only allow common web ports
    port = parsed.port
    if port is not None and port not in ALLOWED_PORTS:
        return f"Port {port} is not allowed. Allowed ports: {sorted(ALLOWED_PORTS)}"

    # Block known metadata/localhost endpoints
    if hostname.lower() in BLOCKED_HOSTNAMES:
        return f"Blocked hostname: {hostname}"

    # Block private/internal IPs to prevent SSRF
    try:
        addr = ipaddress.ip_address(hostname)
        if _is_dangerous_ip(addr):
            return f"Private/internal IP addresses are not allowed: {hostname}"
    except ValueError:
        # Not an IP literal -- that's fine, it's a domain name.
        # We do a DNS check for the most common SSRF bypass (DNS rebinding is
        # harder to prevent fully, but we block obvious cases).
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in resolved:
                ip_str = sockaddr[0]
                try:
                    addr = ipaddress.ip_address(ip_str)
                    if _is_dangerous_ip(addr):
                        return f"Hostname {hostname} resolves to private IP {ip_str}"
                except ValueError:
                    continue
        except socket.gaierror:
            # DNS resolution failed -- let the browser handle it (it will fail too)
            pass

    return None


def validate_timeout(timeout: int | float) -> str | None:
    """Validate timeout value. Returns error message or None if valid."""
    if not isinstance(timeout, (int, float)):
        return "timeout_seconds must be a number"
    if timeout < 5 or timeout > 120:
        return "timeout_seconds must be between 5 and 120"
    return None


def validate_viewport(width: int, height: int) -> str | None:
    """Validate viewport dimensions. Returns error message or None if valid."""
    if not isinstance(width, int) or not isinstance(height, int):
        return "Viewport dimensions must be integers"
    if width < 320 or width > 3840:
        return f"viewport_width must be between 320 and 3840, got {width}"
    if height < 240 or height > 2160:
        return f"viewport_height must be between 240 and 2160, got {height}"
    return None


def validate_output_format(fmt: str) -> str | None:
    """Validate image output format. Returns error message or None if valid."""
    if fmt not in ALLOWED_FORMATS:
        return f"output_format must be one of {sorted(ALLOWED_FORMATS)}, got: {fmt}"
    return None
