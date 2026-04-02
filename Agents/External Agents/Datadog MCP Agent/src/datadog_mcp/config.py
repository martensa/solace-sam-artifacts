"""Configuration for the Datadog MCP server."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


class DatadogConfig(BaseModel):
    """Datadog API configuration loaded from environment variables."""

    api_key: str = Field(description="Datadog API key")
    app_key: str = Field(description="Datadog Application key")
    site: str = Field(default="datadoghq.com", description="Datadog site")

    @property
    def base_url(self) -> str:
        return f"https://api.{self.site}"

    def __repr__(self) -> str:
        return f"DatadogConfig(api_key='***', app_key='***', site='{self.site}')"

    def __str__(self) -> str:
        return self.__repr__()

    @classmethod
    def from_env(cls) -> DatadogConfig:
        api_key = os.environ.get("DD_API_KEY", "")
        app_key = os.environ.get("DD_APP_KEY", "")
        site = os.environ.get("DD_SITE", "datadoghq.com")

        if not api_key:
            raise ValueError("DD_API_KEY environment variable is required")
        if not app_key:
            raise ValueError("DD_APP_KEY environment variable is required")

        return cls(api_key=api_key, app_key=app_key, site=site)
