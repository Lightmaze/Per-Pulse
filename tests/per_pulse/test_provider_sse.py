"""SSE framing regressions with an explicitly synthetic byte-stream transport.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import threading

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core import HarnessScope
from pulse_harness.core.errors import ProviderProtocolError
from pulse_harness.core.provider import ModelDeltaKind, ModelRequest


SCOPE = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")
USAGE = {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}
TEXT = "A split UTF-8 reply: \u4e16\u754c \U0001f30d."


class FragmentedBody(httpx.SyncByteStream):
    def __init__(self, body, width):
        self.body, self.width = body, width
        self.closed = False

    def __iter__(self):
        for offset in range(0, len(self.body), self.width):
            yield self.body[offset:offset + self.width]

    def close(self):
        self.closed = True


def _event(delta, finish=None):
    return {"id": "synthetic-sse-completion", "choices": [
        {"index": 0, "delta": delta, "finish_reason": finish}], "usage": None}


def _frame(value, newline):
    # Multiple data fields join with LF regardless of the transport newline.
    lines = json.dumps(value, ensure_ascii=False, indent=2).encode().split(b"\n")
    return newline.join(b"data: " + line for line in lines) + newline * 2


def _body(newline=b"\n", bom=False):
    return ((b"\xef\xbb\xbf" if bom else b"")
            + _frame(_event({"content": TEXT}), newline)
            + b": heartbeat" + newline * 2
            + _frame(_event({}, "stop"), newline)
            + _frame({"choices": [], "usage": USAGE}, newline)
            + b"data: [DONE]" + newline * 2)


def _adapter(monkeypatch, body, width, **config):
    monkeypatch.setenv("PER_PULSE_SSE_KEY", "synthetic-credential")
    stream = FragmentedBody(body, width)
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "synthetic-sse",
        credential_env="PER_PULSE_SSE_KEY", max_retries=0, **config),
        transport=httpx.MockTransport(lambda _: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream)))
    request = ModelRequest(scope=SCOPE, messages=({"role": "user", "content": "Reply."},), tools=(), cursor=0)
    return adapter, stream, request


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize("width", [1, 7, 65536])
def test_standard_sse_framing_preserves_reply_and_usage(monkeypatch, newline, bom, width):
    adapter, stream, request = _adapter(monkeypatch, _body(newline, bom), width)
    try:
        deltas = list(adapter.stream(request))
        assert "".join(d.text for d in deltas if d.kind is ModelDeltaKind.TEXT) == TEXT
        assert sum(d.kind is ModelDeltaKind.DONE for d in deltas) == 1
        assert deltas[-1].provider_response_id == "synthetic-sse-completion"
        assert (deltas[-1].usage.input_tokens, deltas[-1].usage.output_tokens) == (120, 8)
        assert stream.closed
    finally:
        adapter.close()


def test_mixed_line_endings_do_not_merge_or_duplicate_events(monkeypatch):
    body = (_frame(_event({"content": "First."}), b"\r")
            + _frame(_event({"content": " Second."}), b"\r\n")
            + _frame(_event({}, "stop"), b"\n") + b"data: [DONE]\r\r")
    adapter, stream, request = _adapter(monkeypatch, body, 1)
    try:
        deltas = list(adapter.stream(request))
        assert "".join(d.text for d in deltas if d.kind is ModelDeltaKind.TEXT) == "First. Second."
        assert deltas[-1].kind is ModelDeltaKind.DONE
        assert deltas[-1].usage is None
        assert stream.closed
    finally:
        adapter.close()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("tail", [b"data: [DONE]", b"data: [DONE]{newline}", b"data: {"])
def test_unterminated_sse_event_never_completes_a_reply(monkeypatch, newline, tail):
    tail = tail.replace(b"{newline}", newline)
    body = _frame(_event({"content": "Partial."}, "stop"), newline) + tail
    adapter, stream, request = _adapter(monkeypatch, body, 1)
    try:
        with pytest.raises(ProviderProtocolError) as failure:
            list(adapter.stream(request))
        assert failure.value.code == "provider_stream_incomplete"
        assert stream.closed
    finally:
        adapter.close()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_invalid_utf8_is_still_a_protocol_error(monkeypatch, newline):
    adapter, stream, request = _adapter(monkeypatch, b"data: \xff" + newline * 2, 1)
    try:
        with pytest.raises(ProviderProtocolError, match="UTF-8"):
            list(adapter.stream(request))
        assert stream.closed
    finally:
        adapter.close()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("difference", [0, -1])
def test_response_byte_limit_counts_framing_and_bom(monkeypatch, newline, difference):
    body = _body(newline, bom=True)
    adapter, stream, request = _adapter(monkeypatch, body, len(body), max_response_bytes=len(body) + difference)
    try:
        if difference == 0:
            assert list(adapter.stream(request))[-1].kind is ModelDeltaKind.DONE
        else:
            with pytest.raises(ProviderProtocolError) as failure:
                list(adapter.stream(request))
            assert failure.value.code == "provider_response_too_large"
        assert stream.closed
    finally:
        adapter.close()


def test_cancel_before_dispatch_sends_no_bytes(monkeypatch):
    adapter, stream, request = _adapter(monkeypatch, _body(b"\r", bom=True), 1)
    cancel = threading.Event()
    cancel.set()
    try:
        assert list(adapter.stream(request, cancel_event=cancel)) == []
    finally:
        adapter.close()
