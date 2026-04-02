"""Shared test fixtures for Datadog MCP server tests."""

from __future__ import annotations

import os
import pytest
import httpx
import respx

from datadog_mcp.config import DatadogConfig
from datadog_mcp.client import DatadogClient


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure DD env vars are set for all tests."""
    monkeypatch.setenv("DD_API_KEY", "test-api-key")
    monkeypatch.setenv("DD_APP_KEY", "test-app-key")
    monkeypatch.setenv("DD_SITE", "datadoghq.com")


@pytest.fixture
def config() -> DatadogConfig:
    return DatadogConfig(api_key="test-api-key", app_key="test-app-key", site="datadoghq.com")


@pytest.fixture
def dd_client(config: DatadogConfig) -> DatadogClient:
    return DatadogClient(config)


@pytest.fixture
def mock_api() -> respx.MockRouter:
    """Create a respx mock router for Datadog API calls."""
    with respx.mock(base_url="https://api.datadoghq.com") as router:
        yield router
