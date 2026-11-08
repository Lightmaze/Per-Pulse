"""HTTP Chat Completions transport for the existing Pulse-owned TurnRunner.

This adapter owns only wire translation and a closable response stream. The
kernel still owns transcripts, tool execution, approval and settlement. Its
capability evidence describes this implementation, not a tested remote model.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from pulse_harness.life.tools import LIFE_SCHEMAS
from pulse_harness.adapters.command import COMMAND_SCHEMA
from pulse_harness.adapters.command_output import NAME as COMMAND_OUTPUT_NAME, SCHEMA as COMMAND_OUTPUT_SCHEMA
from pulse_harness.adapters.history import HISTORY_SCHEMAS
from pulse_harness.core.contracts import ToolCall
from pulse_harness.core.errors import ProviderProtocolError, ProviderUnavailable
from pulse_harness.core.provider import ModelDelta, ModelDeltaKind, ModelRequest, ProviderEvidence
from pulse_harness.core.provider import ProviderUsage, validate_provider_response_id


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _decode(value: str) -> Any:
    def reject(constant: str) -> None:
        raise ValueError(f"non-finite JSON: {constant}")
    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, val in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = val
        return out
    return json.loads(value, parse_constant=reject, object_pairs_hook=unique)


def _usage_count(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ProviderProtocolError(
            f"model usage {field} must be a non-negative integer",
            code="provider_usage_invalid",
        )
    return value


def _google_tool_metadata(extra: Any) -> dict[str, Any]:
    """Keep Google's opaque tool signature, without forwarding arbitrary fields."""
    if extra is None:
        return {}
    if not isinstance(extra, Mapping):
        raise ProviderProtocolError("model tool extra_content must be an object")
    google = extra.get("google")
    if google is None:
        return {}
    if not isinstance(google, Mapping):
        raise ProviderProtocolError("model Google tool metadata must be an object")
    signature = google.get("thought_signature")
    if signature is None:
        return {}
    if not isinstance(signature, str) or not signature:
        raise ProviderProtocolError("model thought signature must be non-empty text")
    return {"google": {"thought_signature": signature}}


def _append_reasoning_details(target: list[dict[str, Any]], fragments: Any) -> None:
    """Reassemble adjacent text/summary deltas; keep encrypted blobs opaque.

    OpenRouter's streaming indices can repeat across different block types.
    Follow type transitions and signature closure, as its official SDK does,
    rather than collapsing all index=0 blocks into one.
    """
    if not isinstance(fragments, list):
        raise ProviderProtocolError("model reasoning_details must be an array")
    for fragment in fragments:
        if not isinstance(fragment, dict) or not isinstance(fragment.get("type"), str):
            raise ProviderProtocolError("model reasoning detail must be a typed object")
        if any(fragment.get(key) is not None and not isinstance(fragment[key], str)
               for key in ("text", "summary", "signature", "data")):
            raise ProviderProtocolError("model reasoning detail payload must be text")
        previous = target[-1] if target else None
        field = {"reasoning.text": "text", "reasoning.summary": "summary"}.get(fragment["type"])
        if (field and previous and previous.get("type") == fragment["type"]
                and not (field == "text" and previous.get("signature"))):
            for key, value in fragment.items():
                if key == field and isinstance(value, str):
                    previous[key] = (previous.get(key) or "") + value
                elif previous.get(key) is None:
                    previous[key] = value
        else:
            target.append(dict(fragment))


def _provider_usage(value: Mapping[str, Any]) -> ProviderUsage:
    """Normalize an OpenAI-compatible usage envelope without estimating."""

    if "prompt_tokens" not in value or "completion_tokens" not in value:
        raise ProviderProtocolError(
            "model usage is missing prompt or completion tokens",
            code="provider_usage_invalid",
        )
    input_tokens = _usage_count(value["prompt_tokens"], "prompt_tokens")
    output_tokens = _usage_count(value["completion_tokens"], "completion_tokens")
    total = value.get("total_tokens")
    if total is not None and _usage_count(total, "total_tokens") != input_tokens + output_tokens:
        raise ProviderProtocolError(
            "model usage total does not match prompt plus completion tokens",
            code="provider_usage_invalid",
        )
    cached_candidates: list[int] = []
    details = value.get("prompt_tokens_details")
    if details is not None:
        if not isinstance(details, Mapping):
            raise ProviderProtocolError(
                "model usage prompt token details must be an object",
                code="provider_usage_invalid",
            )
        if details.get("cached_tokens") is not None:
            cached_candidates.append(
                _usage_count(details["cached_tokens"], "prompt_tokens_details.cached_tokens")
            )
    if value.get("prompt_cache_hit_tokens") is not None:
        cached_candidates.append(
            _usage_count(value["prompt_cache_hit_tokens"], "prompt_cache_hit_tokens")
        )
    if len(set(cached_candidates)) > 1:
        raise ProviderProtocolError(
            "model usage reports conflicting cached token counts",
            code="provider_usage_invalid",
        )
    cached_tokens = cached_candidates[0] if cached_candidates else 0
    cache_write_tokens = (
        _usage_count(value["cache_creation_input_tokens"], "cache_creation_input_tokens")
        if value.get("cache_creation_input_tokens") is not None
        else 0
    )
    try:
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            evidence_class="PROVIDER_REPORTED_CHAT_HTTP_V1",
        )
    except ValueError as exc:
        raise ProviderProtocolError(
            "model usage counters are inconsistent",
            code="provider_usage_invalid",
        ) from exc


OUTPUT_LIMIT_NOTE = "\n\n[Stopped at the reply length limit. Ask to continue for the rest.]"

# A request the service refused or never answered is sent again, as the
# official OpenAI and Anthropic clients do: a busy (429) or failing (5xx)
# service, a timed-out (408) or conflicting (409) request, or a connection
# that failed before any reply. The wait doubles from half a second up to
# eight seconds, with jitter, unless the service asks for a wait of up to a
# minute. Once a reply starts it is never sent again.
RETRY_STATUSES = frozenset({408, 409, 429})
INITIAL_RETRY_SECONDS = 0.5
MAX_RETRY_SECONDS = 8.0
MAX_RETRY_AFTER_SECONDS = 60.0


def _retryable(response: httpx.Response) -> bool:
    should = response.headers.get("x-should-retry", "").strip().lower()
    if should in {"true", "false"}:
        return should == "true"
    return response.status_code in RETRY_STATUSES or response.status_code >= 500


def _retry_after(headers: httpx.Headers) -> float | None:
    """The wait the service asked for, in seconds, if it asked for one."""
    try:
        if "retry-after-ms" in headers:
            return float(headers["retry-after-ms"]) / 1000
    except ValueError:
        pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).timestamp() - time.time()
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay(retry: int, headers: httpx.Headers | None = None) -> float:
    """Seconds to wait before retry number ``retry`` (1 for the first)."""
    asked = _retry_after(headers) if headers is not None else None
    if asked is not None and math.isfinite(asked) and 0 <= asked <= MAX_RETRY_AFTER_SECONDS:
        return asked
    delay = min(INITIAL_RETRY_SECONDS * 2 ** (retry - 1), MAX_RETRY_SECONDS)
    return delay * (1 - 0.25 * random.random())


@dataclass(frozen=True)
class ChatHTTPConfig:
    # Full operator-selected URL, not an untrusted model-generated destination.
    endpoint: str
    model: str
    credential_env: str = "P_PULSE_API_KEY"
    # Authentication for an operator-selected HTTPS provider. Azure OpenAI
    # API-key endpoints use the api-key header rather than Bearer.
    auth_style: str = "bearer"
    # Connect and idle timeout: the longest wait for the next bytes, not the
    # whole reply. A long reply keeps streaming as long as bytes keep coming.
    timeout_seconds: float = 120.0
    # Upper bound on one streamed reply, however steadily it arrives.
    max_stream_seconds: float = 1800.0
    # Times a refused or unanswered request is sent again (see RETRY_STATUSES).
    max_retries: int = 2
    max_output_tokens: int = 4096
    max_response_bytes: int = 2 * 1024 * 1024
    # Exact serialized HTTP JSON body bound; not a token or monetary budget.
    max_request_bytes: int = 8 * 1024 * 1024
    allow_local_http: bool = False
    reasoning_field: str | None = None
    provider_namespace: str | None = None
    # Explicitly allow a credential-free *loopback HTTP* model. This is never
    # inferred from a missing key and never enabled for a remote HTTPS host.
    no_auth_local: bool = False
    # Known official endpoints select their wire conventions automatically.
    # Proxies can explicitly choose a profile without changing destinations.
    provider_profile: str = "auto"

    def __post_init__(self) -> None:
        url = urlsplit(self.endpoint)
        local_http = (self.allow_local_http and url.scheme == "http"
                      and url.hostname in {"127.0.0.1", "::1", "localhost"})
        if (url.scheme != "https" and not local_http) or not url.hostname:
            raise ValueError("model endpoint requires HTTPS, or explicit loopback HTTP")
        if url.username or url.password or url.fragment:
            raise ValueError("model endpoint must not contain credentials or fragment")
        if url.query:
            # Azure's versioned Chat Completions endpoint needs a URL query.
            # Allow that non-secret parameter, never credentials in the URL.
            parameters = parse_qsl(url.query, keep_blank_values=True)
            if (len(parameters) != 1 or parameters[0][0] != "api-version"
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", parameters[0][1])):
                raise ValueError("model endpoint query supports api-version only; keep keys in the credential environment")
        if self.auth_style not in {"bearer", "api-key"}:
            raise ValueError("unsupported model authentication style")
        if not self.model.strip() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.credential_env):
            raise ValueError("model and credential environment variable must be valid")
        if type(self.no_auth_local) is not bool:
            raise ValueError("no_auth_local must be a boolean")
        if self.no_auth_local and (not local_http or self.provider_namespace is not None
                                   or self.auth_style != "bearer"):
            raise ValueError("credential-free model calls require explicitly enabled loopback HTTP only")
        for seconds in (self.timeout_seconds, self.max_stream_seconds):
            if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("timeout_seconds and max_stream_seconds must be finite and positive")
        for value in (self.max_output_tokens, self.max_response_bytes, self.max_request_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("token and response bounds must be positive integers")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 10:
            raise ValueError("max_retries must be an integer from 0 to 10")
        if self.reasoning_field not in {None, "reasoning_content"}:
            raise ValueError("unsupported reasoning wire field")
        if self.provider_profile not in {"auto", "compatible", "openai", "anthropic", "gemini", "deepseek", "openrouter"}:
            raise ValueError("unsupported Chat Completions provider profile")
        if self.provider_namespace not in {None, "openrouter"}:
            raise ValueError("unsupported provider namespace")
        if self.provider_namespace == "openrouter":
            if self.auth_style != "bearer":
                raise ValueError("OpenRouter namespace requires Bearer authentication")
            from pulse_harness.adapters.openrouter_usage import (
                OPENROUTER_CHAT_ENDPOINT,
            )

            if self.endpoint.rstrip("/") != OPENROUTER_CHAT_ENDPOINT:
                raise ValueError(
                    "OpenRouter namespace requires the canonical OpenRouter chat endpoint"
                )

    @property
    def resolved_profile(self) -> str:
        if self.provider_profile != "auto":
            return self.provider_profile
        host = urlsplit(self.endpoint).hostname or ""
        if host == "api.openai.com" or host.endswith(".openai.azure.com"):
            return "openai"
        if host == "api.anthropic.com":
            return "anthropic"
        if (host in {"generativelanguage.googleapis.com", "aiplatform.googleapis.com"}
                or host.endswith(".aiplatform.googleapis.com")
                or host.endswith("-aiplatform.googleapis.com")):
            return "gemini"
        if host == "api.deepseek.com":
            return "deepseek"
        if host == "openrouter.ai":
            return "openrouter"
        return "compatible"

    @property
    def reasoning_wire_field(self) -> str | None:
        return self.reasoning_field or (
            "reasoning_content" if self.resolved_profile == "deepseek"
            else "reasoning" if self.resolved_profile == "openrouter" else None
        )


WORKSPACE_SCHEMAS: dict[str, dict[str, Any]] = {
    **HISTORY_SCHEMAS,
    **LIFE_SCHEMAS,
    "run_command": COMMAND_SCHEMA,
    COMMAND_OUTPUT_NAME: COMMAND_OUTPUT_SCHEMA,
    "read_file": {
        "type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0, "default": 0,
                       "description": "Start in Unicode characters. Without expected_digest, reads the current file at this position."},
            "max_chars": {"type": "integer", "minimum": 1,
                          "description": "Maximum source characters to return; the payload allowance may shorten the passage."},
            "expected_digest": {"type": "string", "pattern": "^[a-f0-9]{64}$",
                                "description": "Optional source SHA-256. Use the returned next arguments to continue the same file version. On workspace_digest_conflict, restart with offset=0 and omit expected_digest."},
        },
        "required": ["path"], "additionalProperties": False,
    },
    "mutate_file": {
        # Anthropic rejects root oneOf/anyOf/allOf even on its compatibility
        # endpoint. StructuredWorkspaceToolProvider enforces the exclusive
        # choice before preview, approval or a physical file effect.
        "description": "Supply exactly one of content or patch, together with path and expected_digest.",
        "type": "object", "properties": {
            "path": {"type": "string"},
            "expected_digest": {"type": ["string", "null"],
                                "description": "Observed source SHA-256 from read_file or reading; null only for a new file."},
            "content": {"type": "string", "description": "Complete replacement UTF-8 content; omit when patch is used."},
            "patch": {
                "type": "object",
                "description": "Revise the same file with exact sequential text replacements, not a unified diff.",
                "properties": {"edits": {"type": "array", "minItems": 1,
                    "items": {"type": "object", "properties": {
                        "old": {"type": "string", "minLength": 1,
                                "description": "Exact observed text; must occur exactly once at this step."},
                        "new": {"type": "string", "description": "Replacement text; empty deletes the matched text."}},
                        "required": ["old", "new"], "additionalProperties": False}}},
                "required": ["edits"], "additionalProperties": False,
            },
        }, "required": ["path", "expected_digest"],
        "additionalProperties": False,
    },
}


class ChatHTTPModelAdapter:
    """Streaming network adapter; no fallback model, no tool execution.

    A request the service refuses or never answers is retried before any
    reply arrives (see RETRY_STATUSES); a reply that started is never resent.

    Supply schemas for every tool in the selected catalog. Credentials are
    resolved only when a request starts and never enter durable request data.
    The optional transport exists for HTTP contract tests, not a demo mode.
    """
    def __init__(self, config: ChatHTTPConfig, *,
                 tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
                 transport: httpx.BaseTransport | None = None,
                 attempt_accounting=None) -> None:
        self.workspace_image_resolver = None
        self.life_image_resolver = None  # native runtime may bind a scoped, ephemeral observation renderer
        self.config = config
        self._schemas = _decode(_json(dict(WORKSPACE_SCHEMAS if tool_schemas is None else tool_schemas)))
        self._client = httpx.Client(timeout=config.timeout_seconds, follow_redirects=False,
                                    trust_env=False, transport=transport)
        self._test_transport = transport is not None
        # Host-provided, trusted World admission for physical HTTP attempts.
        # Do not accept a budget or source marker from model-produced text.
        self._attempt_accounting = attempt_accounting
        self._streams: set[_ChatStream] = set()
        self._lock = threading.RLock()
        self._closed = False

    def bind_attempt_accounting(self, accounting) -> None:
        """Bind a trusted World gate before this adapter creates any streams.

        Only the host may supply the gate; provider messages and tool output
        never carry budget authority. A running adapter cannot switch pools.
        """
        if accounting is None:
            raise ValueError("a host request-accounting gate is required")
        with self._lock:
            if self._closed or self._streams:
                raise ProviderProtocolError("cannot bind accounting to an active model")
            if self._attempt_accounting is not None and self._attempt_accounting is not accounting:
                raise ProviderProtocolError("model request accounting is already bound")
            self._attempt_accounting = accounting

    def extend_tool_schemas(self, schemas: Mapping[str, Mapping[str, Any]]) -> None:
        """Host-only catalog extension, before the adapter starts streaming."""
        checked = _decode(_json(dict(schemas)))
        with self._lock:
            if self._closed or self._streams:
                raise ProviderProtocolError("cannot extend an active model catalog")
            for name, schema in checked.items():
                if not isinstance(name, str) or not isinstance(schema, dict) or schema.get("type") != "object":
                    raise ProviderProtocolError("invalid tool parameter schema")
                if name in self._schemas and self._schemas[name] != schema:
                    raise ProviderProtocolError("conflicting host tool schema")
            self._schemas.update(checked)

    @property
    def capabilities(self) -> ProviderEvidence:
        capabilities = {"streaming", "tool_calls", "cancel"}
        adapter_id = "p-pulse-chat-http"
        source = "transport-contract-v1; remote-endpoint-not-independently-verified"
        if self.config.provider_namespace == "openrouter":
            from pulse_harness.adapters.openrouter_usage import (
                OPENROUTER_USAGE_CAPABILITY,
            )

            capabilities.add(OPENROUTER_USAGE_CAPABILITY)
            adapter_id += "/openrouter"
            source += "; provider-namespace:openrouter"
        return ProviderEvidence(
            adapter_id=adapter_id, adapter_version="0.1.0",
            source=source,
            capabilities=frozenset(capabilities),
            verified=True, test_only=self._test_transport,
        )

    def preflight_stream(self) -> None:
        if self._closed:
            raise ProviderUnavailable("model adapter is closed", code="provider_closed")
        if not self.config.no_auth_local and not os.environ.get(self.config.credential_env):
            raise ProviderUnavailable("configured model credential is missing", code="provider_credential_missing")

    def request_body(self, request: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        transcript_messages: list[dict[str, Any]] = []
        reasoning = ""
        for record in request.messages:
            role, content = record.get("role"), record.get("content")
            if not isinstance(content, str):
                raise ProviderProtocolError("only text transcript messages are supported")
            if role == "reasoning":
                reasoning += content
                continue
            message: dict[str, Any] = {"role": role, "content": content}
            if role == "assistant":
                # Only the Kernel's projection of completed round authority
                # can identify tool envelopes. Untagged JSON is ordinary text.
                count = record.get("tool_call_count", 0)
                if type(count) is not int or count < 0:
                    raise ProviderProtocolError("invalid assistant tool-call authority")
                if count:
                    try:
                        payload = _decode(content)
                    except ValueError as exc:
                        raise ProviderProtocolError("invalid assistant tool transcript") from exc
                    if (not isinstance(payload, dict) or set(payload) != {"text", "tool_calls"}
                            or not isinstance(payload["text"], str) or not isinstance(payload["tool_calls"], list)
                            or len(payload["tool_calls"]) != count):
                        raise ProviderProtocolError("invalid assistant tool transcript")
                    message["content"] = payload["text"] or None
                    message["tool_calls"] = []
                    for call in payload["tool_calls"]:
                        if (not isinstance(call, dict) or not isinstance(call.get("tool_call_id"), str)
                                or not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict)):
                            raise ProviderProtocolError("invalid assistant tool transcript")
                        message["tool_calls"].append({"id": call["tool_call_id"], "type": "function",
                            "function": {"name": call["name"], "arguments": _json(call["arguments"])}})
                        if self.config.resolved_profile == "gemini":
                            metadata = _google_tool_metadata(call.get("provider_metadata"))
                            if metadata:
                                message["tool_calls"][-1]["extra_content"] = metadata
                        if self.config.resolved_profile == "openrouter":
                            metadata = call.get("provider_metadata", {}).get("openrouter", {})
                            if "reasoning_details" in metadata:
                                if not isinstance(metadata["reasoning_details"], list):
                                    raise ProviderProtocolError("retained reasoning_details must be an array")
                                message["reasoning_details"] = metadata["reasoning_details"]
                if self.config.reasoning_wire_field and (reasoning or message.get("tool_calls")):
                    message[self.config.reasoning_wire_field] = reasoning
                reasoning = ""
            elif role == "tool":
                try:
                    result = _decode(content)
                    message = {"role": "tool", "tool_call_id": result["tool_call_id"],
                               "content": _json({key: result.get(key) for key in ("status", "output", "error_code")})}
                    if not isinstance(message["tool_call_id"], str):
                        raise ValueError("invalid tool identity")
                except (ValueError, KeyError, TypeError):
                    raise ProviderProtocolError("invalid kernel tool-result transcript") from None
            elif role not in {"system", "user"}:
                raise ProviderProtocolError("unsupported transcript role")
            messages.append(message)
            if not str(record.get("item_id", "")).startswith("auxiliary-context:"):
                transcript_messages.append(message)
        token_field = "max_completion_tokens" if self.config.resolved_profile == "openai" else "max_tokens"
        body: dict[str, Any] = {"model": self.config.model, "messages": messages,
            "stream": True, token_field: self.config.max_output_tokens,
            "stream_options": {"include_usage": True}}
        if request.tools:
            body["tools"] = []
            for tool in request.tools:
                schema = self._schemas.get(tool.name)
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise ProviderProtocolError("tool parameter schema is missing", code="tool_schema_missing")
                body["tools"].append({"type": "function", "function": {
                    "name": tool.name, "description": tool.description, "parameters": schema}})
        if self.life_image_resolver is not None:
            # Render only current-round genuine life_frame tool results. Earlier
            # images do not get replayed at every later user turn. Pixels stay out
            # of the durable transcript; the reference, hash and source time stay.
            names = {}
            if transcript_messages and transcript_messages[-1]['role'] == 'tool':
                boundary = max((i for i,m in enumerate(transcript_messages) if m['role']=='assistant'), default=-1)
                for item in transcript_messages[max(0,boundary):]:
                    for call in item.get('tool_calls', []): names[call['id']] = call['function']['name']
                visual = []
                for item in transcript_messages[boundary+1:]:
                    if item['role'] != 'tool' or names.get(item.get('tool_call_id')) != 'life_frame': continue
                    result = _decode(item['content'])
                    output = result.get('output')
                    payload = output.get('life_image') if isinstance(output,dict) else None
                    if result.get('status') != 'succeeded': continue
                    if isinstance(payload,dict) and payload.get('kind')=='pulse_life_image.v1':
                        parts = self.life_image_resolver(request.scope, payload)
                        visual.extend(parts or [{'type':'text', 'text':'The referenced live image expired or was revoked; no pixels accompany this reference. Do not claim to have seen it.'}])
                        if len(visual) >= 16: break  # at most eight transient frames per model round
                if visual:
                    messages.append({'role':'user','content':visual})
        if self.workspace_image_resolver is not None and transcript_messages and transcript_messages[-1]["role"] == "tool":
            boundary = max((i for i, item in enumerate(transcript_messages) if item["role"] == "assistant"), default=-1)
            names = {call["id"]: call["function"]["name"]
                     for item in transcript_messages[max(0, boundary):] for call in item.get("tool_calls", [])}
            visual = []
            count = 0
            seen_images = set()
            for item in transcript_messages[boundary + 1:]:
                if item["role"] != "tool" or names.get(item.get("tool_call_id")) != "read_file":
                    continue
                result = _decode(item["content"])
                output = result.get("output")
                payload = output.get("image") if isinstance(output, dict) else None
                if result.get("status") != "succeeded" or not isinstance(payload, dict) or payload.get("kind") != "pulse.workspace-image.v1":
                    continue
                identity = (payload.get("path"), payload.get("sha256"))
                if not all(isinstance(value, str) for value in identity) or identity in seen_images:
                    continue
                seen_images.add(identity)
                count += 1
                parts = self.workspace_image_resolver(request.scope, payload) if count <= 5 else []
                visual.extend(parts or [{"type": "text", "text":
                    "No pixels accompany this image reference: its source changed, its live read expired, "
                    "or this round exceeded five images. Use read_file again for the required source."}])
            if visual:
                messages.append({"role": "user", "content": visual})
        # continuation_of/tool_results already exist in the durable transcript.
        # Appending them here would replay a tool exchange twice on resume.
        return body

    def stream(self, request: ModelRequest, *, cancel_event: threading.Event | None = None) -> _ChatStream:
        with self._lock:
            self.preflight_stream()
            stream = _ChatStream(self, self.request_body(request), cancel_event, request.scope)
            self._streams.add(stream)
            return stream

    def close(self) -> None:
        with self._lock:
            self._closed = True
            streams = tuple(self._streams)
        for stream in streams:
            stream.close()
        self._client.close()


class _ChatStream:
    """Owns the live HTTP response so kernel cancellation can close it."""
    def __init__(self, adapter: ChatHTTPModelAdapter, body: dict[str, Any],
                 cancel: threading.Event | None, scope) -> None:
        self.adapter, self.body, self.cancel, self.scope = adapter, body, cancel, scope
        self._closed = threading.Event()
        self._response: httpx.Response | None = None
        self._lock = threading.RLock()
        self._started = False
        self.usage: ProviderUsage | None = None
        self.response_id: str | None = None
        self._done = False

    def _cancelled(self) -> bool:
        return self._closed.is_set() or (self.cancel is not None and self.cancel.is_set())

    def _pause(self, seconds: float) -> bool:
        """Wait before a retry; False when the turn is stopped meanwhile."""
        deadline = time.monotonic() + seconds
        while not self._cancelled():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            self._closed.wait(min(remaining, 0.05))
        return False

    def __iter__(self) -> Iterator[ModelDelta]:
        if self._started:
            raise ProviderProtocolError("model response stream cannot be replayed")
        self._started = True
        response = None
        active_ticket = None
        accounting = self.adapter._attempt_accounting
        try:
            if self._cancelled():
                return
            headers = {"Accept": "text/event-stream"}
            if not self.adapter.config.no_auth_local:
                key = os.environ.get(self.adapter.config.credential_env)
                if not key:
                    raise ProviderUnavailable("configured model credential is missing", code="provider_credential_missing")
                if self.adapter.config.auth_style == "api-key":
                    headers["api-key"] = key
                else:
                    headers["Authorization"] = f"Bearer {key}"
            request = self.adapter._client.build_request("POST", self.adapter.config.endpoint,
                headers=headers, json=self.body)
            # Check the actual encoded HTTP body, including tool schemas and
            # temporary image parts, before *any* network send. Never truncate
            # the continuing history invisibly to fit this transport guard.
            if len(request.content) > self.adapter.config.max_request_bytes:
                raise ProviderUnavailable(
                    "model request exceeds configured byte bound; no provider call was sent",
                    code="provider_request_too_large",
                )
            # A native Stop or explicit stream cancellation can arrive while
            # serializing a long-lived Context Body. Check again immediately
            # before dispatch; the initial guard alone misses this interval.
            # This narrows dispatch risk, but is not an atomic provider bill cap.
            if self._cancelled():
                return
            retries = self.adapter.config.max_retries
            attempt = 0
            while True:
                attempt += 1
                # Each *physical* HTTP attempt, including transport and HTTP
                # retries, must reserve a distinct durable slot. The World
                # decides whether this scope belongs to an autonomous root.
                ticket = accounting.reserve(self.scope) if accounting is not None else None
                if self._cancelled():
                    if accounting is not None:
                        accounting.abandon(ticket)
                    return
                if accounting is not None:
                    try:
                        accounting.dispatch(ticket)  # durable before any send
                    except BaseException:
                        accounting.abandon(ticket)
                        raise
                    active_ticket = ticket
                try:
                    response = self.adapter._client.send(request, stream=True)
                except httpx.ReadTimeout:
                    # The provider may already have the request. Keep its slot.
                    raise
                except httpx.TransportError:
                    # A failed handshake and an accepted-but-lost request look
                    # alike at this boundary. Conservatively count it.
                    if active_ticket is not None:
                        accounting.settle(active_ticket, "uncertain")
                        active_ticket = None
                    if attempt > retries or self._cancelled():
                        raise
                    if not self._pause(retry_delay(attempt)):
                        return
                    continue
                with self._lock:
                    self._response = response
                    cancelled = self._cancelled()
                if cancelled:
                    return
                if response.status_code == 200:
                    break
                if active_ticket is not None:
                    accounting.settle(active_ticket, "rejected")
                    active_ticket = None
                if attempt > retries or not _retryable(response):
                    # Never persist a remote body, URL, request headers or credentials.
                    tries = f" after {attempt} attempts" if attempt > 1 else ""
                    raise ProviderUnavailable(f"model endpoint returned HTTP {response.status_code}{tries}",
                                              code=f"provider_http_{response.status_code}")
                delay = retry_delay(attempt, response.headers)
                response.close()
                response = None
                if not self._pause(delay):
                    return
            if "text/event-stream" not in response.headers.get("content-type", "").lower():
                raise ProviderProtocolError("model endpoint did not return an SSE stream")
            yield from self._deltas(response)
        except httpx.HTTPError:
            if not self._cancelled():
                raise ProviderUnavailable("model transport failed", code="provider_transport_error") from None
        finally:
            try:
                # Only a validated terminal SSE reply proves completion.
                # Cancellation, a broken stream and a crash cannot refund a
                # dispatched request, even without reported token usage.
                if active_ticket is not None:
                    accounting.settle(active_ticket, "completed" if self._done else "uncertain")
            finally:
                if response is not None:
                    response.close()
                self.close()

    def _events(self, response: httpx.Response) -> Iterator[str]:
        pending = b""
        parts: list[str] = []
        count = 0
        start = time.monotonic()
        for chunk in response.iter_bytes():
            if self._cancelled():
                return
            # Waiting for bytes is bounded by the client's idle timeout; this
            # bounds a reply that keeps streaming for too long.
            if time.monotonic() - start > self.adapter.config.max_stream_seconds:
                raise ProviderUnavailable("model reply exceeded the maximum stream time", code="provider_stream_too_long")
            count += len(chunk)
            if count > self.adapter.config.max_response_bytes:
                raise ProviderProtocolError("model response exceeded configured byte bound", code="provider_response_too_large")
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                try:
                    line = raw.rstrip(b"\r").decode("utf-8")
                except UnicodeError:
                    raise ProviderProtocolError("model stream contains invalid UTF-8") from None
                if not line:
                    if parts:
                        yield "\n".join(parts)
                        parts.clear()
                elif line.startswith("data:"):
                    parts.append(line[5:].removeprefix(" "))
        if pending or parts:
            raise ProviderProtocolError("model stream ended inside an SSE event", code="provider_stream_incomplete")

    def _deltas(self, response: httpx.Response) -> Iterator[ModelDelta]:
        calls: dict[int, dict[str, Any]] = {}
        reasoning_details: list[dict[str, Any]] = []
        has_reasoning_details = False
        finish: str | None = None
        for data in self._events(response):
            if self._cancelled():
                return
            if data == "[DONE]":
                if finish not in {"stop", "tool_calls"} or (bool(calls) != (finish == "tool_calls")):
                    raise ProviderProtocolError("model stream has no valid terminal", code="provider_stream_incomplete")
                ids: set[str] = set()
                for index in sorted(calls):
                    call = calls[index]
                    try:
                        arguments = _decode(call["arguments"])
                    except ValueError:
                        raise ProviderProtocolError("invalid tool argument JSON") from None
                    if not call["id"] or not call["name"] or call["id"] in ids or not isinstance(arguments, dict):
                        raise ProviderProtocolError("invalid or duplicate tool call")
                    ids.add(call["id"])
                    metadata = dict(call["provider_metadata"])
                    if has_reasoning_details and index == min(calls):
                        # Message-level continuation travels once with the
                        # first call in the same durable assistant response.
                        metadata["openrouter"] = {"reasoning_details": reasoning_details}
                    yield ModelDelta(ModelDeltaKind.TOOL_CALL,
                                     tool_call=ToolCall(call["id"], call["name"], arguments,
                                                        metadata))
                self._done = True
                yield ModelDelta(
                    ModelDeltaKind.DONE,
                    finish_reason=finish,
                    usage=self.usage,
                    provider_response_id=self.response_id,
                )
                return
            try:
                event = _decode(data)
            except ValueError:
                raise ProviderProtocolError("invalid model event JSON") from None
            if not isinstance(event, dict) or "error" in event:
                raise ProviderProtocolError("model stream reported a malformed or error event")
            choices = event.get("choices")
            if not isinstance(choices, list):
                raise ProviderProtocolError("model event has no choices array")
            # Azure sends filter-only frames with an empty response ID and,
            # for output annotations, no delta. They can follow finish=stop.
            annotation = (
                not choices and any(isinstance(event.get(key), list)
                    for key in ("prompt_annotations", "prompt_filter_results"))
            ) or (
                len(choices) == 1 and isinstance(choices[0], dict)
                and isinstance(choices[0].get("content_filter_results"), dict)
                and choices[0].get("delta") in (None, {})
            )
            if "id" in event and not (annotation and event["id"] in (None, "")):
                try:
                    response_id = validate_provider_response_id(event["id"])
                except (TypeError, ValueError):
                    raise ProviderProtocolError(
                        "model response identity is invalid",
                        code="provider_response_id_invalid",
                    ) from None
                if self.response_id is not None and self.response_id != response_id:
                    raise ProviderProtocolError(
                        "model response identity changed midstream",
                        code="provider_response_id_conflict",
                    )
                self.response_id = response_id
            # OpenAI and compatible services send usage:null on ordinary
            # chunks, followed by one actual report (possibly usage-only).
            if event.get("usage") is not None:
                raw_usage = event["usage"]
                if not isinstance(raw_usage, Mapping):
                    raise ProviderProtocolError(
                        "model usage must be an object",
                        code="provider_usage_invalid",
                    )
                if self.usage is not None:
                    raise ProviderProtocolError(
                        "model stream reported usage more than once",
                        code="provider_usage_duplicate",
                    )
                self.usage = _provider_usage(raw_usage)
            if not choices:
                continue
            if (len(choices) != 1 or not isinstance(choices[0], dict)
                    or type(choices[0].get("index")) is not int or choices[0]["index"] != 0):
                raise ProviderProtocolError("only one completion choice is supported")
            choice = choices[0]
            if annotation:
                filters = choice["content_filter_results"]
                if choice.get("finish_reason") == "content_filter" or any(
                    isinstance(item, Mapping) and item.get("filtered") is True for item in filters.values()
                ):
                    raise ProviderProtocolError("model service filtered the reply", code="provider_content_filtered")
                if choice.get("finish_reason") is None:
                    continue
            if finish is not None:
                raise ProviderProtocolError("model emitted content after its terminal")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise ProviderProtocolError("model event delta is invalid")
            if self.adapter.config.resolved_profile == "openrouter" and delta.get("reasoning_details") is not None:
                _append_reasoning_details(reasoning_details, delta["reasoning_details"])
                has_reasoning_details = True
            reasoning_field = "reasoning_content" if delta.get("reasoning_content") is not None else "reasoning"
            for field, kind in (("content", ModelDeltaKind.TEXT), (reasoning_field, ModelDeltaKind.REASONING)):
                value = delta.get(field)
                if value is not None and not isinstance(value, str):
                    raise ProviderProtocolError("model content must be text")
                if value:
                    yield ModelDelta(kind, text=value)
            tool_deltas = delta.get("tool_calls")
            if tool_deltas is None:
                tool_deltas = []
            if not isinstance(tool_deltas, list):
                raise ProviderProtocolError("model tool deltas must be an array")
            for fragment in tool_deltas:
                if not isinstance(fragment, dict) or type(fragment.get("index")) is not int or fragment["index"] < 0:
                    raise ProviderProtocolError("model tool index is invalid")
                if fragment.get("type") is not None and fragment["type"] != "function":
                    raise ProviderProtocolError("unsupported tool-call type")
                call = calls.setdefault(fragment["index"], {
                    "id": "", "name": "", "arguments": "", "provider_metadata": {},
                })
                metadata = _google_tool_metadata(fragment.get("extra_content"))
                if metadata:
                    if call["provider_metadata"] and call["provider_metadata"] != metadata:
                        raise ProviderProtocolError("model tool continuation metadata changed midstream")
                    call["provider_metadata"] = metadata
                function = fragment.get("function")
                if function is None:
                    function = {}
                if not isinstance(function, dict):
                    raise ProviderProtocolError("model function delta is invalid")
                for key, value in (("id", fragment.get("id")), ("name", function.get("name")), ("arguments", function.get("arguments"))):
                    if value is not None:
                        if not isinstance(value, str):
                            raise ProviderProtocolError("model tool fragment must be text")
                        call[key] += value
            reason = choice.get("finish_reason")
            if reason is not None:
                if not isinstance(reason, str):
                    raise ProviderProtocolError("model finish reason must be text")
                if reason == "length" and not calls:
                    # Keep a reply cut at the output limit, and say so, rather
                    # than discard it. A cut tool call has incomplete arguments.
                    yield ModelDelta(ModelDeltaKind.TEXT, text=OUTPUT_LIMIT_NOTE)
                    reason = "stop"
                elif reason == "length":
                    raise ProviderProtocolError("model reached its output limit inside a tool call",
                                                code="provider_output_limit")
                elif reason == "content_filter":
                    raise ProviderProtocolError("model service filtered the reply", code="provider_content_filtered")
                elif reason not in {"stop", "tool_calls"}:
                    raise ProviderProtocolError("model generation did not complete", code="provider_generation_incomplete")
                finish = reason
        if not self._cancelled():
            raise ProviderProtocolError("model stream ended without DONE", code="provider_stream_incomplete")

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            response = self._response
        if response is not None:
            response.close()
        with self.adapter._lock:
            self.adapter._streams.discard(self)


def model_factory_from_env():
    """Optional Kernel composition seam; absence leaves the adapter unconfigured.

    Setting P_PULSE_CHAT_ENDPOINT and P_PULSE_CHAT_MODEL explicitly opts in.
    Each durable session receives its own closable adapter, not its own model
    identity. No credential value is copied into the factory configuration.
    """
    endpoint = os.environ.get("P_PULSE_CHAT_ENDPOINT")
    model = os.environ.get("P_PULSE_CHAT_MODEL")
    if endpoint is None and model is None:
        return None
    if not endpoint or not model:
        raise ValueError("P_PULSE_CHAT_ENDPOINT and P_PULSE_CHAT_MODEL must be configured together")
    config = ChatHTTPConfig(
        endpoint, model,
        credential_env=os.environ.get("P_PULSE_CREDENTIAL_ENV", "P_PULSE_API_KEY"),
        auth_style=os.environ.get("P_PULSE_CHAT_AUTH_STYLE", "bearer"),
        timeout_seconds=float(os.environ.get("P_PULSE_CHAT_TIMEOUT", "120")),
        max_stream_seconds=float(os.environ.get("P_PULSE_CHAT_MAX_SECONDS", "1800")),
        max_retries=int(os.environ.get("P_PULSE_CHAT_RETRIES", "2")),
        max_output_tokens=int(os.environ.get("P_PULSE_MAX_OUTPUT_TOKENS", "4096")),
        max_request_bytes=int(os.environ.get("P_PULSE_MAX_REQUEST_BYTES", "8388608")),
        reasoning_field=os.environ.get("P_PULSE_REASONING_FIELD") or None,
        allow_local_http=os.environ.get("P_PULSE_ALLOW_LOCAL_HTTP") == "1",
        provider_namespace=os.environ.get("P_PULSE_PROVIDER_NAMESPACE") or None,
        no_auth_local=os.environ.get("P_PULSE_LOCAL_NO_AUTH") == "1",
        provider_profile=os.environ.get("P_PULSE_CHAT_PROFILE", "auto"),
    )
    def factory(_scope):
        return ChatHTTPModelAdapter(config)
    return factory
