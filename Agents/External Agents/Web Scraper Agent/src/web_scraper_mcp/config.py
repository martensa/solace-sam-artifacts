"""Configuration for the Web Scraper MCP Server."""

from __future__ import annotations

import os
import random

from pydantic import field_validator
from pydantic_settings import BaseSettings

# MCP-level settings (no WEB_SCRAPER_ prefix -- shared SAM convention)
MCP_MAX_RESPONSE_CHARS = int(os.environ.get("MCP_MAX_RESPONSE_CHARS", "25000"))
MCP_LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO")
MCP_LOG_FILE = os.environ.get("MCP_LOG_FILE", "")


class BrowserConfig(BaseSettings):
    """Browser and stealth settings loaded from environment variables."""

    model_config = {"env_prefix": "WEB_SCRAPER_"}

    # Browser
    headless: bool = True
    browser_type: str = "chromium"  # chromium, firefox, webkit
    locale: str = "de-DE"
    timezone: str = "Europe/Berlin"

    # Stealth
    stealth_enabled: bool = True
    min_delay_ms: int = 500
    max_delay_ms: int = 3000

    # Image processing
    image_max_width: int = 1200
    image_max_height: int = 1200
    image_quality: int = 90
    image_default_format: str = "png"
    image_strip_metadata: bool = True

    # Rate limiting
    per_domain_delay_seconds: float = 3.0

    # Browser pool
    max_contexts: int = 3
    context_idle_timeout: int = 300

    # Timeouts
    default_timeout_ms: int = 30000
    navigation_timeout_ms: int = 60000

    @field_validator("browser_type")
    @classmethod
    def _validate_browser_type(cls, v: str) -> str:
        allowed = {"chromium", "firefox", "webkit"}
        if v not in allowed:
            raise ValueError(f"browser_type must be one of {sorted(allowed)}, got: {v}")
        return v

    @field_validator("per_domain_delay_seconds")
    @classmethod
    def _validate_delay(cls, v: float) -> float:
        if v < 0.1 or v > 60:
            raise ValueError("per_domain_delay_seconds must be between 0.1 and 60")
        return v

    @field_validator("max_contexts")
    @classmethod
    def _validate_max_contexts(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError("max_contexts must be between 1 and 10")
        return v

    def random_delay(self) -> float:
        """Return a random delay in seconds for human-like behavior."""
        return random.randint(self.min_delay_ms, self.max_delay_ms) / 1000.0

    @staticmethod
    def random_viewport() -> dict[str, int]:
        """Return a randomized but realistic viewport size."""
        viewports = [
            {"width": 1920, "height": 1080},
            {"width": 1536, "height": 864},
            {"width": 1440, "height": 900},
            {"width": 1366, "height": 768},
            {"width": 1280, "height": 720},
        ]
        return random.choice(viewports)


# Stealth browser launch arguments
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

# User agents pool for rotation (diverse OS + browser versions)
USER_AGENTS = [
    # Chrome 135 -- macOS / Windows / Linux
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    # Chrome 134
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    # Chrome 133
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    # Edge (Chromium) 135 / 134
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    # Firefox 136 / 135 (only useful if browser_type=firefox)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",
    # Safari 18 (only useful if browser_type=webkit)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
]

# Known tracking/placeholder domains to ignore when extracting images
IGNORED_IMAGE_DOMAINS = [
    "pixel.",
    "tracker.",
    "analytics.",
    "doubleclick.net",
    "google-analytics.com",
    "facebook.com/tr",
    "bat.bing.com",
]

# CSS selectors commonly used for product images (priority order)
PRODUCT_IMAGE_SELECTORS = [
    "[data-testid='product-image'] img",
    ".product-image img",
    ".product-gallery img",
    ".gallery-image img",
    "#main-image img",
    "#product-image img",
    ".product-detail img",
    "[class*='product'] [class*='image'] img",
    "[class*='gallery'] img",
    "article img",
]
