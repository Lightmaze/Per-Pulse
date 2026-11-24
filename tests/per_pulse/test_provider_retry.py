"""A busy or briefly failing model service does not end the turn.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted, including the failures; the adapter and,
in the last test, the Kernel, World and page service are real.
"""
from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
import json
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter, retry_delay
from pulse_harness.core.errors import ProviderProtocolError, ProviderUnavailable
from pulse_harness.core.provider import ModelDeltaKind, ModelRequest
from pulse_harness.core.scope import HarnessScope

REPLY = [{"choices": [{"index": 0, "delta": {"content": "Synthetic fixture reply."}, "finish_reason": None}]},
         {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
NOW = {"retry-after": "0"}


def _ok(events=REPLY, done=True):
    body = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + ("data: [DONE]\n\n" if done else "")
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def _adapter(monkeypatch, responses, requests, **config):
    """Answer each request with the next of ``responses`` (a response or an exception)."""
    monkeypatch.setenv("PER_PULSE_RETRY_KEY", "synthetic-test-credential")
    pending = list(responses)

    def handler(request):
        requests.append(json.loads(request.content))
        step = pending.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return ChatHTTPModelAdapter(
        ChatHTTPConfig("https://fixture.invalid/chat/completions", "retry-fixture",
                       credential_env="PER_PULSE_RETRY_KEY", **config),
        transport=httpx.MockTransport(handler))


def _request():
    scope = HarnessScope("world", "engram", "session", 0, "owner", turn_id="turn")
    return ModelRequest(scope, ({"role": "user", "content": "Hello."},), (), 0)


def _text(adapter, cancel=None):
    return "".join(d.text for d in adapter.stream(_request(), cancel_event=cancel) if d.kind is ModelDeltaKind.TEXT)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 529, 408, 409])
def test_a_busy_or_failing_service_is_asked_again(monkeypatch, status):
    requests = []
    adapter = _adapter(monkeypatch, [httpx.Response(status, headers=NOW), _ok()], requests)
    assert _text(adapter) == "Synthetic fixture reply."
    assert len(requests) == 2 and requests[0] == requests[1]


def test_a_connection_that_fails_before_any_reply_is_tried_again(monkeypatch):
    requests = []
    adapter = _adapter(monkeypatch, [httpx.ConnectError("refused"),
                                     httpx.RemoteProtocolError("Server disconnected without sending a response."),
                                     _ok()], requests)
    assert _text(adapter) == "Synthetic fixture reply."
    assert len(requests) == 3


def test_after_the_retries_the_turn_fails_with_the_service_status(monkeypatch):
    requests = []
    adapter = _adapter(monkeypatch, [httpx.Response(429, headers=NOW)] * 3, requests)
    with pytest.raises(ProviderUnavailable) as failed:
        _text(adapter)
    assert failed.value.code == "provider_http_429" and "after 3 attempts" in str(failed.value)
    assert len(requests) == 3


@pytest.mark.parametrize("response", [
    httpx.Response(400), httpx.Response(401), httpx.Response(404),
    # The service says not to: the same request would fail the same way.
    httpx.Response(503, headers={"x-should-retry": "false"}),
])
def test_a_request_the_service_rejected_is_not_sent_again(monkeypatch, response):
    requests = []
    adapter = _adapter(monkeypatch, [response, _ok()], requests)
    with pytest.raises(ProviderUnavailable) as failed:
        _text(adapter)
    assert failed.value.code == f"provider_http_{response.status_code}" and len(requests) == 1


def test_the_service_can_ask_for_a_retry_of_any_status(monkeypatch):
    requests = []
    adapter = _adapter(monkeypatch, [httpx.Response(400, headers={"x-should-retry": "true", **NOW}), _ok()], requests)
    assert _text(adapter) == "Synthetic fixture reply." and len(requests) == 2


def test_a_reply_that_started_is_never_sent_again(monkeypatch):
    requests = []
    adapter = _adapter(monkeypatch, [_ok(REPLY[:1], done=False), _ok()], requests)
    with pytest.raises(ProviderProtocolError) as failed:
        _text(adapter)
    assert failed.value.code == "provider_stream_incomplete" and len(requests) == 1


def test_a_service_that_went_quiet_is_not_waited_on_again(monkeypatch):
    requests = []
    adapter = _adapter(monkeypatch, [httpx.ReadTimeout("no reply"), _ok()], requests)
    with pytest.raises(ProviderUnavailable) as failed:
        _text(adapter)
    assert failed.value.code == "provider_transport_error" and len(requests) == 1


def test_retries_can_be_turned_off(monkeypatch):
    requests = []
    adapter = _adapter(monkeypatch, [httpx.Response(503, headers=NOW), _ok()], requests, max_retries=0)
    with pytest.raises(ProviderUnavailable):
        _text(adapter)
    assert len(requests) == 1
    with pytest.raises(ValueError):
        ChatHTTPConfig("https://fixture.invalid/chat/completions", "m", max_retries=-1)


def test_stop_during_the_wait_ends_the_turn_without_another_request(monkeypatch):
    requests = []
    adapter = _adapter(monkeypatch, [httpx.Response(429, headers={"retry-after": "30"}), _ok()], requests)
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    assert _text(adapter, cancel) == ""
    assert time.monotonic() - started < 3 and len(requests) == 1


def test_the_wait_follows_the_service_and_otherwise_backs_off():
    assert retry_delay(1, httpx.Headers({"retry-after": "2"})) == 2
    assert retry_delay(1, httpx.Headers({"retry-after-ms": "1500"})) == 1.5
    later = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
    assert 15 < retry_delay(1, httpx.Headers({"retry-after": later})) <= 20
    # Waits beyond a minute, or unreadable ones, fall back to backing off.
    for headers in ({"retry-after": "3600"}, {"retry-after": "soon"}, {}):
        assert 0.375 <= retry_delay(1, httpx.Headers(headers)) <= 0.5
    assert 0.75 <= retry_delay(2) <= 1.0
    assert 6 <= retry_delay(10) <= 8


def test_a_page_gets_its_reply_through_a_brief_service_error(tmp_path, monkeypatch):
    requests = []
    responses = [httpx.Response(503, headers=NOW), _ok()]
    monkeypatch.setenv("PER_PULSE_RETRY_KEY", "synthetic-test-credential")

    def handler(request):
        requests.append(request)
        return responses.pop(0) if responses else _ok()

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "retry-fixture",
                           credential_env="PER_PULSE_RETRY_KEY"),
            transport=httpx.MockTransport(handler))

    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        page_id = client.post("/api/pages", json={"title": "Plan", "instruction": "Plan the week."}).json()["page"]["id"]
        deadline = time.monotonic() + 15
        while (view := client.get(f"/api/pages/{page_id}").json())["page"]["status"] in ("queued", "working"):
            assert time.monotonic() < deadline, "the turn did not settle"
            time.sleep(0.05)
    assert view["page"]["status"] == "idle"
    assert [m["content"] for m in view["messages"] if m["role"] == "assistant"] == ["Synthetic fixture reply."]
    assert len(requests) == 2
