"""Configuration for the Price Comparison MCP Server."""

from __future__ import annotations

import os
import random

from pydantic import field_validator
from pydantic_settings import BaseSettings

# MCP-level settings (no prefix -- shared SAM convention)
MCP_MAX_RESPONSE_CHARS = int(os.environ.get("MCP_MAX_RESPONSE_CHARS", "50000"))
MCP_LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO")
MCP_LOG_FILE = os.environ.get("MCP_LOG_FILE", "")


class BrowserConfig(BaseSettings):
    """Browser and stealth settings loaded from environment variables."""

    model_config = {"env_prefix": "WEB_SCRAPER_"}

    # Browser
    headless: bool = True
    browser_type: str = "chromium"
    locale: str = "de-DE"
    timezone: str = "Europe/Berlin"

    # Stealth
    stealth_enabled: bool = True
    min_delay_ms: int = 300
    max_delay_ms: int = 1500

    # Rate limiting -- faster than Web Scraper (price pages are simpler)
    per_domain_delay_seconds: float = 1.0

    # Browser pool -- more contexts for parallel price fetching
    max_contexts: int = 5
    context_idle_timeout: int = 120

    # Timeouts
    default_timeout_ms: int = 15000
    navigation_timeout_ms: int = 20000

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


class PriceSearchConfig(BaseSettings):
    """Price search pipeline settings loaded from environment variables."""

    model_config = {"env_prefix": "PRICE_"}

    searxng_url: str = "http://searxng.sam-solace-lab-shared.svc.cluster.local:8080"
    serpapi_key: str = ""
    max_detail_urls: int = 5
    detail_timeout_seconds: int = 15
    total_timeout_seconds: int = 55
    cache_ttl_seconds: int = 300
    concurrent_fetches: int = 3


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

# User agents pool for rotation
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
]
