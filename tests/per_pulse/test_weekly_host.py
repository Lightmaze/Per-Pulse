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


def _enqueue_self(runtime, content, key):
    from pulse_system.core.types import CausalEventDomain, CausalEventKind, CausalEventSource
    # Host-owned input, like native ContextPulse reentry. The public user
    # injection API correctly rejects content with an untyped SELF label.
    return runtime.causal_ledger.enqueue(
        world_id=runtime.world_id, flow=None, domain=CausalEventDomain.PULSE,
        kind=CausalEventKind.SPONTANEOUS, source=CausalEventSource.SELF,
        content=content, engram_id=runtime.continuity_engram_id,
        idempotency_key=key, runtime_fence=runtime._current_runtime_fence(),
    ).id


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
        before = len(calls)
        continued = client.post(
            f"/api/pages/{page.json()['page']['id']}/messages",
            json={"content": "Continue in the same native Work."},
        )
        assert continued.status_code == 202
        deadline = time.monotonic() + 20
        while len(calls) == before and time.monotonic() < deadline:
            time.sleep(.02)
        assert len(calls) > before
        _settled(client, page.json()["page"]["id"])
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


def test_native_self_turn_uses_weekly_pool_while_exhaustion_keeps_home_usable(tmp_path, monkeypatch):
    calls = []
    with TestClient(create_app(tmp_path, model_factory=_factory(monkeypatch, calls)),
                    base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 1}).status_code == 200
        _enqueue_self(runtime, "Synthetic autonomous practice.", "fixture-first-self")
        _settled(client, "home")
        usage = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert usage["consumed"] == 1 and usage["remaining"] == 0
        assert usage["states"]["completed"] == 1
        before = len(calls)
        event_id = _enqueue_self(runtime, "Synthetic exhausted continuation.", "fixture-exhausted-self")
        # Drive the ordinary scheduler repeatedly with no allowance. The same
        # durable event waits; no Kernel turn is created and no send occurs.
        for _ in range(3):
            runtime.tick_once()
        event = runtime.causal_ledger.get_event(event_id)
        assert event.status.value == "queued"
        assert len(calls) == before
        sent = client.post("/api/pages/home/messages", json={"content": "A direct user request."})
        assert sent.status_code == 202
        deadline = time.monotonic() + 20
        while len(calls) == before and time.monotonic() < deadline:
            time.sleep(.02)
        assert len(calls) > before
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == 1
        assert runtime.causal_ledger.get_event(event_id).status.value == "queued"
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 2}).status_code == 200
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            event = runtime.causal_ledger.get_event(event_id)
            if event.status.value == "settled":
                break
            time.sleep(.02)
        assert event.status.value == "settled"
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == 2


def test_paused_native_event_survives_offline_reopen_then_resumes_once(tmp_path, monkeypatch):
    from per_pulse.service import PulseSettings
    settings = PulseSettings(enabled=True, spontaneous_rate=0)
    calls = []
    with TestClient(create_app(tmp_path, pulse=settings, model_factory=_factory(monkeypatch, calls)),
                    base_url="http://localhost") as client:
        assert client.patch("/api/pulse", json={"paused": True}).status_code == 200
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 2}).status_code == 200
        runtime = client.app.state.pages.runtime
        event_id = _enqueue_self(runtime, "Synthetic retained practice.", "fixture-retained-self")
        for _ in range(3):
            runtime.tick_once()
        assert runtime.causal_ledger.get_event(event_id).status.value == "queued"
        assert calls == []

    with TestClient(create_app(tmp_path, pulse=settings), base_url="http://localhost") as client:
        assert client.patch("/api/pulse", json={"paused": False}).status_code == 200
        runtime = client.app.state.pages.runtime
        for _ in range(3):
            assert client.get("/api/pulse/observation").status_code == 200
        assert runtime.causal_ledger.get_event(event_id).status.value == "queued"
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == 0

    with TestClient(create_app(tmp_path, pulse=settings, model_factory=_factory(monkeypatch, calls)),
                    base_url="http://localhost") as client:
        _settled(client, "home")
        runtime = client.app.state.pages.runtime
        assert runtime.causal_ledger.get_event(event_id).status.value == "settled"
        assert len(calls) == 1
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == 1


def test_example_cannot_enable_remote_provider_budget(tmp_path):
    with TestClient(create_app(tmp_path, example=True), base_url="http://localhost") as client:
        reply = client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": True, "weeklyLimit": 1},
        )
        assert reply.status_code == 409
        assert reply.json()["error"]["code"] == "example_mode"


def test_user_delegation_and_sourced_report_complete_while_autonomous_allowance_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_WEEK_HOST_KEY", "synthetic-credential")
    calls = []
    def factory(scope):
        def handler(request):
            calls.append(request)
            messages = json.loads(request.content)["messages"]
            boundary = max(i for i, item in enumerate(messages) if item["role"] == "user")
            current = messages[boundary:]
            finished_tool = any(item["role"] == "tool" for item in current)
            if not finished_tool and (scope.context_id.startswith("work:") or "Delegate a task." in messages[boundary]["content"]):
                args = ({"op": "report", "kind": "finding", "note": "The delegated finding is ready."}
                        if scope.context_id.startswith("work:") else
                        {"op": "create", "title": "Weekly delegation", "instruction": "Return a small finding."})
                delta = {"tool_calls": [{"index": 0, "id": "fixture-work", "type": "function",
                         "function": {"name": "work", "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"content": "The delegated work is complete. [Synthetic fixture]"}, "stop"
            frames = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(item) + "\n\n" for item in frames) + "data: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "delegation-week-test",
            credential_env="PER_PULSE_WEEK_HOST_KEY"), transport=httpx.MockTransport(handler))

    with TestClient(create_app(tmp_path, model_factory=factory, posture=False), base_url="http://localhost") as client:
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["enabled"] is False
        assert client.post("/api/pages/home/messages", json={"content": "Delegate a task."}).status_code == 202
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            fronts = client.get("/api/bootstrap").json()["pages"]
            works = [item for item in fronts if item["title"] == "Weekly delegation"]
            if works:
                break
            time.sleep(.03)
        assert works
        _settled(client, works[0]["id"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            home = client.get("/api/pages/home").json()
            if sum(item["role"] == "assistant" for item in home["messages"]) >= 2:
                break
            time.sleep(.03)
        assert any(item.get("activation") == "work_report" for item in home["messages"])
        assert len(calls) >= 5
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == 0


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
