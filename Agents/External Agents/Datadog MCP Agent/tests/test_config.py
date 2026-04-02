"""Tests for configuration module."""

import os
import pytest

from datadog_mcp.config import DatadogConfig


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", "my-api-key")
    monkeypatch.setenv("DD_APP_KEY", "my-app-key")
    monkeypatch.setenv("DD_SITE", "datadoghq.eu")

    config = DatadogConfig.from_env()
    assert config.api_key == "my-api-key"
    assert config.app_key == "my-app-key"
    assert config.site == "datadoghq.eu"
    assert config.base_url == "https://api.datadoghq.eu"


def test_config_default_site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", "key")
    monkeypatch.setenv("DD_APP_KEY", "app")
    monkeypatch.delenv("DD_SITE", raising=False)

    config = DatadogConfig.from_env()
    assert config.site == "datadoghq.com"


def test_config_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DD_API_KEY", raising=False)
    monkeypatch.setenv("DD_APP_KEY", "app")
    with pytest.raises(ValueError, match="DD_API_KEY"):
        DatadogConfig.from_env()


def test_config_missing_app_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", "key")
    monkeypatch.delenv("DD_APP_KEY", raising=False)
    with pytest.raises(ValueError, match="DD_APP_KEY"):
        DatadogConfig.from_env()


def test_config_repr_masks_keys() -> None:
    """Ensure API keys are never exposed in repr/str output."""
    config = DatadogConfig(api_key="super-secret-key", app_key="super-secret-app")
    text = repr(config)
    assert "super-secret-key" not in text
    assert "super-secret-app" not in text
    assert "***" in text
    # str() should also be safe
    assert "super-secret-key" not in str(config)
