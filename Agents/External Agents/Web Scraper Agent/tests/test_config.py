"""Tests for config.py -- BrowserConfig and module-level settings."""

from __future__ import annotations

import pytest

from web_scraper_mcp.config import STEALTH_ARGS, USER_AGENTS, BrowserConfig


class TestBrowserConfigDefaults:

    def test_default_headless(self) -> None:
        cfg = BrowserConfig()
        assert cfg.headless is True

    def test_default_browser_type(self) -> None:
        cfg = BrowserConfig()
        assert cfg.browser_type == "chromium"

    def test_default_locale(self) -> None:
        cfg = BrowserConfig()
        assert cfg.locale == "de-DE"

    def test_default_timezone(self) -> None:
        cfg = BrowserConfig()
        assert cfg.timezone == "Europe/Berlin"

    def test_default_stealth_enabled(self) -> None:
        cfg = BrowserConfig()
        assert cfg.stealth_enabled is True

    def test_default_per_domain_delay(self) -> None:
        cfg = BrowserConfig()
        assert cfg.per_domain_delay_seconds == 3.0

    def test_default_max_contexts(self) -> None:
        cfg = BrowserConfig()
        assert cfg.max_contexts == 3


class TestBrowserConfigValidation:

    def test_invalid_browser_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="browser_type"):
            BrowserConfig(browser_type="netscape")

    def test_valid_browser_types(self) -> None:
        for bt in ("chromium", "firefox", "webkit"):
            cfg = BrowserConfig(browser_type=bt)
            assert cfg.browser_type == bt

    def test_delay_too_low_rejected(self) -> None:
        with pytest.raises(ValueError, match="per_domain_delay"):
            BrowserConfig(per_domain_delay_seconds=0.01)

    def test_delay_too_high_rejected(self) -> None:
        with pytest.raises(ValueError, match="per_domain_delay"):
            BrowserConfig(per_domain_delay_seconds=61)

    def test_max_contexts_too_low(self) -> None:
        with pytest.raises(ValueError, match="max_contexts"):
            BrowserConfig(max_contexts=0)

    def test_max_contexts_too_high(self) -> None:
        with pytest.raises(ValueError, match="max_contexts"):
            BrowserConfig(max_contexts=11)


class TestBrowserConfigMethods:

    def test_random_delay_in_range(self) -> None:
        cfg = BrowserConfig(min_delay_ms=500, max_delay_ms=3000)
        for _ in range(50):
            d = cfg.random_delay()
            assert 0.5 <= d <= 3.0

    def test_random_viewport_valid(self) -> None:
        vp = BrowserConfig.random_viewport()
        assert "width" in vp
        assert "height" in vp
        assert 1280 <= vp["width"] <= 1920
        assert 720 <= vp["height"] <= 1080


class TestStealthArgs:

    def test_stealth_args_is_list(self) -> None:
        assert isinstance(STEALTH_ARGS, list)
        assert len(STEALTH_ARGS) > 0

    def test_all_args_are_strings(self) -> None:
        for arg in STEALTH_ARGS:
            assert isinstance(arg, str)
            assert arg.startswith("--")


class TestUserAgents:

    def test_pool_has_enough_agents(self) -> None:
        assert len(USER_AGENTS) >= 10

    def test_all_agents_are_strings(self) -> None:
        for ua in USER_AGENTS:
            assert isinstance(ua, str)
            assert "Mozilla" in ua

    def test_multiple_browsers_represented(self) -> None:
        has_chrome = any("Chrome/" in ua and "Edg" not in ua for ua in USER_AGENTS)
        has_edge = any("Edg/" in ua for ua in USER_AGENTS)
        has_firefox = any("Firefox/" in ua for ua in USER_AGENTS)
        has_safari = any("Safari/" in ua and "Chrome" not in ua for ua in USER_AGENTS)
        assert has_chrome
        assert has_edge
        assert has_firefox
        assert has_safari


class TestMCPSettings:

    def test_mcp_max_response_chars_default(self) -> None:
        from web_scraper_mcp.config import MCP_MAX_RESPONSE_CHARS
        # Default is 25000 unless overridden by env
        assert isinstance(MCP_MAX_RESPONSE_CHARS, int)
        assert MCP_MAX_RESPONSE_CHARS > 0

    def test_mcp_log_level_default(self) -> None:
        from web_scraper_mcp.config import MCP_LOG_LEVEL
        assert MCP_LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
