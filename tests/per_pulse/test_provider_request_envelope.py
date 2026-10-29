"""Wire-size admission for every native Chat Completions model request.

The HTTP transport here is an explicit synthetic fixture. These checks establish
that oversized context does not contact any provider, not token/dollar caps.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import httpx
import pytest

from pulse_harness.adapters.chat_http import (
    ChatHTTPConfig,
    ChatHTTPModelAdapter,
    model_factory_from_env,
)
from pulse_harness.core import HarnessScope
from pulse_harness.core.errors import ProviderUnavailable
from pulse_harness.core.provider import ModelDeltaKind, ModelRequest


ENDPOINT = "https://fixture.invalid/v1/chat/completions"
KEY = "PER_PULSE_REQUEST_CAP_FIXTURE_KEY"


def _request(message: str) -> ModelRequest:
    return ModelRequest(
        scope=HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn"),
        messages=({"role": "system", "content": "Continue the same Work."},
                  {"role": "user", "content": message}),
        tools=(), cursor=0,
    )


def _adapter(monkeypatch, limit: int, sent: list[bytes]) -> ChatHTTPModelAdapter:
    monkeypatch.setenv(KEY, "fixture-only")
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        events = [
            {"choices": [{"index": 0, "delta": {"content": "Done."}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        sse = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)
    return ChatHTTPModelAdapter(
        ChatHTTPConfig(ENDPOINT, "synthetic-request-bound", credential_env=KEY,
                       max_request_bytes=limit),
        transport=httpx.MockTransport(handler),
    )


def test_exact_encoded_request_boundary_accepts_equal_and_refuses_one_byte_over(monkeypatch):
    """Count encoded JSON bytes, not codepoints or a rough prompt estimate."""
    actual = _request("这是同一个持续工作。" * 15)
    sent = []
    probe = _adapter(monkeypatch, 8192, sent)
    try:
        # This is the actual encoder used by the HTTP adapter, not a guessed
        # UTF-8 character count or the length of the unencoded transcript.
        wire = probe._client.build_request("POST", ENDPOINT,
                                           json=probe.request_body(actual)).content
        bound = len(wire)
        assert bound > len(actual.messages[-1]["content"])
    finally:
        probe.close()

    allowed = _adapter(monkeypatch, bound, sent)
    try:
        result = list(allowed.stream(actual))
        assert result[-1].kind is ModelDeltaKind.DONE
        assert sent == [wire]
    finally:
        allowed.close()

    blocked = _adapter(monkeypatch, bound - 1, sent)
    try:
        with pytest.raises(ProviderUnavailable) as exc:
            list(blocked.stream(actual))
        assert exc.value.code == "provider_request_too_large"
        assert "no provider call was sent" in str(exc.value)
        assert sent == [wire]
    finally:
        blocked.close()


def test_one_oversized_request_does_not_truncate_original_history(monkeypatch):
    sent = []
    adapter = _adapter(monkeypatch, 400, sent)
    long_work = _request("Keep the original Work history: " + "交付证据" * 150)
    try:
        assert long_work.messages[-1]["content"].endswith("交付证据")
        with pytest.raises(ProviderUnavailable) as exc:
            list(adapter.stream(long_work))
        assert exc.value.code == "provider_request_too_large"
        assert sent == []
        # Only the outgoing request is refused; no transcript record was
        # rewritten or compacted by this transport-layer check.
        assert long_work.messages[-1]["content"].count("交付证据") == 150
    finally:
        adapter.close()


@pytest.mark.parametrize("limit", [0, -1, True, 1.25])
def test_invalid_request_byte_bounds_fail_before_provider_setup(limit):
    with pytest.raises(ValueError):
        ChatHTTPConfig(ENDPOINT, "fixture", max_request_bytes=limit)


def test_environment_bound_is_explicit_and_shared_by_native_adapter_instances(monkeypatch):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "fixture")
    monkeypatch.setenv("P_PULSE_MAX_REQUEST_BYTES", "123456")
    factory = model_factory_from_env()
    assert factory is not None
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")
    first = factory(scope)
    second = factory(scope)
    try:
        assert first.config.max_request_bytes == second.config.max_request_bytes == 123456
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("cancel_mode", ["request_cancel", "native_stream_stop"])
def test_cancel_while_encoding_never_sends(monkeypatch, cancel_mode):
    """A Stop or explicit close can arrive after the initial dispatch gate.

    The deterministic boundary is between build_request (which serializes the
    complete continuing history) and HTTP dispatch. Sending here could consume
    real paid tokens even though the stream has already been cancelled.
    """
    import threading

    sent = []
    adapter = _adapter(monkeypatch, 8192, sent)
    cancel = threading.Event()
    stream = adapter.stream(_request("A continuing Work awaiting its turn."), cancel_event=cancel)
    original_build = adapter._client.build_request

    def cancel_during_encoding(*args, **kwargs):
        wire = original_build(*args, **kwargs)
        if cancel_mode == "request_cancel":
            cancel.set()
        else:
            stream.close()
        return wire

    monkeypatch.setattr(adapter._client, "build_request", cancel_during_encoding)
    try:
        assert list(stream) == []
        assert sent == [], "Cancellation before dispatch must not contact the provider"
    finally:
        adapter.close()
