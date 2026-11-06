"""Weekly World admission at the actual HTTP send boundary.

Only the HTTP responses are synthetic. These are contract checks for wire
attempt accounting, not evidence of a live provider or autonomous learning.
"""
from __future__ import annotations

import json
from threading import RLock

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.errors import ProviderProtocolError, ProviderUnavailable
from pulse_harness.core.provider import ModelDeltaKind, ModelRequest
from pulse_harness.core.scope import HarnessScope
from pulse_system.service.weekly_requests import WeeklyRequestBudget, WeeklyRequestGate


class _Storage:
    def __init__(self):
        self._lock = RLock()
        self.values = {}

    def load_component_state(self, key):
        import copy
        return copy.deepcopy(self.values.get(key))

    def save_component_state(self, key, state):
        import copy
        self.values[key] = copy.deepcopy(state)


def _response(*, valid=True):
    events = [
        {"choices": [{"index": 0, "delta": {"content": "Fixture."}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    content = "".join("data: " + json.dumps(item) + "\n\n" for item in events)
    if valid:
        content += "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)


def _request():
    return ModelRequest(
        HarnessScope("world", "engram", "session", 0, "owner", turn_id="turn"),
        ({"role": "user", "content": "Hello."},), (), 0,
    )


def _adapter(responses, *, limit, autonomous=True, max_retries=1):
    store = _Storage()
    budget = WeeklyRequestBudget(store, "world")
    if limit:
        budget.configure(enabled=True, weekly_limit=limit)
    requests = []
    remaining = list(responses)

    def handler(request):
        requests.append(request)
        return remaining.pop(0)

    gate = WeeklyRequestGate(budget, lambda _scope: "SELF-root" if autonomous else None)
    adapter = ChatHTTPModelAdapter(
        ChatHTTPConfig("https://fixture.invalid/chat/completions", "weekly-fixture",
                       credential_env="WEEKLY_TEST_KEY", max_retries=max_retries),
        transport=httpx.MockTransport(handler),
        attempt_accounting=gate,
    )
    return adapter, budget, requests


def _run(adapter):
    request = _request()
    return "".join(delta.text for delta in adapter.stream(request)
                   if delta.kind is ModelDeltaKind.TEXT)


def test_retry_consumes_two_physical_sends(monkeypatch):
    monkeypatch.setenv("WEEKLY_TEST_KEY", "fixture-credential")
    adapter, budget, requests = _adapter(
        [httpx.Response(429, headers={"retry-after": "0"}), _response()], limit=2)
    try:
        assert _run(adapter) == "Fixture."
        assert len(requests) == 2
        status = budget.status()
        assert status["consumed"] == 2
        assert status["states"]["rejected"] == 1
        assert status["states"]["completed"] == 1
        assert status["remaining"] == 0
    finally:
        adapter.close()


def test_exhaustion_blocks_retry_before_second_send(monkeypatch):
    monkeypatch.setenv("WEEKLY_TEST_KEY", "fixture-credential")
    adapter, budget, requests = _adapter(
        [httpx.Response(503, headers={"retry-after": "0"}), _response()], limit=1)
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            _run(adapter)
        assert failure.value.code == "autonomous_weekly_requests_exhausted"
        assert len(requests) == 1
        assert budget.status()["states"]["rejected"] == 1
    finally:
        adapter.close()


def test_broken_stream_remains_uncertain_and_not_refunded(monkeypatch):
    monkeypatch.setenv("WEEKLY_TEST_KEY", "fixture-credential")
    adapter, budget, requests = _adapter([_response(valid=False)], limit=1)
    try:
        with pytest.raises(ProviderProtocolError):
            _run(adapter)
        assert len(requests) == 1
        assert budget.status()["states"]["uncertain"] == 1
        assert budget.status()["remaining"] == 0
    finally:
        adapter.close()


def test_foreground_does_not_consume_autonomous_pool(monkeypatch):
    monkeypatch.setenv("WEEKLY_TEST_KEY", "fixture-credential")
    adapter, budget, requests = _adapter([_response()], limit=0, autonomous=False)
    try:
        assert _run(adapter) == "Fixture."
        assert len(requests) == 1
        assert budget.status()["consumed"] == 0
    finally:
        adapter.close()


def test_preflight_request_size_failure_happens_before_reservation(monkeypatch):
    monkeypatch.setenv("WEEKLY_TEST_KEY", "fixture-credential")
    adapter, budget, requests = _adapter([], limit=1)
    adapter.config = ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "weekly-fixture",
        credential_env="WEEKLY_TEST_KEY", max_request_bytes=1,
    )
    try:
        with pytest.raises(ProviderUnavailable) as failure:
            _run(adapter)
        assert failure.value.code == "provider_request_too_large"
        assert requests == []
        assert budget.status()["attempts"] == 0
    finally:
        adapter.close()
