"""/tick: explicit, bounded quiet continuation of one page.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The model is a local HTTP test transport; World, Kernel and SQLite are real.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from per_pulse.execution import PageExecution
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.core.causality import CausalTransitionError
from pulse_system.service.runtime import ServiceError


class Transport:
    def __init__(self, monkeypatch, *, delay: float = 0.0):
        monkeypatch.setenv("PER_PULSE_TICK_TEST_KEY", "synthetic-test-credential")
        self.requests: list[list[dict]] = []
        self.lock = threading.Lock()
        self.delay = delay
        self.before_reply = None

    def factory(self, scope):
        def respond(request):
            body = json.loads(request.content)
            if self.before_reply is not None:
                self.before_reply()
            if self.delay:
                time.sleep(self.delay)
            with self.lock:
                self.requests.append(body["messages"])
                output = f"STEP_{len(self.requests)}"
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": output}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}},
            ]
            stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=stream + "data: [DONE]\n\n")

        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "tick-fixture",
                           credential_env="PER_PULSE_TICK_TEST_KEY"),
            transport=httpx.MockTransport(respond),
        )


def client_for(path, **kwargs):
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def eventually(probe, predicate, *, timeout=10):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = probe()
        if predicate(value):
            return value
        time.sleep(0.025)
    pytest.fail(f"condition did not settle: {value!r}")


def view(client, page_id):
    response = client.get(f"/api/pages/{page_id}")
    assert response.status_code == 200, response.text
    return response.json()


def replies(payload):
    return [m["content"] for m in payload["messages"] if m["role"] == "assistant"]


def new_work(client, instruction="LOCAL_HISTORY"):
    response = client.post("/api/pages", json={"title": "Thinking", "instruction": instruction})
    assert response.status_code == 201, response.text
    page_id = response.json()["page"]["id"]
    eventually(lambda: view(client, page_id), lambda v: len(replies(v)) == 1)
    return page_id


def test_tick_gives_exactly_n_quiet_turns_from_the_page_history(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    # Background pulses are off: /tick is an explicit request, not background work.
    with client_for(tmp_path, model_factory=transport.factory) as client:
        work = new_work(client)
        response = client.post(f"/api/pages/{work}/tick", json={"count": 3})
        assert response.status_code == 201, response.text
        assert response.json()["tick"]["status"] == "running"
        done = eventually(lambda: view(client, work), lambda v: v["tick"]["status"] == "complete")
        assert done["tick"]["completed"] == done["tick"]["target"] == 3
        assert replies(done) == ["STEP_1", "STEP_2", "STEP_3", "STEP_4"]
        time.sleep(0.2)
        assert len(transport.requests) == 4, "A finished run never makes a fourth step."
        for step in transport.requests[1:]:
            text = json.dumps(step)
            assert "LOCAL_HISTORY" in text
            assert step[-1] == {"role": "user", "content": ""}, "A time step carries no new goal."
            assert "tick" not in text.lower()
        assert all(m["role"] != "user" for m in done["messages"][1:]), "Steps are not shown as your messages."
        pulse = client.get("/api/pulse").json()
        assert pulse["budget"]["used"] == 0
        assert [run["pageId"] for run in pulse["tickRuns"]] == [work]
        home = view(client, "home")
        assert home["messages"] == [] and home["tick"] is None

    with client_for(tmp_path, model_factory=transport.factory) as client:
        time.sleep(0.2)
        assert len(transport.requests) == 4
        assert view(client, work)["tick"]["status"] == "complete"


def test_tick_needs_a_finished_reply_a_model_and_a_valid_count(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    with client_for(tmp_path / "connected", model_factory=transport.factory) as client:
        empty_home = client.post("/api/pages/home/tick", json={"count": 1})
        assert empty_home.status_code == 409
        assert empty_home.json()["error"]["code"] == "tick_needs_conversation"
        work = new_work(client)
        for count in (0, 257):
            invalid = client.post(f"/api/pages/{work}/tick", json={"count": count})
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "tick_count_invalid"
        assert client.post("/api/pages/missing/tick", json={"count": 1}).status_code == 404
    with client_for(tmp_path / "offline", model_factory=None) as client:
        response = client.post("/api/pages/home/tick", json={"count": 1})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "tick_model_unavailable"


def test_stop_and_a_new_message_end_the_run_before_the_next_step(tmp_path, monkeypatch):
    transport = Transport(monkeypatch, delay=0.15)
    with client_for(tmp_path, model_factory=transport.factory, pulse=PulseSettings(enabled=True)) as client:
        work = new_work(client)
        assert client.post(f"/api/pages/{work}/tick", json={"count": 20}).status_code == 201
        busy = client.post(f"/api/pages/{work}/tick", json={"count": 1})
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "tick_running"
        stopped = client.post(f"/api/pages/{work}/tick/stop", json={})
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["tick"]["status"] == "stopped"
        assert stopped.json()["tick"]["reason"] == "operator"
        # A step already running may finish; a queued, unstarted step is cancelled.
        time.sleep(0.5)
        stopped_at = len(transport.requests)
        assert stopped_at in (1, 2)
        time.sleep(0.5)
        assert len(transport.requests) == stopped_at, "Stop takes effect before the next step."

        assert client.post(f"/api/pages/{work}/tick", json={"count": 20}).status_code == 201
        eventually(lambda: view(client, work), lambda v: v["tick"]["completed"] >= 1)
        message = client.post(f"/api/pages/{work}/messages", json={"content": "I'll take it from here."})
        assert message.status_code == 202
        ended = eventually(lambda: view(client, work), lambda v: v["tick"]["status"] == "stopped")
        assert ended["tick"]["reason"] == "user_message"
        eventually(lambda: view(client, work), lambda v: v["page"]["status"] == "idle")
        count = len(transport.requests)
        time.sleep(0.4)
        assert len(transport.requests) == count


def test_stop_cancels_a_queued_step_that_has_not_started(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    with client_for(tmp_path, model_factory=transport.factory) as client:
        work = new_work(client)
        runtime = client.app.state.pages.runtime
        dispatch = runtime._engine.tick
        monkeypatch.setattr(runtime._engine, "tick", lambda *args, **kwargs: [])
        started = client.post(f"/api/pages/{work}/tick", json={"count": 5})
        assert started.status_code == 201 and started.json()["tick"]["delivered"] == 1
        steps = runtime.causal_ledger.list_events(kind="spontaneous", limit=10)
        assert [event.status.value for event in steps] == ["queued"]
        stopped = client.post(f"/api/pages/{work}/tick/stop", json={}).json()["tick"]
        assert stopped["status"] == "stopped" and stopped["delivered"] == stopped["completed"] == 0
        assert runtime.causal_ledger.get_event(steps[0].id).status.value == "cancelled"
        monkeypatch.setattr(runtime._engine, "tick", dispatch)
        time.sleep(0.4)
        assert len(transport.requests) == 1, "A cancelled step never reaches the model."
        assert replies(view(client, work)) == ["STEP_1"]


@pytest.mark.parametrize("home", [False, True], ids=["work", "home"])
@pytest.mark.parametrize("interrupt_takeover", [False, True], ids=["durable-stop", "restart-before-stop"])
def test_native_user_input_cancels_queued_steps_across_restart(tmp_path, monkeypatch, home, interrupt_takeover):
    transport = Transport(monkeypatch)
    with client_for(tmp_path, model_factory=transport.factory) as client:
        runtime = client.app.state.pages.runtime
        if home:
            runtime.inject(runtime.continuity_engram_id, "LOCAL_HISTORY")
            page_id = "home"
            eventually(lambda: view(client, page_id), lambda v: len(replies(v)) == 1)
        else:
            page_id = new_work(client)
        monkeypatch.setattr(runtime._engine, "tick", lambda *args, **kwargs: [])
        assert client.post(f"/api/pages/{page_id}/tick", json={"count": 5}).status_code == 201
        step, = runtime.causal_ledger.list_events(kind="spontaneous", limit=10)
        if interrupt_takeover:
            # Simulate exit after the input commit but before its cancellation.
            # Startup must reconcile that durable input before Engine dispatch.
            monkeypatch.setattr(runtime.context_pulse, "stop_ticks", lambda *args, **kwargs: None)
        if home:
            runtime.inject(runtime.continuity_engram_id, "I will take over now.")
        else:
            runtime.send_task_front_message(page_id, "I will take over now.")
        if not interrupt_takeover:
            stopped = view(client, page_id)["tick"]
            assert stopped["status"] == "stopped" and stopped["reason"] == "user_message"
            assert stopped["delivered"] == stopped["completed"] == 0
            assert runtime.causal_ledger.get_event(step.id).status.value == "cancelled"
        assert len(transport.requests) == 1

    with client_for(tmp_path, model_factory=transport.factory) as client:
        continued = eventually(lambda: view(client, page_id), lambda v: len(replies(v)) == 2)
        assert continued["tick"]["status"] == "stopped"
        assert continued["tick"]["reason"] == "user_message"
        assert continued["tick"]["delivered"] == continued["tick"]["completed"] == 0
        assert client.app.state.pages.runtime.causal_ledger.get_event(step.id).status.value == "cancelled"
        time.sleep(0.2)
        assert len(transport.requests) == 2, "Only the new user message continues after restart."
        assert transport.requests[-1][-1] == {"role": "user", "content": "I will take over now."}


def test_rejected_other_context_and_old_idempotent_input_do_not_stop_ticks(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    with client_for(tmp_path, model_factory=transport.factory) as client:
        runtime = client.app.state.pages.runtime
        subject = runtime.continuity_engram_id
        original = runtime.inject(subject, "Original Home input.", idempotency_key="tick-original-input")
        eventually(lambda: view(client, "home"), lambda v: len(replies(v)) == 1)
        other = new_work(client)
        dispatch = runtime._engine.tick
        monkeypatch.setattr(runtime._engine, "tick", lambda *args, **kwargs: [])
        assert client.post("/api/pages/home/tick", json={"count": 5}).status_code == 201
        step, = runtime.causal_ledger.list_events(kind="spontaneous", limit=10)
        with pytest.raises(ServiceError):
            runtime.inject(subject, " ")
        assert runtime.inject(subject, "Original Home input.", idempotency_key="tick-original-input") == original
        runtime.send_task_front_message(other, "A separate Work remains independent.")
        runtime.context_pulse.tick()
        assert view(client, "home")["tick"]["status"] == "running"
        assert runtime.causal_ledger.get_event(step.id).status.value == "queued"
        client.post("/api/pages/home/tick/stop", json={})
        monkeypatch.setattr(runtime._engine, "tick", dispatch)
        eventually(lambda: view(client, other), lambda v: len(replies(v)) == 2)
        assert len(transport.requests) == 3


def test_user_takeover_allows_an_already_running_step_to_finish(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    with client_for(tmp_path, model_factory=transport.factory) as client:
        page_id = new_work(client)
        runtime = client.app.state.pages.runtime

        def hold_reply():
            entered.set()
            assert release.wait(5), "The test must release its synthetic response."

        transport.before_reply = hold_reply
        try:
            assert client.post(f"/api/pages/{page_id}/tick", json={"count": 5}).status_code == 201
            assert entered.wait(5)
            step, = runtime.causal_ledger.list_events(kind="spontaneous", limit=10)
            assert runtime.causal_ledger.get_event(step.id).status.value == "running"
            runtime.send_task_front_message(page_id, "Continue with my new input.")
            stopped = view(client, page_id)["tick"]
            assert stopped["status"] == "stopped" and stopped["reason"] == "user_message"
            assert runtime.causal_ledger.get_event(step.id).status.value == "running"
        finally:
            transport.before_reply = None
            release.set()
        finished = eventually(lambda: view(client, page_id),
                              lambda v: len(replies(v)) == 3 and v["tick"]["completed"] == 1)
        assert finished["tick"]["delivered"] == 1
        assert finished["tick"]["status"] == "stopped" and finished["tick"]["reason"] == "user_message"
        assert runtime.causal_ledger.get_event(step.id).status.value == "settled"
        time.sleep(0.2)
        assert len(transport.requests) == 3


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("reason", ["user_message", "operator"])
def test_stopping_counts_a_settled_step_before_controller_observation(tmp_path, monkeypatch, count, reason):
    transport = Transport(monkeypatch)
    with client_for(tmp_path, model_factory=transport.factory) as client:
        page_id = new_work(client)
        runtime = client.app.state.pages.runtime
        controller = runtime.context_pulse
        advance = controller._advance_tick

        def hold_after_first_delivery(key, now):
            if controller._state["tick_runs"][key]["last_event_id"] is None:
                advance(key, now)

        monkeypatch.setattr(controller, "_advance_tick", hold_after_first_delivery)
        assert client.post(f"/api/pages/{page_id}/tick", json={"count": count}).status_code == 201
        step, = runtime.causal_ledger.list_events(kind="spontaneous", limit=10)
        eventually(lambda: runtime.causal_ledger.get_event(step.id), lambda event: event.status.value == "settled")
        assert view(client, page_id)["tick"]["completed"] == 0
        if reason == "user_message":
            runtime.send_task_front_message(page_id, "Continue after that completed step.")
        else:
            assert client.post(f"/api/pages/{page_id}/tick/stop", json={}).status_code == 200
        run = view(client, page_id)["tick"]
        assert run["delivered"] == run["completed"] == 1
        complete = reason == "user_message" and count == 1
        assert run["status"] == ("complete" if complete else "stopped")
        assert run["reason"] == (None if complete else reason)
        monkeypatch.setattr(controller, "_advance_tick", advance)
        expected = 3 if reason == "user_message" else 2
        eventually(lambda: view(client, page_id), lambda v: len(replies(v)) == expected)
        time.sleep(0.2)
        assert len(transport.requests) == expected


@pytest.mark.parametrize("outcome", ["settled", "failed", "cancelled"])
@pytest.mark.parametrize("reconcile_after_restart", [False, True], ids=["live", "restart"])
def test_operator_stop_counts_only_a_successful_running_step(tmp_path, monkeypatch, outcome, reconcile_after_restart):
    transport = Transport(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    with client_for(tmp_path, model_factory=transport.factory) as client:
        page_id = new_work(client)
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)

        def hold_reply():
            entered.set()
            assert release.wait(5), "The test must release its synthetic response."
            if outcome == "failed":
                raise httpx.ReadTimeout("Explicit synthetic provider failure.")

        transport.before_reply = hold_reply
        try:
            assert client.post(f"/api/pages/{page_id}/tick", json={"count": 5}).status_code == 201
            assert entered.wait(5)
            step, = runtime.causal_ledger.list_events(kind="spontaneous", limit=10)
            assert step.status.value == "running"
            stopped = client.post(f"/api/pages/{page_id}/tick/stop", json={}).json()["tick"]
            assert stopped["status"] == "stopped" and stopped["reason"] == "operator"
            assert stopped["delivered"] == 1 and stopped["completed"] == 0
            if reconcile_after_restart:
                # Persist the stopped run before its late result is observed.
                monkeypatch.setattr(runtime.context_pulse, "_count_tick_reply", lambda *args: None)
            if outcome == "cancelled":
                working = execution.snapshot(page_id)
                assert execution.cancel(page_id, working["turnId"])["accepted"]
        finally:
            release.set()
        eventually(lambda: runtime.causal_ledger.get_event(step.id),
                   lambda event: event.status.value not in {"queued", "running"})
        if outcome == "settled":
            eventually(lambda: view(client, page_id), lambda v: len(replies(v)) == 2)
            assert runtime.causal_ledger.get_event(step.id).status.value == "settled"
        else:
            expected = "error" if outcome == "failed" else "stopped"
            eventually(lambda: execution.snapshot(page_id), lambda state: state["status"] == expected)
            assert runtime.causal_ledger.get_event(step.id).status.value != "settled"
            assert len(replies(view(client, page_id))) == 1
        transport.before_reply = None
        completed = int(outcome == "settled")
        if not reconcile_after_restart:
            finished = eventually(lambda: view(client, page_id), lambda v: v["tick"]["completed"] == completed)
            assert finished["tick"]["status"] == "stopped" and finished["tick"]["reason"] == "operator"
        else:
            assert view(client, page_id)["tick"]["completed"] == 0

    with client_for(tmp_path, model_factory=transport.factory) as client:
        runtime = client.app.state.pages.runtime
        run = eventually(lambda: view(client, page_id), lambda v: v["tick"]["completed"] == completed)["tick"]
        assert run["delivered"] == 1
        assert run["status"] == "stopped" and run["reason"] == "operator"
        for _ in range(3):
            runtime.context_pulse.tick()
            repeated = client.post(f"/api/pages/{page_id}/tick/stop", json={}).json()["tick"]
            assert repeated == run
        assert [event.id for event in runtime.causal_ledger.list_events(kind="spontaneous", limit=10)] == [step.id]


def test_user_admission_cancels_queued_tick_before_a_concurrent_claim(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    with client_for(tmp_path, model_factory=transport.factory) as client:
        page_id = new_work(client)
        runtime = client.app.state.pages.runtime
        ledger = runtime.causal_ledger
        monkeypatch.setattr(runtime._engine, "tick", lambda *args, **kwargs: [])
        assert client.post(f"/api/pages/{page_id}/tick", json={"count": 5}).status_code == 201
        step, = ledger.list_events(kind="spontaneous", limit=10)
        accepted, claim_attempted, claimed = threading.Event(), threading.Event(), threading.Event()
        outcomes = []
        enqueue = ledger.enqueue

        def hold_after_acceptance(*args, **kwargs):
            event = enqueue(*args, **kwargs)
            if kwargs.get("content") == "Take over at the claim boundary.":
                accepted.set()
                assert claim_attempted.wait(5)
                assert not claimed.wait(0.1), "A claim must wait until accepted input cancels its queued step."
            return event

        def claim_step():
            assert accepted.wait(5)
            claim_attempted.set()
            try:
                ledger.begin_turn(step.id, runtime.continuity_engram_id, "",
                                  execution_context=page_id, runtime_fence=runtime._current_runtime_fence())
                outcomes.append("claimed")
            except CausalTransitionError:
                outcomes.append("cancelled")
            finally:
                claimed.set()

        monkeypatch.setattr(ledger, "enqueue", hold_after_acceptance)
        claimant = threading.Thread(target=claim_step)
        claimant.start()
        try:
            runtime.send_task_front_message(page_id, "Take over at the claim boundary.")
        finally:
            claimant.join(timeout=5)
        assert not claimant.is_alive()
        assert outcomes == ["cancelled"]
        assert ledger.get_event(step.id).status.value == "cancelled"
        assert view(client, page_id)["tick"]["reason"] == "user_message"
