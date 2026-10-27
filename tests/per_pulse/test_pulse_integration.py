"""Native Pulse acceptance paths with explicit local HTTP transport fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
These tests exercise real World/Kernel persistence. They do not contact or
evaluate an external model; fixture output is confined to the test transport.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


class TransportFixture:
    def __init__(self, monkeypatch, *, failure=False, failure_after=None):
        monkeypatch.setenv("PER_PULSE_CORE_TEST_KEY", "synthetic-test-credential")
        self.requests: list[dict] = []
        self.scopes: list = []
        self.lock = threading.Lock()
        self.failure = failure
        self.failure_after = failure_after

    def factory(self, scope):
        def respond(request):
            body = json.loads(request.content)
            with self.lock:
                self.requests.append(body)
                self.scopes.append(scope)
            if self.failure or (self.failure_after is not None and len(self.requests) > self.failure_after):
                return httpx.Response(503, text="Explicit fixture failure.")
            text = json.dumps(body["messages"])
            output = ("B_FIXTURE_OUTPUT" if "B_LOCAL_HISTORY" in text else
                      "C_FIXTURE_OUTPUT" if "C_LOCAL_HISTORY" in text else "A_FIXTURE_OUTPUT")
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": output}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 32, "completion_tokens": 8, "total_tokens": 40}},
            ]
            stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=stream + "data: [DONE]\n\n")

        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "pulse-core-fixture",
                           credential_env="PER_PULSE_CORE_TEST_KEY"),
            transport=httpx.MockTransport(respond),
        )


def client_for(path, **kwargs):
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def eventually(probe, predicate, *, timeout=8):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = probe()
        if predicate(value):
            return value
        time.sleep(0.025)
    pytest.fail(f"Pulse condition did not settle: {value!r}")


def page(client, title, instruction):
    response = client.post("/api/pages", json={"title": title, "instruction": instruction})
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def view(client, page_id):
    response = client.get(f"/api/pages/{page_id}")
    assert response.status_code == 200, response.text
    return response.json()


def assistant_count(payload):
    return sum(message["role"] == "assistant" for message in payload["messages"])


def connect(client, source, target, **kwargs):
    response = client.post("/api/pulse/connections", json={
        "sourcePageId": source, "targetPageId": target, **kwargs,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_native_context_diffusion_preserves_subject_history_and_causal_parent(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=0,
                             check_interval_seconds=0.01, refractory_seconds=0,
                             max_propagation_depth=1,
                             max_background_pulses_per_hour=4)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Source context", "A_LOCAL_HISTORY")
        target = page(client, "Related context", "B_LOCAL_HISTORY")
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 1)
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 1)
        connect(client, source, target, weight=0.9)
        connect(client, target, source, weight=0.9)
        response = client.post(f"/api/pages/{source}/messages", json={"content": "A_NEW_INPUT"})
        assert response.status_code == 202
        original_event_id = response.json()["messageId"]
        result = eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        eventually(lambda: client.get("/api/pulse").json(), lambda v: v["budget"]["used"] == 1)
        time.sleep(0.15)
        assert len(fixture.requests) == 4, "The reverse edge must not escape the depth ceiling."
        runtime = client.app.state.pages.runtime
        subject_id = runtime.continuity_engram_id
        source_center = runtime.world.get_task_front(source).center_id
        target_center = runtime.world.get_task_front(target).center_id
        assert runtime.world.get_task_front(source).focal_engram_id == subject_id
        assert runtime.world.get_task_front(target).focal_engram_id == subject_id
        assert {scope.engram_id for scope in fixture.scopes} == {subject_id}
        assert len({scope.session_id for scope in fixture.scopes}) == 2
        target_request = fixture.requests[-1]["messages"]
        assert "B_LOCAL_HISTORY" in json.dumps(target_request)
        assert "A_FIXTURE_OUTPUT" in json.dumps(target_request)
        assert "A_LOCAL_HISTORY" not in json.dumps(target_request)
        assert "A_NEW_INPUT" not in json.dumps(target_request)
        propagations = runtime.causal_ledger.list_events(kind="propagation", limit=100)
        assert len(propagations) == 1
        propagated = propagations[0]
        parent = runtime.causal_ledger.get_event(propagated.parent_event_id)
        assert parent.kind.value == "assistant_result"
        assert parent.center_id == source_center
        assert parent.parent_event_id == original_event_id
        assert propagated.center_id == target_center
        assert propagated.engram_id == parent.engram_id == subject_id
        assert propagated.causal_id == parent.causal_id
        assert propagated.content == parent.content == "A_FIXTURE_OUTPUT"
        visible = next(message for message in result["messages"] if message["id"] == propagated.id)
        assert visible["role"] == "system", "A connection must not impersonate the user."
        assert visible["sourcePageId"] == source
        assert visible["sourceMessageId"] == parent.id
        assert visible["activation"] == "propagation"
        assert not view(client, "home")["messages"]
        result_ids = [message["id"] for message in result["messages"]]

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        time.sleep(0.15)
        assert len(fixture.requests) == 4, "Opening the same body must not replay settled results."
        assert [m["id"] for m in view(client, target)["messages"]] == result_ids
        state = client.get("/api/pulse").json()
        assert state["budget"]["used"] == 1
        assert len(state["connections"]) == 2


def test_endogenous_reentry_is_bounded_and_keeps_its_budget_across_restart(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=1,
                             check_interval_seconds=0.01, refractory_seconds=0.05,
                             max_background_pulses_per_hour=2)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        local = page(client, "Continuing thought", "A_LOCAL_HISTORY")
        settled = eventually(lambda: view(client, local), lambda v: assistant_count(v) == 3)
        time.sleep(0.2)
        assert len(fixture.requests) == 3
        assert all("A_LOCAL_HISTORY" in json.dumps(r["messages"]) for r in fixture.requests)
        runtime = client.app.state.pages.runtime
        impulses = runtime.causal_ledger.list_events(kind="spontaneous", limit=100)
        assert len(impulses) == 2
        assert all(event.content is None for event in impulses)
        assert all(event.center_id == runtime.world.get_task_front(local).center_id for event in impulses)
        for event in impulses:
            source = runtime.causal_ledger.get_event(event.parent_event_id)
            assert source.kind.value == "assistant_result"
            assert source.engram_id == event.engram_id == runtime.continuity_engram_id
        assert client.get("/api/pulse").json()["budget"]["remaining"] == 0
        ids = [message["id"] for message in settled["messages"]]

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        time.sleep(0.2)
        assert len(fixture.requests) == 3
        assert client.get("/api/pulse").json()["budget"]["remaining"] == 0
        assert [m["id"] for m in view(client, local)["messages"]] == ids
        response = client.post(f"/api/pages/{local}/messages", json={"content": "User can still continue."})
        assert response.status_code == 202
        eventually(lambda: view(client, local), lambda v: assistant_count(v) == 4)
        assert len(fixture.requests) == 4, "Background exhaustion must not block foreground input."


def test_offline_and_example_profiles_do_not_run_background_models(tmp_path, monkeypatch):
    settings = PulseSettings(enabled=True, spontaneous_rate=1,
                             check_interval_seconds=0.01, refractory_seconds=0)
    with client_for(tmp_path / "offline", pulse=settings, model_factory=None) as client:
        local = page(client, "Saved while offline", "A_LOCAL_HISTORY")
        time.sleep(0.08)
        assert not client.get("/api/pulse").json()["running"]
        assert assistant_count(view(client, local)) == 0
        assert client.app.state.pages.runtime.tick_count == 0
    fixture = TransportFixture(monkeypatch)
    with client_for(tmp_path / "example", example=True, pulse=settings,
                    model_factory=fixture.factory) as client:
        assert not client.get("/api/pulse").json()["running"]
        assert fixture.requests == []
        assert client.app.state.pages.runtime.tick_count == 0


def test_failed_provider_output_does_not_become_a_reentry_source(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch, failure=True)
    settings = PulseSettings(enabled=True, spontaneous_rate=1,
                             check_interval_seconds=0.01, refractory_seconds=0)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        local = page(client, "Failed turn", "A_LOCAL_HISTORY")
        failed = eventually(lambda: view(client, local), lambda v: v["page"]["status"] == "error")
        time.sleep(0.15)
        assert assistant_count(failed) == 0
        assert len(fixture.requests) == 1
        assert client.get("/api/pulse").json()["budget"]["used"] == 0
        assert not client.app.state.pages.runtime.causal_ledger.list_events(kind="spontaneous", limit=100)


def test_committed_delivery_survives_checkpoint_failure_without_duplicate_or_quota_refill(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0,
                             check_interval_seconds=0.01, max_background_pulses_per_hour=1)
    checkpoint_failed = threading.Event()
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Source context", "A_LOCAL_HISTORY")
        target = page(client, "Target context", "B_LOCAL_HISTORY")
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 1)
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 1)
        connect(client, source, target)
        runtime = client.app.state.pages.runtime
        controller = runtime.context_pulse
        save = controller._save

        def interrupt_checkpoint(now):
            if runtime.causal_ledger.list_events(kind="propagation", limit=100):
                checkpoint_failed.set()
                raise OSError("Synthetic crash between durable effect and projection checkpoint.")
            save(now)

        monkeypatch.setattr(controller, "_save", interrupt_checkpoint)
        sent = client.post(f"/api/pages/{source}/messages", json={"content": "A_AFTER_CONNECTION"})
        assert sent.status_code == 202
        eventually(checkpoint_failed.is_set, bool)
        with runtime.storage._lock:
            deliveries = runtime.causal_ledger.list_events(kind="propagation", limit=100)
            assert len(deliveries) == 1
            delivery_id = deliveries[0].id
            parent = runtime.causal_ledger.get_event(deliveries[0].parent_event_id)
            durable = runtime.storage.load_component_state(controller.key)
            assert durable["cursor"] < parent.seq
        assert len(fixture.requests) == 3

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        time.sleep(0.15)
        runtime = client.app.state.pages.runtime
        deliveries = runtime.causal_ledger.list_events(kind="propagation", limit=100)
        assert [event.id for event in deliveries] == [delivery_id]
        assert deliveries[0].status.value == "settled"
        assert client.get("/api/pulse").json()["budget"]["used"] == 1
        assert len(fixture.requests) == 4


def test_fanout_quota_is_atomic_and_restart_does_not_admit_the_other_branch(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0,
                             check_interval_seconds=0.01, max_background_pulses_per_hour=1)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Source context", "A_LOCAL_HISTORY")
        first = page(client, "First target", "B_LOCAL_HISTORY first")
        second = page(client, "Second target", "B_LOCAL_HISTORY second")
        for local in (source, first, second):
            eventually(lambda local=local: view(client, local), lambda v: assistant_count(v) == 1)
        connect(client, source, first)
        connect(client, source, second)
        sent = client.post(f"/api/pages/{source}/messages", json={"content": "A_FANOUT"})
        assert sent.status_code == 202
        eventually(lambda: len(fixture.requests), lambda count: count == 5)
        time.sleep(0.15)
        runtime = client.app.state.pages.runtime
        assert len(runtime.causal_ledger.list_events(kind="propagation", limit=100)) == 1
        assert client.get("/api/pulse").json()["budget"]["used"] == 1
        assert len(fixture.requests) == 5
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        time.sleep(0.2)
        assert len(fixture.requests) == 5
        assert len(client.app.state.pages.runtime.causal_ledger.list_events(kind="propagation", limit=100)) == 1
        assert client.get("/api/pulse").json()["budget"]["remaining"] == 0


def test_failed_reentry_does_not_replay_old_success_as_an_infinite_retry(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch, failure_after=1)
    settings = PulseSettings(enabled=True, spontaneous_rate=1, refractory_seconds=0.05,
                             check_interval_seconds=0.01, max_background_pulses_per_hour=3)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        local = page(client, "Continuing thought", "A_LOCAL_HISTORY")
        runtime = client.app.state.pages.runtime
        failed = eventually(
            lambda: runtime.causal_ledger.list_events(kind="spontaneous", limit=100),
            lambda rows: len(rows) == 1 and rows[0].status.value in {"failed", "uncertain"},
        )
        failed_id = failed[0].id
        time.sleep(0.2)
        assert len(fixture.requests) == 2
        assert assistant_count(view(client, local)) == 1
        assert client.get("/api/pulse").json()["budget"]["remaining"] == 2
        diagnostic = client.get("/api/pulse").json()["recentActivations"][0]
        assert diagnostic["eventId"] == failed_id
        assert diagnostic["pageId"] == local
        assert diagnostic["status"] in {"failed", "uncertain"}
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        time.sleep(0.2)
        assert len(fixture.requests) == 2
        assert [event.id for event in client.app.state.pages.runtime.causal_ledger.list_events(
            kind="spontaneous", limit=100)] == [failed_id]


def test_inhibitory_edges_defer_background_but_keep_foreground_open_across_restart(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0,
                             check_interval_seconds=0.01, max_background_pulses_per_hour=4)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        brake = page(client, "Inhibitory source", "A_LOCAL_HISTORY brake")
        target = page(client, "Inhibited local context", "B_LOCAL_HISTORY")
        relay = page(client, "Excitatory source", "A_LOCAL_HISTORY relay")
        for local in (brake, target, relay):
            eventually(lambda local=local: view(client, local), lambda v: assistant_count(v) == 1)
        connect(client, brake, target, weight=1, kind="inhibitory")
        connect(client, relay, target, weight=0.9)
        runtime = client.app.state.pages.runtime
        target_center = runtime.world.get_task_front(target).center_id
        for number in (2, 3):
            sent = client.post(f"/api/pages/{brake}/messages", json={"content": f"Inhibitory contribution {number}."})
            assert sent.status_code == 202
            eventually(lambda: view(client, brake), lambda v: assistant_count(v) == number)
        eventually(lambda: runtime.context_pulse.connections.inhibition_level(target_center), lambda level: level > 1.5)
        sent = client.post(f"/api/pages/{relay}/messages", json={"content": "Relay this settled result."})
        assert sent.status_code == 202
        deliveries = eventually(lambda: runtime.causal_ledger.list_events(kind="propagation", limit=100), bool)
        assert len(deliveries) == 1
        delivery_id = deliveries[0].id
        time.sleep(0.15)
        assert runtime.causal_ledger.get_event(delivery_id).status.value == "queued"
        assert assistant_count(view(client, target)) == 1
        assert len(fixture.requests) == 6
        sent = client.post(f"/api/pages/{target}/messages", json={"content": "Foreground request remains available."})
        assert sent.status_code == 202
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        assert runtime.causal_ledger.get_event(delivery_id).status.value == "queued"
        assert len(fixture.requests) == 7
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        time.sleep(0.2)
        runtime = client.app.state.pages.runtime
        assert runtime.context_pulse.connections.inhibition_level(target_center) > 1.0
        assert runtime.causal_ledger.get_event(delivery_id).status.value == "queued"
        assert client.get("/api/pulse").json()["budget"]["used"] == 1
        assert len(fixture.requests) == 7


def test_disabling_pulse_pauses_queued_delivery_and_reenable_resumes_it_once(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    paused_by_refractory = PulseSettings(enabled=True, spontaneous_rate=0,
                                        refractory_seconds=60, max_background_pulses_per_hour=1)
    with client_for(tmp_path, pulse=paused_by_refractory, model_factory=fixture.factory) as client:
        source = page(client, "Source context", "A_LOCAL_HISTORY")
        target = page(client, "Target context", "B_LOCAL_HISTORY")
        for local in (source, target):
            eventually(lambda local=local: view(client, local), lambda v: assistant_count(v) == 1)
        connect(client, source, target)
        assert client.post(f"/api/pages/{source}/messages", json={"content": "A_PAUSED_DELIVERY"}).status_code == 202
        runtime = client.app.state.pages.runtime
        queued = eventually(lambda: runtime.causal_ledger.list_events(kind="propagation", limit=100), bool)
        assert len(queued) == 1
        delivery_id = queued[0].id
        assert len(fixture.requests) == 3
    with client_for(tmp_path, pulse=PulseSettings(enabled=False, refractory_seconds=0),
                    model_factory=fixture.factory) as client:
        time.sleep(0.2)
        assert client.get("/api/pulse").json()["running"] is False
        assert client.app.state.pages.runtime.causal_ledger.get_event(delivery_id).status.value == "queued"
        assert len(fixture.requests) == 3
    with client_for(tmp_path, pulse=PulseSettings(enabled=True, spontaneous_rate=0,
                                                refractory_seconds=0, max_background_pulses_per_hour=1),
                    model_factory=fixture.factory) as client:
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        assert client.app.state.pages.runtime.causal_ledger.get_event(delivery_id).status.value == "settled"
        assert len(fixture.requests) == 4
        assert client.get("/api/pulse").json()["budget"]["used"] == 1


def test_disconnect_cancels_unstarted_delivery_and_reconnect_does_not_revive_it(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=60)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Source context", "A_LOCAL_HISTORY")
        target = page(client, "Target context", "B_LOCAL_HISTORY")
        for local in (source, target):
            eventually(lambda local=local: view(client, local), lambda v: assistant_count(v) == 1)
        original_edge = connect(client, source, target)["connection"]["id"]
        assert client.post(f"/api/pages/{source}/messages", json={"content": "A_REVOKED_DELIVERY"}).status_code == 202
        runtime = client.app.state.pages.runtime
        queued = eventually(lambda: runtime.causal_ledger.list_events(kind="propagation", limit=100), bool)
        delivery_id = queued[0].id
        removed = client.request("DELETE", "/api/pulse/connections",
                                 json={"sourcePageId": source, "targetPageId": target})
        assert removed.status_code == 200, removed.text
        assert removed.json()["removed"] is True
        eventually(lambda: runtime.causal_ledger.get_event(delivery_id), lambda row: row.status.value == "cancelled")
        replacement = connect(client, source, target)["connection"]["id"]
        assert replacement != original_edge
        assert len(fixture.requests) == 3
    with client_for(tmp_path, pulse=PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0),
                    model_factory=fixture.factory) as client:
        time.sleep(0.2)
        runtime = client.app.state.pages.runtime
        assert runtime.causal_ledger.get_event(delivery_id).status.value == "cancelled"
        assert assistant_count(view(client, target)) == 1
        assert len(fixture.requests) == 3
        assert client.post(f"/api/pages/{source}/messages", json={"content": "A_AFTER_RECONNECTION"}).status_code == 202
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        assert len(fixture.requests) == 5


def test_two_local_outputs_converge_once_with_both_sources_and_no_transcript_copy(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0,
                             max_background_pulses_per_hour=4, max_propagation_depth=1)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        first = page(client, "First source", "A_LOCAL_HISTORY")
        second = page(client, "Second source", "C_LOCAL_HISTORY")
        target = page(client, "Converging context", "B_LOCAL_HISTORY")
        for local in (first, second, target):
            eventually(lambda local=local: view(client, local), lambda v: assistant_count(v) == 1)
        connect(client, first, target)
        connect(client, second, target)
        # Hold the existing coordination lock while submitting both inputs.
        # This makes their source turns one deterministic scheduling cohort;
        # the real Dendrite, not this test, decides how their results converge.
        runtime = client.app.state.pages.runtime
        with runtime._tick_lock:
            assert client.post(f"/api/pages/{first}/messages", json={"content": "A_NEW_INPUT"}).status_code == 202
            assert client.post(f"/api/pages/{second}/messages", json={"content": "C_NEW_INPUT"}).status_code == 202
        final = eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        time.sleep(0.15)
        assert len(fixture.requests) == 6, "Two local results should share one receiving Kernel turn."
        incoming = runtime.causal_ledger.list_events(kind="propagation", limit=100)
        assert len(incoming) == 2
        assert {event.content for event in incoming} == {"A_FIXTURE_OUTPUT", "C_FIXTURE_OUTPUT"}
        convergence = [event for event in runtime.causal_ledger.list_events(kind="pulse", limit=100)
                       if event.metadata.get("context_pulse") == "convergence"]
        assert len(convergence) == 1
        fused = convergence[0]
        assert fused.status.value == "settled"
        assert fused.metadata["depth"] == 1
        integration = runtime.causal_ledger.get_dendritic_integration_for_event(fused.id)
        assert {member.event_id for member in integration.members} == {event.id for event in incoming}
        assert {event.engram_id for event in incoming + convergence} == {runtime.continuity_engram_id}
        target_request = fixture.requests[-1]["messages"]
        text = json.dumps(target_request)
        assert all(marker in text for marker in ("B_LOCAL_HISTORY", "A_FIXTURE_OUTPUT", "C_FIXTURE_OUTPUT"))
        assert all(marker not in text for marker in ("A_LOCAL_HISTORY", "C_LOCAL_HISTORY", "A_NEW_INPUT", "C_NEW_INPUT"))
        visible = [message for message in final["messages"] if message.get("activation") == "propagation"]
        assert len(visible) == 2
        assert {message["sourcePageId"] for message in visible} == {first, second}
        assert all(message["role"] == "system" for message in visible)
        assert fused.id not in {message["id"] for message in final["messages"]}
        assert client.get("/api/pulse").json()["budget"]["used"] == 2
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        time.sleep(0.2)
        assert assistant_count(view(client, target)) == 2
        assert len(fixture.requests) == 6
        assert client.get("/api/pulse").json()["budget"]["used"] == 2


def test_native_background_model_round_ceiling_is_causally_scoped(tmp_path, monkeypatch):
    """Use actual World/Kernel scopes to distinguish a user input from propagation."""
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        refractory_seconds=0, max_propagation_depth=1,
        max_background_pulses_per_hour=1,
        max_background_main_model_rounds_per_turn=2,
    )
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Source", "A_LOCAL_HISTORY")
        target = page(client, "Receiver", "B_LOCAL_HISTORY")
        eventually(lambda: view(client, source), lambda value: assistant_count(value) == 1)
        eventually(lambda: view(client, target), lambda value: assistant_count(value) == 1)
        connect(client, source, target, weight=0.9)
        sent = client.post(f"/api/pages/{source}/messages", json={"content": "Human input"})
        assert sent.status_code == 202
        eventually(lambda: view(client, target), lambda value: assistant_count(value) == 2)
        runtime = client.app.state.pages.runtime
        assert client.get("/api/pulse").json()["backgroundMainModelRoundLimit"] == 2
        # The provider factory receives a *session* scope (no turn_id). Each
        # actual model round belongs to a durable Kernel turn; inspect those
        # scopes instead of mistaking the factory's session for a World turn.
        sessions = {scope.session_id: scope.session_scope() for scope in fixture.scopes}
        bound_turns = []
        for session_scope in sessions.values():
            kernel = runtime.kernel_service._runtime(session_scope)
            bound_turns.extend(
                (kernel, record.scope)
                for record in kernel.store.turns_for_session(session_scope, limit=16)
            )
        assert len(bound_turns) == len(fixture.requests) == 4
        foreground, background = [], []
        for kernel, scope in bound_turns:
            assert scope.turn_id
            world_turn = runtime.causal_ledger.get_turn_for_world(scope.turn_id, runtime.world_id)
            assert world_turn is not None
            event = runtime.causal_ledger.get_event(world_turn.event_id)
            assert event is not None
            cap = kernel.runner.tool_provider.model_round_limit_for_scope(scope, 16)
            if event.kind.value == "propagation":
                assert event.source.value == "propagation"
                background.append(cap)
            else:
                foreground.append(cap)
        assert background == [2]
        assert sorted(foreground) == [16, 16, 16]

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        assert client.get("/api/pulse").json()["backgroundMainModelRoundLimit"] == 2
        assert len(fixture.requests) == 4


@pytest.mark.parametrize("invalid", [0, -1, 17, True, 2.5, None])
def test_native_background_model_round_limit_invalid(invalid):
    with pytest.raises(ValueError, match="max_background_main_model_rounds_per_turn"):
        PulseSettings(max_background_main_model_rounds_per_turn=invalid)
