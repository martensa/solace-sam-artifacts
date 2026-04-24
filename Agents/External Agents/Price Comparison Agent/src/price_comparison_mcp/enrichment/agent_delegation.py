"""Inter-agent delegation to EANSearchAgent (capability-gated, opt-in).

When OpenFoodFacts + Wikidata both miss for a valid barcode, we can
optionally delegate to a peer SAM agent (EANSearchAgent) that has
paid-backend access (ean-search.org / UPCitemdb). This runs in the
same K8s cluster and is reached via the SAM Gateway's REST API.

Feature-flagged and disabled by default:
  - env `PRICE_ENABLE_AGENT_DELEGATION` must be "true"
  - env `PRICE_SAM_GATEWAY_URL` must be set to the in-cluster svc URL
    (e.g. "http://agent-mesh.sam-solace-lab.svc.cluster.local")
  - env `PRICE_EAN_AGENT_NAME` default "EANSearchAgent"

Protocol:
  1. POST /api/v1/message:send  -> { task_id }
  2. poll GET /api/v1/tasks/<id> until "COMPLETED" or timeout
  3. extract product text, run the same brand + name heuristic as
     OpenFoodFacts / Wikidata.

Fail-silent: any non-200 / timeout / unparseable response yields None.
The caller continues with whatever hint they have from earlier tiers.

Public surface:
  is_enabled()                               -> bool
  async fetch(client, ean, *, timeout=8.0)   -> EnrichmentResult | None
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Any

import httpx

from .openfoodfacts import EnrichmentResult

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment + defaults
# ---------------------------------------------------------------------------

_ENV_ENABLE = "PRICE_ENABLE_AGENT_DELEGATION"
_ENV_GATEWAY = "PRICE_SAM_GATEWAY_URL"
_ENV_AGENT_NAME = "PRICE_EAN_AGENT_NAME"

DEFAULT_TIMEOUT_S = 8.0        # Total budget for send + poll cycle.
DEFAULT_POLL_INTERVAL_S = 0.5  # Poll cadence between status checks.
DEFAULT_AGENT_NAME = "EANSearchAgent"


def is_enabled() -> bool:
    """True iff the delegation feature flag is set AND gateway URL exists."""
    flag = (os.environ.get(_ENV_ENABLE) or "").strip().lower()
    gateway = (os.environ.get(_ENV_GATEWAY) or "").strip()
    return flag in ("true", "1", "yes", "on") and bool(gateway)


def _gateway_url() -> str:
    return (os.environ.get(_ENV_GATEWAY) or "").rstrip("/")


def _agent_name() -> str:
    return (os.environ.get(_ENV_AGENT_NAME) or DEFAULT_AGENT_NAME).strip()


# ---------------------------------------------------------------------------
# Response parsing: extract brand + name from EANSearchAgent's text output.
# ---------------------------------------------------------------------------


# Heuristic patterns -- EANSearchAgent typically returns lines like:
#   "Product: Bosch Akku-Schrauber IXO 3.6V"
#   "Brand: Bosch"
#   "Name: Akku-Schrauber IXO"
# or a free-form paragraph. We accept either.
_RE_BRAND = re.compile(r"(?:Brand|Marke|Hersteller)\s*:\s*(?P<v>[^\n\r]+)", re.I)
_RE_NAME = re.compile(r"(?:Product|Name|Produktname|Title)\s*:\s*(?P<v>[^\n\r]+)", re.I)


def _parse_response(text: str, ean: str) -> EnrichmentResult | None:
    """Map EANSearchAgent's text response onto EnrichmentResult."""
    if not text:
        return None
    brand = ""
    name = ""
    m = _RE_BRAND.search(text)
    if m:
        brand = m.group("v").strip().strip("`\"'")
    m = _RE_NAME.search(text)
    if m:
        name = m.group("v").strip().strip("`\"'")
    # Fallback: if neither structured field was present, take the first
    # non-empty line as the product name.
    if not brand and not name:
        first_line = next(
            (ln.strip() for ln in text.splitlines() if ln.strip()),
            "",
        )
        if first_line and len(first_line) < 200:
            name = first_line
    if not brand and not name:
        return None
    return EnrichmentResult(
        source="ean_search_agent",
        ean=ean,
        brand=brand,
        product_name=name,
        quantity="",
        category_hint="",
    )


# ---------------------------------------------------------------------------
# Gateway client
# ---------------------------------------------------------------------------


async def _send_message(
    client: httpx.AsyncClient,
    ean: str,
    *,
    timeout: float,
) -> str | None:
    """POST /api/v1/message:send -> return task_id or None on failure."""
    url = f"{_gateway_url()}/api/v1/message:send"
    msg_id = f"price-delegation-{uuid.uuid4().hex[:12]}"
    payload = {
        "id": msg_id,
        "params": {
            "message": {
                "role": "user",
                "parts": [{
                    "kind": "text",
                    "text": (
                        f"Look up EAN/GTIN barcode {ean}. Return the "
                        f"product name and brand as structured fields "
                        f"('Brand:' and 'Product:' lines)."
                    ),
                }],
                "messageId": msg_id,
                "metadata": {"agent_name": _agent_name()},
            },
        },
    }
    try:
        resp = await client.post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        log.debug("agent_delegation send error: %s", exc)
        return None
    if resp.status_code != 200:
        log.debug("agent_delegation send status=%d", resp.status_code)
        return None
    try:
        data = resp.json()
    except Exception as exc:
        log.debug("agent_delegation send non-json: %s", exc)
        return None
    # Gateway returns { "result": { "task_id": ... } } or { "task_id": ... }
    task_id = (
        (data.get("result") or {}).get("task_id")
        or data.get("task_id")
        or data.get("id")
    )
    if not task_id:
        log.debug("agent_delegation send: no task_id in %s", list(data.keys()))
        return None
    return str(task_id)


async def _poll_task(
    client: httpx.AsyncClient,
    task_id: str,
    *,
    deadline: float,
    interval: float,
) -> str | None:
    """GET /api/v1/tasks/<id> in a loop; return response text or None."""
    url = f"{_gateway_url()}/api/v1/tasks/{task_id}"
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None
        try:
            resp = await client.get(url, timeout=min(remaining, 3.0))
        except httpx.HTTPError as exc:
            log.debug("agent_delegation poll error: %s", exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        # The gateway response shape: invocation_flow -> events ->
        # direction=response -> payload.result.status.message.parts[].text
        # We walk leniently: any parts[*].text we find concatenated.
        text = _extract_response_text(data)
        status = _extract_status(data)
        if text and status in ("completed", "COMPLETED", "done"):
            return text
        if status in ("failed", "FAILED", "error"):
            return None
        await asyncio.sleep(interval)


def _extract_status(data: Any) -> str:
    """Walk the response dict looking for the terminal status field."""
    try:
        flow = data.get("invocation_flow") or data.get("flow") or []
        if isinstance(flow, list) and flow:
            last = flow[-1]
            if isinstance(last, dict):
                payload = last.get("payload") or {}
                result = payload.get("result") or {}
                status = result.get("status") or {}
                if isinstance(status, dict):
                    return (status.get("state") or status.get("status") or "").lower()
                if isinstance(status, str):
                    return status.lower()
        return str(data.get("status", "")).lower()
    except Exception:
        return ""


def _extract_response_text(data: Any) -> str:
    """Concatenate every `parts[*].text` we can find in the payload."""
    chunks: list[str] = []
    try:
        flow = data.get("invocation_flow") or data.get("flow") or []
        for evt in flow if isinstance(flow, list) else []:
            if not isinstance(evt, dict):
                continue
            if (evt.get("direction") or "").lower() != "response":
                continue
            payload = evt.get("payload") or {}
            result = payload.get("result") or {}
            status = result.get("status") or {}
            message = (
                status.get("message") if isinstance(status, dict) else None
            ) or result.get("message") or {}
            parts = message.get("parts") if isinstance(message, dict) else None
            if isinstance(parts, list):
                for p in parts:
                    if isinstance(p, dict):
                        t = p.get("text")
                        if isinstance(t, str) and t.strip():
                            chunks.append(t)
    except Exception:
        pass
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def fetch(
    client: httpx.AsyncClient | None,
    ean: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
) -> EnrichmentResult | None:
    """Delegate an EAN lookup to the configured peer agent.

    Returns None if:
      - the feature is disabled (no env flag / no gateway URL);
      - the EAN is not a plausible GTIN;
      - send / poll fails or the task errors out;
      - the response text cannot be parsed into brand + name.
    """
    if not is_enabled():
        return None
    if not ean or not ean.isdigit() or len(ean) not in (8, 12, 13, 14):
        return None

    owns_client = client is None
    c = client or httpx.AsyncClient()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    try:
        task_id = await _send_message(c, ean, timeout=min(timeout, 5.0))
        if not task_id:
            return None
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        text = await _poll_task(
            c, task_id,
            deadline=deadline,
            interval=poll_interval,
        )
        if not text:
            return None
        return _parse_response(text, ean)
    except asyncio.TimeoutError:
        log.debug("agent_delegation total budget exceeded")
        return None
    except Exception as exc:
        log.debug("agent_delegation error: %s", exc)
        return None
    finally:
        if owns_client:
            await c.aclose()
