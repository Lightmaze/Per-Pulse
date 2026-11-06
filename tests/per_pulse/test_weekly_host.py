"""One native World controls provider sends and restores the weekly policy.

Only the HTTP reply is synthetic. The page, Kernel, causal ledger, adapter,
World SQLite, API and restart path are real; no learning claims are made.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def _factory(monkeypatch, calls):
    monkeypatch.setenv("PER_PULSE_WEEK_HOST_KEY", "synthetic-credential")

    def handler(request):
        calls.append(request)
        frames = [
            {"choices": [{"index": 0, "delta": {"content": "Fixture result."},
                          "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        payload = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=payload + "data: [DONE]\n\n",
        )

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig(
                "https://fixture.invalid/chat/completions", "weekly-host-test",
                credential_env="PER_PULSE_WEEK_HOST_KEY",
            ),
            transport=httpx.MockTransport(handler),
        )
    return factory


def _settled(client, page_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        view = client.get(f"/api/pages/{page_id}").json()
        if any(m["role"] == "assistant" for m in view["messages"]) and view["page"]["status"] != "working":
            return view
        time.sleep(.05)
    raise AssertionError("The real Kernel did not settle the fixture reply")


def test_host_binds_weekly_gate_but_keeps_direct_user_work_outside_pool(tmp_path, monkeypatch):
    calls = []
    app = create_app(tmp_path, model_factory=_factory(monkeypatch, calls))
    with TestClient(app, base_url="http://localhost") as client:
        status = client.get("/api/pulse/weekly-usage")
        assert status.status_code == 200
        assert status.json()["usage"]["enabled"] is False
        world = client.get("/api/bootstrap").json()["workspaceId"]
        enabled = client.patch(
            "/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 2},
            headers={"x-per-pulse-world": world},
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["usage"]["remaining"] == 2
        page = client.post(
            "/api/pages", json={"title": "Manual task", "instruction": "Write a sentence."}
        )
        assert page.status_code == 201, page.text
        _settled(client, page.json()["page"]["id"])
        assert calls
        status = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert status["consumed"] == 0
        assert status["remaining"] == 2
        assert status["world_id"] == world

    with TestClient(create_app(tmp_path, model_factory=_factory(monkeypatch, [])),
                    base_url="http://localhost") as client:
        restored = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert restored["enabled"] is True
        assert restored["weekly_limit"] == 2
        assert restored["remaining"] == 2
        assert client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": False, "weeklyLimit": 2},
        ).status_code == 200
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["enabled"] is False


def test_weekly_api_rejects_unconfigured_spending_and_invalid_world(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        status = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert not status["enabled"]
        enabled = client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": True, "weeklyLimit": 10},
        )
        assert enabled.status_code == 409
        assert enabled.json()["error"]["code"] == "provider_unconfigured"
        mismatch = client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": False, "weeklyLimit": 0},
            headers={"x-per-pulse-world": "another-world"},
        )
        assert mismatch.status_code == 409
        assert status["world_id"] != "another-world"
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["enabled"] is False


def test_example_cannot_enable_remote_provider_budget(tmp_path):
    with TestClient(create_app(tmp_path, example=True), base_url="http://localhost") as client:
        reply = client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": True, "weeklyLimit": 1},
        )
        assert reply.status_code == 409
        assert reply.json()["error"]["code"] == "example_mode"


def test_borrowed_posture_and_coverme_sends_inherit_native_origin_pool(tmp_path):
    """A trace-only reviewer is not a new World root or an unmetered request."""
    from types import SimpleNamespace as Row

    import pytest

    from per_pulse.weekly_host import _source_for_model_request
    from pulse_harness.core.errors import ProviderUnavailable
    from pulse_harness.core.scope import HarnessScope
    from pulse_system.core.types import CausalEventKind as Kind, CausalEventSource as Source
    from pulse_system.service.coverme import _fork_scope as coverme_scope
    from pulse_system.service.expert_computation import _fork_scope as expert_scope
    from pulse_system.service.weekly_requests import WeeklyRequestBudget, WeeklyRequestGate
    from pulse_system.substrate.storage import Storage

    class Ledger:
        def __init__(self, event):
            self.event = event

        def get_turn_for_world(self, turn_id, world_id):
            return Row(event_id="root", engram_id="engram") if (
                turn_id == "turn" and world_id == "world") else None

        def get_event(self, event_id):
            return self.event if event_id == "root" else None

    native = HarnessScope(
        "world", "engram", "native-session", 0, "owner",
        context_id="work:origin", turn_id="turn",
    )
    reviewer = coverme_scope(native, "original-response-round")
    advisor = expert_scope(native, "original-advice-identity")
    user_event = Row(id="root", world_id="world", kind=Kind.STIMULUS,
                     source=Source.USER, parent_event_id=None)
    self_event = Row(id="root", world_id="world", kind=Kind.SPONTANEOUS,
                     source=Source.SELF, parent_event_id=None)

    assert _source_for_model_request(Ledger(user_event), "world", native, native) is None
    assert _source_for_model_request(Ledger(user_event), "world", native, reviewer) is None
    assert _source_for_model_request(Ledger(user_event), "world", native, advisor) is None
    assert _source_for_model_request(Ledger(self_event), "world", native, native) == "root"
    assert _source_for_model_request(Ledger(self_event), "world", native, reviewer) == "root"
    assert _source_for_model_request(Ledger(self_event), "world", native, advisor) == "root"

    # Each physical reviewer/fork attempt consumes this autonomous root's
    # remaining allowance; the original user-initiated review consumes none.
    store = Storage(tmp_path / "weekly-sidecars.db")
    try:
        budget = WeeklyRequestBudget(store, "world")
        budget.configure(enabled=True, weekly_limit=2)
        gate = WeeklyRequestGate(
            budget, lambda scope: _source_for_model_request(
                Ledger(self_event), "world", native, scope))
        for candidate in (reviewer, advisor):
            ticket = gate.reserve(candidate)
            gate.dispatch(ticket)
            gate.settle(ticket, "completed")
        assert budget.status()["consumed"] == 2
        with pytest.raises(ProviderUnavailable) as exhausted:
            gate.reserve(native)
        assert exhausted.value.code == "autonomous_weekly_requests_exhausted"
    finally:
        store.close()

    foreign = HarnessScope("other", "engram", reviewer.session_id, 0, "owner",
                           context_id=reviewer.context_id, turn_id=reviewer.turn_id,
                           capability_digest=reviewer.capability_digest)
    with pytest.raises(ProviderUnavailable) as invalid:
        _source_for_model_request(Ledger(user_event), "world", native, foreign)
    assert invalid.value.code == "autonomous_request_source_unverified"
    wrong_fork = HarnessScope(
        "world", "engram", "expert:" + "a" * 64, 0, "owner",
        context_id=advisor.context_id, turn_id="compute:" + "b" * 64,
        capability_digest=advisor.capability_digest,
    )
    with pytest.raises(ProviderUnavailable):
        _source_for_model_request(Ledger(user_event), "world", native, wrong_fork)
