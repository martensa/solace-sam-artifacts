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

    # Browser pool -- must accommodate the peak simultaneous contexts
    # created by the batch pipeline (batch_concurrency=3 items *
    # inner concurrent_fetches=3 per item = 9 contexts at peak).
    max_contexts: int = 10
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
        if v < 1 or v > 20:
            raise ValueError("max_contexts must be between 1 and 20")
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
    # Additional structured-shopping API keys. Each one is optional -
    # when empty the corresponding client becomes a no-op. Order of
    # preference: SearXNG shopping (free) -> SerpAPI -> Serper -> Brave
    # -> Apify (slowest, paid credits).
    brave_api_key: str = ""
    serper_api_key: str = ""
    apify_token: str = ""
    # Quality-first defaults (B2B primary use case). Trading a few seconds
    # for better distributor coverage and fewer "not found" responses.
    max_detail_urls: int = 10
    max_detail_urls_per_domain: int = 1  # Diversity at fetch time
    max_offers_per_domain: int = 4        # Diversity in final output
    detail_timeout_seconds: int = 18      # Some B2B shops are slow
    # 110s gives the batch tool enough headroom to fetch 8 URLs per
    # item (was 5) without shrinking per-URL timeouts below 17s.
    # Covers specialist B2B distributors (voltus, elektro4000,
    # contorion, mercateo) that often rank outside SearXNG top-5.
    total_timeout_seconds: int = 110
    cache_ttl_seconds: int = 1800         # B2B prices don't change per-minute
    concurrent_fetches: int = 4           # browser contexts allow

    # ---------------------------------------------------------------------
    # v1.0 feature flags -- category-aware pipeline switches.
    # Defaults are set so that alpha3 and later run the category pipeline
    # live. Anyone who needs the legacy v2.3.5 behaviour back sets the
    # flag to false.
    # ---------------------------------------------------------------------
    # If true: classify every query, select a CategoryProfile from the
    # registry, and thread it through scoring / penalty / aggregator
    # resolution. When false, profile=None everywhere (pre-v1.0 behaviour).
    enable_categories: bool = True
    # If true: reject queries that look like a GTIN / ISBN but fail the
    # checksum (saves 60-75s of Playwright work on typo barcodes).
    enable_ean_fastfail: bool = True
    # If true: use locale/templates.py to produce query expansions.
    # Falls back to "{q} Preis kaufen" when false.
    enable_locale_templates: bool = True
    # If true: title_gate_antilex from the profile downgrades offers to
    # mc=low when forbidden words appear in the page title.
    enable_antilex_gate: bool = True

    # LLM classifier Stage-3 settings (wired in alpha5; declared here
    # so alpha3/alpha4 don't churn config again).
    enable_classifier_llm: bool = True
    classifier_llm_max_latency_ms: int = 3000
    # Heuristic confidence threshold below which Stage-3 LLM fires.
    classifier_llm_min_confidence: float = 0.5

    # LLM reranker (wired in alpha5; starts OFF).
    enable_llm_reranker: bool = False

    # Domain discovery (wired in beta1; starts ON with soft SQLite fallback).
    enable_domain_discovery: bool = True
    domain_stats_db_path: str = "/app/data/domain_stats.db"
    domain_stats_s3_snapshot: bool = True


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
