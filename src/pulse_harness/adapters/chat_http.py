"""HTTP Chat Completions transport for the existing Pulse-owned TurnRunner.

This adapter owns only wire translation and a closable response stream. The
kernel still owns transcripts, tool execution, approval and settlement. Its
capability evidence describes this implementation, not a tested remote model.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from pulse_harness.life.tools import LIFE_SCHEMAS
from pulse_harness.adapters.command import COMMAND_SCHEMA
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
        if "cached_tokens" in details:
            cached_candidates.append(
                _usage_count(details["cached_tokens"], "prompt_tokens_details.cached_tokens")
            )
    if "prompt_cache_hit_tokens" in value:
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
        if "cache_creation_input_tokens" in value
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


@dataclass(frozen=True)
class ChatHTTPConfig:
    # Full operator-selected URL, not an untrusted model-generated destination.
    endpoint: str
    model: str
    credential_env: str = "P_PULSE_API_KEY"
    timeout_seconds: float = 60.0
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

    def __post_init__(self) -> None:
        url = urlsplit(self.endpoint)
        local_http = (self.allow_local_http and url.scheme == "http"
                      and url.hostname in {"127.0.0.1", "::1", "localhost"})
        if (url.scheme != "https" and not local_http) or not url.hostname:
            raise ValueError("model endpoint requires HTTPS, or explicit loopback HTTP")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("model endpoint must not contain credentials, query or fragment")
        if not self.model.strip() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.credential_env):
            raise ValueError("model and credential environment variable must be valid")
        if type(self.no_auth_local) is not bool:
            raise ValueError("no_auth_local must be a boolean")
        if self.no_auth_local and (not local_http or self.provider_namespace is not None):
            raise ValueError("credential-free model calls require explicitly enabled loopback HTTP only")
        if isinstance(self.timeout_seconds, bool) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        for value in (self.max_output_tokens, self.max_response_bytes, self.max_request_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("token and response bounds must be positive integers")
        if self.reasoning_field not in {None, "reasoning_content"}:
            raise ValueError("unsupported reasoning wire field")
        if self.provider_namespace not in {None, "openrouter"}:
            raise ValueError("unsupported provider namespace")
        if self.provider_namespace == "openrouter":
            from pulse_harness.adapters.openrouter_usage import (
                OPENROUTER_CHAT_ENDPOINT,
            )

            if self.endpoint.rstrip("/") != OPENROUTER_CHAT_ENDPOINT:
                raise ValueError(
                    "OpenRouter namespace requires the canonical OpenRouter chat endpoint"
                )


WORKSPACE_SCHEMAS: dict[str, dict[str, Any]] = {
    **HISTORY_SCHEMAS,
    **LIFE_SCHEMAS,
    "run_command": COMMAND_SCHEMA,
    "read_file": {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"], "additionalProperties": False,
    },
    "mutate_file": {
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
        "oneOf": [{"required": ["content"]}, {"required": ["patch"]}],
        "additionalProperties": False,
    },
}


class ChatHTTPModelAdapter:
    """Streaming network adapter; no retry, no fallback, no tool execution.

    Supply schemas for every tool in the selected catalog. Credentials are
    resolved only when a request starts and never enter durable request data.
    The optional transport exists for HTTP contract tests, not a demo mode.
    """
    def __init__(self, config: ChatHTTPConfig, *,
                 tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.life_image_resolver = None  # native runtime may bind a scoped, ephemeral observation renderer
        self.config = config
        self._schemas = _decode(_json(dict(WORKSPACE_SCHEMAS if tool_schemas is None else tool_schemas)))
        self._client = httpx.Client(timeout=config.timeout_seconds, follow_redirects=False,
                                    trust_env=False, transport=transport)
        self._test_transport = transport is not None
        self._streams: set[_ChatStream] = set()
        self._lock = threading.RLock()
        self._closed = False

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
                try:
                    payload = _decode(content)
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and set(payload) == {"text", "tool_calls"} and payload["tool_calls"]:
                    if not isinstance(payload["text"], str) or not isinstance(payload["tool_calls"], list):
                        raise ProviderProtocolError("invalid assistant tool transcript")
                    message["content"] = payload["text"] or None
                    message["tool_calls"] = []
                    for call in payload["tool_calls"]:
                        if (not isinstance(call, dict) or not isinstance(call.get("tool_call_id"), str)
                                or not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict)):
                            raise ProviderProtocolError("invalid assistant tool transcript")
                        message["tool_calls"].append({"id": call["tool_call_id"], "type": "function",
                            "function": {"name": call["name"], "arguments": _json(call["arguments"])}})
                if self.config.reasoning_field and reasoning:
                    message[self.config.reasoning_field] = reasoning
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
        body: dict[str, Any] = {"model": self.config.model, "messages": messages,
            "stream": True, "max_tokens": self.config.max_output_tokens,
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
            if messages and messages[-1]['role'] == 'tool':
                boundary = max((i for i,m in enumerate(messages) if m['role']=='assistant'), default=-1)
                for item in messages[max(0,boundary):]:
                    for call in item.get('tool_calls', []): names[call['id']] = call['function']['name']
                visual = []
                for item in messages[boundary+1:]:
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
        # continuation_of/tool_results already exist in the durable transcript.
        # Appending them here would replay a tool exchange twice on resume.
        return body

    def stream(self, request: ModelRequest, *, cancel_event: threading.Event | None = None) -> _ChatStream:
        with self._lock:
            self.preflight_stream()
            stream = _ChatStream(self, self.request_body(request), cancel_event)
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
    def __init__(self, adapter: ChatHTTPModelAdapter, body: dict[str, Any], cancel: threading.Event | None) -> None:
        self.adapter, self.body, self.cancel = adapter, body, cancel
        self._closed = threading.Event()
        self._response: httpx.Response | None = None
        self._lock = threading.RLock()
        self._started = False
        self.usage: ProviderUsage | None = None
        self.response_id: str | None = None

    def _cancelled(self) -> bool:
        return self._closed.is_set() or (self.cancel is not None and self.cancel.is_set())

    def __iter__(self) -> Iterator[ModelDelta]:
        if self._started:
            raise ProviderProtocolError("model response stream cannot be replayed")
        self._started = True
        response = None
        try:
            if self._cancelled():
                return
            headers = {"Accept": "text/event-stream"}
            if not self.adapter.config.no_auth_local:
                key = os.environ.get(self.adapter.config.credential_env)
                if not key:
                    raise ProviderUnavailable("configured model credential is missing", code="provider_credential_missing")
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
            response = self.adapter._client.send(request, stream=True)
            with self._lock:
                self._response = response
                cancelled = self._cancelled()
            if cancelled:
                return
            if response.status_code != 200:
                # Never persist a remote body, URL, request headers or credentials.
                raise ProviderUnavailable(f"model endpoint returned HTTP {response.status_code}",
                                          code=f"provider_http_{response.status_code}")
            if "text/event-stream" not in response.headers.get("content-type", "").lower():
                raise ProviderProtocolError("model endpoint did not return an SSE stream")
            yield from self._deltas(response)
        except httpx.HTTPError:
            if not self._cancelled():
                raise ProviderUnavailable("model transport failed; request was not retried", code="provider_transport_error") from None
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
            if time.monotonic() - start > self.adapter.config.timeout_seconds:
                raise ProviderUnavailable("model response deadline exceeded", code="provider_timeout")
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
        calls: dict[int, dict[str, str]] = {}
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
                    yield ModelDelta(ModelDeltaKind.TOOL_CALL,
                                     tool_call=ToolCall(call["id"], call["name"], arguments))
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
            if "id" in event:
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
            if "usage" in event:
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
            choices = event.get("choices")
            if not isinstance(choices, list):
                raise ProviderProtocolError("model event has no choices array")
            if not choices:
                continue
            if len(choices) != 1 or not isinstance(choices[0], dict) or choices[0].get("index") != 0:
                raise ProviderProtocolError("only one completion choice is supported")
            choice = choices[0]
            if finish is not None:
                raise ProviderProtocolError("model emitted content after its terminal")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise ProviderProtocolError("model event delta is invalid")
            for field, kind in (("content", ModelDeltaKind.TEXT), ("reasoning_content", ModelDeltaKind.REASONING)):
                value = delta.get(field)
                if value is not None and not isinstance(value, str):
                    raise ProviderProtocolError("model content must be text")
                if value:
                    yield ModelDelta(kind, text=value)
            tool_deltas = delta.get("tool_calls") or []
            if not isinstance(tool_deltas, list):
                raise ProviderProtocolError("model tool deltas must be an array")
            for fragment in tool_deltas:
                if not isinstance(fragment, dict) or type(fragment.get("index")) is not int or fragment["index"] < 0:
                    raise ProviderProtocolError("model tool index is invalid")
                if fragment.get("type", "function") != "function":
                    raise ProviderProtocolError("unsupported tool-call type")
                call = calls.setdefault(fragment["index"], {"id": "", "name": "", "arguments": ""})
                function = fragment.get("function") or {}
                if not isinstance(function, dict):
                    raise ProviderProtocolError("model function delta is invalid")
                for key, value in (("id", fragment.get("id")), ("name", function.get("name")), ("arguments", function.get("arguments"))):
                    if value is not None:
                        if not isinstance(value, str):
                            raise ProviderProtocolError("model tool fragment must be text")
                        call[key] += value
            reason = choice.get("finish_reason")
            if reason is not None:
                if reason not in {"stop", "tool_calls"}:
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
        timeout_seconds=float(os.environ.get("P_PULSE_CHAT_TIMEOUT", "60")),
        max_output_tokens=int(os.environ.get("P_PULSE_MAX_OUTPUT_TOKENS", "4096")),
        max_request_bytes=int(os.environ.get("P_PULSE_MAX_REQUEST_BYTES", "8388608")),
        reasoning_field=os.environ.get("P_PULSE_REASONING_FIELD") or None,
        allow_local_http=os.environ.get("P_PULSE_ALLOW_LOCAL_HTTP") == "1",
        provider_namespace=os.environ.get("P_PULSE_PROVIDER_NAMESPACE") or None,
        no_auth_local=os.environ.get("P_PULSE_LOCAL_NO_AUTH") == "1",
    )
    def factory(_scope):
        return ChatHTTPModelAdapter(config)
    return factory
