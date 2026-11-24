"""Authenticated OpenRouter generation-usage lookup.

This adapter is deliberately narrower than the chat transport. It accepts one
durable OpenRouter generation identity and returns token counters only. Cost
fields and upstream provider identities are not projected into the Kernel's
token-usage contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import re
from typing import Any, Mapping

import httpx

from pulse_harness.core.errors import ProviderProtocolError, ProviderUnavailable
from pulse_harness.core.provider import ProviderUsage, validate_provider_response_id


OPENROUTER_GENERATION_ENDPOINT = "https://openrouter.ai/api/v1/generation"
OPENROUTER_CHAT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_USAGE_CAPABILITY = "usage_lookup:openrouter-generation-v1"


@dataclass(frozen=True, slots=True)
class OpenRouterUsageConfig:
    """Operator-owned credential and resource bounds for usage lookup."""

    credential_env: str = "P_PULSE_API_KEY"
    timeout_seconds: float = 30.0
    max_response_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.credential_env):
            raise ValueError("credential environment variable must be valid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if type(self.max_response_bytes) is not int or self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")


def _decode_json(payload: bytes) -> Any:
    def reject(constant: str) -> None:
        raise ValueError(f"non-finite JSON: {constant}")

    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        text = payload.decode("utf-8")
        return json.loads(
            text,
            parse_constant=reject,
            object_pairs_hook=unique,
        )
    except (UnicodeError, ValueError):
        raise ProviderProtocolError(
            "OpenRouter usage lookup returned invalid JSON",
            code="provider_usage_lookup_invalid",
        ) from None


def _counter(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ProviderProtocolError(
            f"OpenRouter usage {field} must be a non-negative integer",
            code="provider_usage_lookup_invalid",
        )
    return value


class OpenRouterUsageReconciler:
    """Lookup exact token usage by OpenRouter generation ID."""

    capability = OPENROUTER_USAGE_CAPABILITY

    def __init__(
        self,
        config: OpenRouterUsageConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or OpenRouterUsageConfig()
        self._client = httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._closed = False

    def lookup(self, provider_response_id: str) -> ProviderUsage:
        if self._closed:
            raise ProviderUnavailable(
                "OpenRouter usage reconciler is closed",
                code="provider_closed",
            )
        generation_id = validate_provider_response_id(provider_response_id)
        credential = os.environ.get(self.config.credential_env)
        if not credential:
            raise ProviderUnavailable(
                "configured OpenRouter credential is missing",
                code="provider_credential_missing",
            )

        try:
            with self._client.stream(
                "GET",
                OPENROUTER_GENERATION_ENDPOINT,
                params={"id": generation_id},
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Accept": "application/json",
                },
            ) as response:
                if response.status_code != 200:
                    raise ProviderUnavailable(
                        f"OpenRouter usage endpoint returned HTTP {response.status_code}",
                        code=f"provider_usage_lookup_http_{response.status_code}",
                    )
                if "application/json" not in response.headers.get(
                    "content-type", ""
                ).lower():
                    raise ProviderProtocolError(
                        "OpenRouter usage endpoint did not return JSON",
                        code="provider_usage_lookup_invalid",
                    )
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > self.config.max_response_bytes:
                        raise ProviderProtocolError(
                            "OpenRouter usage response exceeded configured byte bound",
                            code="provider_usage_lookup_response_too_large",
                        )
                    chunks.append(chunk)
        except httpx.HTTPError:
            raise ProviderUnavailable(
                "OpenRouter usage lookup transport failed",
                code="provider_usage_lookup_transport_error",
            ) from None

        payload = _decode_json(b"".join(chunks))
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("data"), Mapping
        ):
            raise ProviderProtocolError(
                "OpenRouter usage response has no data object",
                code="provider_usage_lookup_invalid",
            )
        data = payload["data"]
        try:
            returned_id = validate_provider_response_id(data.get("id"))
        except (TypeError, ValueError):
            raise ProviderProtocolError(
                "OpenRouter usage response has an invalid generation identity",
                code="provider_usage_lookup_invalid",
            ) from None
        if returned_id != generation_id:
            raise ProviderProtocolError(
                "OpenRouter usage response identity does not match the requested generation",
                code="provider_usage_lookup_identity_mismatch",
            )
        if "tokens_prompt" not in data or "tokens_completion" not in data:
            raise ProviderProtocolError(
                "OpenRouter usage response is missing token counters",
                code="provider_usage_lookup_invalid",
            )
        input_tokens = _counter(data["tokens_prompt"], "tokens_prompt")
        output_tokens = _counter(data["tokens_completion"], "tokens_completion")
        cached_value = data.get("native_tokens_cached", 0)
        cached_tokens = (
            0
            if cached_value is None
            else _counter(cached_value, "native_tokens_cached")
        )
        try:
            return ProviderUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                evidence_class="PROVIDER_REPORTED_OPENROUTER_GENERATION_V1",
            )
        except ValueError as exc:
            raise ProviderProtocolError(
                "OpenRouter usage counters are inconsistent",
                code="provider_usage_lookup_invalid",
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def __enter__(self) -> "OpenRouterUsageReconciler":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
