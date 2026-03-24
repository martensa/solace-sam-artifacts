"""Async HTTP client for the Datadog REST API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from datadog_mcp.config import DatadogConfig


class DatadogClient:
    """Thin async wrapper around the Datadog REST API (v1 + v2)."""

    def __init__(self, config: DatadogConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers={
                    "DD-API-KEY": self._config.api_key,
                    "DD-APPLICATION-KEY": self._config.app_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # -- Generic request helpers---------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an API request with rate-limit and transient-error retry."""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self.client.request(
                    method, path, params=params, json=json_body
                )
            except httpx.TransportError as exc:
                last_exc = exc
                await asyncio.sleep(min(2**attempt, 8))
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("x-ratelimit-reset", "5"))
                await asyncio.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"Server error {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
                await asyncio.sleep(min(2**attempt, 8))
                continue
            resp.raise_for_status()
            if resp.status_code == 204:
                return {"status": "ok"}
            try:
                return resp.json()
            except (ValueError, KeyError) as exc:
                raise RuntimeError(
                    f"Invalid JSON response from {path} (status {resp.status_code})"
                ) from exc

        if last_exc is not None:
            raise RuntimeError(f"Request failed after 3 retries: {last_exc}") from last_exc
        raise RuntimeError("Request failed after 3 retries")

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        cleaned = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=cleaned)

    async def post(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> dict[str, Any]:
        cleaned = {k: v for k, v in params.items() if v is not None}
        return await self._request("POST", path, params=cleaned, json_body=body)

    async def put(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("PUT", path, json_body=body)

    async def patch(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("PATCH", path, json_body=body)

    async def delete(self, path: str, **params: Any) -> dict[str, Any]:
        cleaned = {k: v for k, v in params.items() if v is not None}
        return await self._request("DELETE", path, params=cleaned)
