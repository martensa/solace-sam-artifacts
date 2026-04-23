"""LLM-based result validation for price comparison offers.

After the deterministic pipeline assembles candidate offers, a single
LLM call inspects the top-N offers and flags ones that clearly do not
match the query (wrong SKU, variant, accessory, bundle). This is the
final gate before results are returned to the user.

The validator uses the same LiteLLM proxy the rest of SAM uses. It is
OPTIONAL -- if `PRICE_LLM_VALIDATOR_ENABLED` is false or the endpoint
is unreachable, validation is skipped and offers pass through unchanged.

Design notes:
  - Strictly structured JSON output with per-offer verdicts.
  - Runs on top-N (default 8) offers only; deeper offers are already
    low confidence and the LLM call would add latency for little gain.
  - Uses fast model (claude-sonnet-4-6) with tight max_tokens budget.
  - 8s timeout; on timeout we skip validation and keep offers.
  - Never mutates rejected offers -- only tags `llm_verdict` and
    optionally downgrades `match_confidence` to "low" so they sort last.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger("price-comparison-mcp.validator")


_SYSTEM_PROMPT = """\
You are a strict product-matching validator for a B2B procurement price
comparison tool. For each candidate offer, decide whether it matches the
user's query (exact SKU or clearly the same product) or not (wrong
variant, wrong product, accessory, bundle, different SKU).

Output STRICT JSON only, no markdown, no prose:
{"verdicts": [
  {"idx": 0, "match": "yes"|"no"|"unsure", "reason": "<=80 chars"},
  ...
]}

Rules:
- "yes" = same SKU / same product. Variant codes (SCH/FTT, DE/EU) must
  match. Model numbers (1674FC, WRL 200.400 F) must match.
- "no" = clearly different SKU / variant / accessory / bundle.
- "unsure" = cannot tell from title alone, or looks like a category page.
- Brand-only matches (only the manufacturer name matches) are "no".
- If the title contains the exact model number AND the query model
  number, answer "yes" unless a different variant suffix is present.
"""


class LLMValidator:
    """Optional LLM-based post-filter for candidate offers."""

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        model: str = "openai/claude-sonnet-4-6",
        timeout: float = 8.0,
        max_offers: int = 8,
    ) -> None:
        self._endpoint = (endpoint or "").rstrip("/")
        self._api_key = api_key or ""
        # LiteLLM SDK conventions prefix provider (e.g. "openai/claude-sonnet-4-6").
        # When calling the LiteLLM proxy via raw HTTP, the JSON body needs the
        # plain model name -- the provider prefix is SDK-only metadata and
        # triggers "team_model_access_denied" on the proxy otherwise.
        if model and "/" in model:
            model = model.split("/", 1)[1]
        self._model = model
        self._timeout = timeout
        self._max_offers = max_offers
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def available(self) -> bool:
        return bool(self._endpoint and self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def validate(
        self,
        query: str,
        offers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return offers with `llm_verdict` field added on top-N.

        Does NOT remove offers. Offers verdicted "no" have their
        `match_confidence` forced to "low" so the downstream sort
        pushes them to the end. Offers verdicted "yes" get a bump
        from "medium" / "unknown" to "high" (but never downgrade
        an existing "exact" / "high" tag).
        """
        if not self.available or not offers:
            return offers

        # Only validate the top N candidates by current rank to keep
        # latency bounded. Rest pass through unchanged.
        candidates = offers[: self._max_offers]
        if not candidates:
            return offers

        user_prompt = self._build_user_prompt(query, candidates)

        try:
            client = await self._get_client()
            payload = {
                "model": self._model,
                "temperature": 0.0,
                "max_tokens": 800,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            }
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            response = await client.post(
                f"{self._endpoint}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            logger.warning("LLM validator timed out, skipping validation")
            return offers
        except Exception as e:
            logger.warning("LLM validator failed: %s", e)
            return offers

        try:
            content = data["choices"][0]["message"]["content"]
            verdicts = self._parse_verdicts(content)
        except (KeyError, IndexError, ValueError) as e:
            logger.warning("LLM validator: bad response shape: %s", e)
            return offers

        # Apply verdicts to candidates. Leave trailing offers untouched.
        for idx, offer in enumerate(candidates):
            verdict = verdicts.get(idx)
            if not verdict:
                continue
            match = verdict.get("match", "unsure")
            reason = verdict.get("reason", "")
            offer["llm_verdict"] = match
            if reason:
                offer["llm_reason"] = reason[:120]
            current_conf = (offer.get("match_confidence") or "").lower()
            if match == "no" and current_conf != "exact":
                offer["match_confidence"] = "low"
            elif match == "yes" and current_conf in ("", "medium", "unknown"):
                offer["match_confidence"] = "high"

        n_yes = sum(1 for v in verdicts.values() if v.get("match") == "yes")
        n_no = sum(1 for v in verdicts.values() if v.get("match") == "no")
        logger.info(
            "LLM validator: %d verdicts (yes=%d, no=%d) for query '%s'",
            len(verdicts), n_yes, n_no, query[:60],
        )
        return offers

    def _build_user_prompt(
        self,
        query: str,
        offers: list[dict[str, Any]],
    ) -> str:
        lines = [f"Query: {query}", "", "Candidates:"]
        for idx, offer in enumerate(offers):
            merchant = offer.get("merchant", "")
            price = offer.get("total_price") or offer.get("price") or 0.0
            title = offer.get("page_title", "") or ""
            url = offer.get("url", "")
            # Compact line-per-offer, cap lengths
            lines.append(
                f"[{idx}] {merchant} | {price:.2f} EUR | "
                f"title: {title[:120]} | url: {url[:120]}"
            )
        lines.append("")
        lines.append("Respond with JSON as instructed.")
        return "\n".join(lines)

    @staticmethod
    def _parse_verdicts(content: str) -> dict[int, dict[str, str]]:
        """Parse LLM output into {idx: {match, reason}}. Robust to stray text."""
        # Find first JSON object in content
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        result: dict[int, dict[str, str]] = {}
        for v in data.get("verdicts", []):
            if not isinstance(v, dict):
                continue
            try:
                idx = int(v.get("idx"))
            except (TypeError, ValueError):
                continue
            m = str(v.get("match", "unsure")).lower()
            if m not in ("yes", "no", "unsure"):
                m = "unsure"
            reason = str(v.get("reason", ""))
            result[idx] = {"match": m, "reason": reason}
        return result

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def create_validator_from_env() -> Optional[LLMValidator]:
    """Instantiate an LLMValidator from environment variables.

    Returns None if validator is disabled. Reuses the same LLM proxy
    the SAM runtime uses (LLM_SERVICE_ENDPOINT / LLM_SERVICE_API_KEY)
    to avoid any new credentials.
    """
    enabled = os.environ.get("PRICE_LLM_VALIDATOR_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        return None
    endpoint = os.environ.get("LLM_SERVICE_ENDPOINT", "").rstrip("/")
    api_key = os.environ.get("LLM_SERVICE_API_KEY", "")
    model = os.environ.get(
        "PRICE_LLM_VALIDATOR_MODEL",
        os.environ.get("LLM_SERVICE_GENERAL_MODEL_NAME", "openai/claude-sonnet-4-6"),
    )
    if not endpoint or not api_key:
        return None
    timeout_s = float(os.environ.get("PRICE_LLM_VALIDATOR_TIMEOUT_SECONDS", "8.0"))
    max_offers = int(os.environ.get("PRICE_LLM_VALIDATOR_MAX_OFFERS", "8"))
    return LLMValidator(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout=timeout_s,
        max_offers=max_offers,
    )
