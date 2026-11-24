"""Provider refusal text reaches the same live and durable reply path.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.errors import ProviderProtocolError
from pulse_harness.core.provider import ModelDeltaKind
from test_provider_compatibility import _chunk, _request, _sse, USAGE


REFUSAL = "Synthetic refusal fixture. I cannot complete this request."


def _adapter(monkeypatch, events):
    monkeypatch.setenv("PER_PULSE_REFUSAL_KEY", "synthetic-credential")
    return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "synthetic-refusal",
        credential_env="PER_PULSE_REFUSAL_KEY", max_retries=0),
        transport=httpx.MockTransport(lambda _: _sse(events)))


def test_streamed_refusal_is_returned_whole_with_its_reported_usage(monkeypatch):
    events = [_chunk({"role": "assistant", "content": None, "refusal": None}),
              *(_chunk({"refusal": char}) for char in REFUSAL),
              _chunk({}, "stop"), {"choices": [], "usage": USAGE}]
    adapter = _adapter(monkeypatch, events)
    try:
        deltas = list(adapter.stream(_request()))
        assert "".join(d.text for d in deltas if d.kind is ModelDeltaKind.TEXT) == REFUSAL
        assert not any(d.kind is ModelDeltaKind.TOOL_CALL for d in deltas)
        assert deltas[-1].kind is ModelDeltaKind.DONE
        assert deltas[-1].usage.input_tokens == 120
    finally:
        adapter.close()


@pytest.mark.parametrize("value", [False, 0, [], {}])
def test_non_text_refusal_still_fails_as_a_protocol_error(monkeypatch, value):
    adapter = _adapter(monkeypatch, [_chunk({"refusal": value}), _chunk({}, "stop")])
    try:
        with pytest.raises(ProviderProtocolError):
            list(adapter.stream(_request()))
    finally:
        adapter.close()


def test_refusal_survives_native_restart_and_the_next_user_message(tmp_path, monkeypatch):
    from per_pulse.execution import PageExecution
    from test_execution import _client, _new_page, _send, _wait

    monkeypatch.setenv("PER_PULSE_REFUSAL_KEY", "synthetic-credential")
    requests = []

    def factory(_scope):
        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            latest_user = next(m for m in reversed(body["messages"]) if m["role"] == "user")
            if latest_user["content"] == "FOLLOWUP":
                assert any(m["role"] == "assistant" and m["content"] == REFUSAL for m in body["messages"])
                delta = {"content": "Ready for another task."}
            else:
                delta = {"content": None, "refusal": REFUSAL}
            return _sse([_chunk(delta), _chunk({}, "stop"), {"choices": [], "usage": USAGE}])
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "synthetic-refusal",
            credential_env="PER_PULSE_REFUSAL_KEY", max_retries=0), transport=httpx.MockTransport(respond))

    def replies(client, page):
        return [m["content"] for m in client.get(f"/api/pages/{page}").json()["messages"] if m["role"] == "assistant"]

    with _client(tmp_path, model_factory=factory, posture=False) as client:
        page = _new_page(client, instruction="REFUSAL_FIXTURE")
        execution = PageExecution(client.app.state.pages.runtime)
        state = _wait(execution, page, lambda view: view["status"] == "idle" and replies(client, page) == [REFUSAL])
        assert state["pendingActions"] == []
        assert state["usage"]["inputTokens"] == 120
        assert len(requests) == 1
    with _client(tmp_path, model_factory=factory, posture=False) as client:
        assert replies(client, page) == [REFUSAL]
        _send(client, page, "FOLLOWUP")
        _wait(PageExecution(client.app.state.pages.runtime), page,
              lambda view: view["status"] == "idle" and replies(client, page) == [REFUSAL, "Ready for another task."])
        assert len(requests) == 2
