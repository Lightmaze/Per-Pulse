"""Paused influence survives the real HTTP adapter and Kernel lifecycle.

The model transport is an explicit synthetic fixture. These checks exercise
native persistence and provider requests, not external model usefulness.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

from per_pulse.service import PulseSettings
from test_pulse_integration import (
    TransportFixture,
    assistant_count,
    client_for,
    connect,
    eventually,
    page,
    view,
)


def test_paused_influence_survives_restart_then_executes_once_in_original_history(
    tmp_path, monkeypatch,
):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        refractory_seconds=0, max_propagation_depth=1,
        max_background_pulses_per_hour=1,
    )

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Evidence source", "A_LOCAL_HISTORY")
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 1)
        target = page(client, "Paused synthesis", "B_LOCAL_HISTORY")
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 1)
        assert len(fixture.requests) == 2
        original_target_session = fixture.scopes[-1].session_id

        edge_id = connect(client, source, target, weight=0.9)["connection"]["id"]
        runtime = client.app.state.pages.runtime
        subject_id = runtime.continuity_engram_id
        target_center = runtime.world.get_task_front(target).center_id
        runtime.world.update_activity_center(target_center, status="paused")
        sent = client.post(f"/api/pages/{source}/messages", json={
            "content": "A_NEW_INPUT: preserve this finding while synthesis is paused.",
        })
        assert sent.status_code == 202, sent.text
        input_id = sent.json()["messageId"]
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 2)

        state = eventually(
            lambda: client.get("/api/pulse").json(),
            lambda value: any(
                item["pageId"] == target
                and item["status"] == "queued"
                and item["deferredReason"] == "context_paused"
                for item in value["recentActivations"]
            ),
        )
        activation = next(item for item in state["recentActivations"] if item["pageId"] == target)
        delivery_id = activation["eventId"]
        delivery = runtime.causal_ledger.get_event(delivery_id)
        source_result_id = delivery.parent_event_id
        source_result = runtime.causal_ledger.get_event(source_result_id)
        assert source_result.parent_event_id == input_id
        assert source_result.status.value == "settled"
        assert delivery.content == source_result.content == "A_FIXTURE_OUTPUT"
        assert delivery.metadata["context_connection_id"] == edge_id
        assert activation["sourceEventId"] == source_result_id
        assert activation["sourcePageId"] == source
        assert activation["sourceCenterId"] == runtime.world.get_task_front(source).center_id
        assert state["queue"]["queued"] == 1
        assert state["queue"]["running"] == 0
        assert state["budget"]["used"] == 1
        assert state["budget"]["remaining"] == 0

        # Allow more native coordinator passes to prove that a retained input
        # does not start a request while its local context remains paused.
        observed_tick = runtime.tick_count
        eventually(lambda: runtime.tick_count, lambda tick: tick >= observed_tick + 3)
        assert len(fixture.requests) == 3
        assert assistant_count(view(client, target)) == 1
        assert runtime.causal_ledger.get_event(delivery_id).status.value == "queued"

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        runtime = client.app.state.pages.runtime
        assert runtime.continuity_engram_id == subject_id
        assert runtime.world.get_task_front(target).center_id == target_center
        assert runtime.world.get_activity_center(target_center).status.value == "paused"
        observed_tick = runtime.tick_count
        eventually(lambda: runtime.tick_count, lambda tick: tick >= observed_tick + 3)
        assert len(fixture.requests) == 3
        assert assistant_count(view(client, target)) == 1

        state = client.get("/api/pulse").json()
        activation = next(item for item in state["recentActivations"] if item["eventId"] == delivery_id)
        assert activation["status"] == "queued"
        assert activation["deferredReason"] == "context_paused"
        assert state["budget"]["used"] == 1
        assert state["budget"]["remaining"] == 0

        runtime.world.update_activity_center(target_center, status="active")
        final = eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        observed_tick = runtime.tick_count
        eventually(lambda: runtime.tick_count, lambda tick: tick >= observed_tick + 3)
        assert len(fixture.requests) == 4
        assert fixture.scopes[-1].session_id == original_target_session
        assert {scope.engram_id for scope in fixture.scopes} == {subject_id}

        target_history = json.dumps(fixture.requests[-1]["messages"])
        assert all(marker in target_history for marker in (
            "B_LOCAL_HISTORY", "B_FIXTURE_OUTPUT", "A_FIXTURE_OUTPUT",
        ))
        assert "A_LOCAL_HISTORY" not in target_history
        assert "A_NEW_INPUT" not in target_history
        delivery = runtime.causal_ledger.get_event(delivery_id)
        assert delivery.status.value == "settled"
        assert delivery.attempts == 1
        assert delivery.parent_event_id == source_result_id
        assert delivery.metadata["context_connection_id"] == edge_id
        assert runtime.causal_ledger.get_event(source_result_id).parent_event_id == input_id
        assert [event.id for event in runtime.causal_ledger.list_events(
            world_id=runtime.world_id, kind="propagation", limit=100,
        )] == [delivery_id]

        incoming = next(message for message in final["messages"] if message["id"] == delivery_id)
        assert incoming["role"] == "system"
        assert incoming["activation"] == "propagation"
        assert incoming["sourcePageId"] == source
        assert incoming["sourceMessageId"] == source_result_id
        state = client.get("/api/pulse").json()
        assert state["budget"]["used"] == 1
        assert state["budget"]["remaining"] == 0
        assert state["queue"]["queued"] == 0
        assert state["queue"]["running"] == 0
        activation = next(item for item in state["recentActivations"] if item["eventId"] == delivery_id)
        assert activation["status"] == "settled"
        assert activation["deferredReason"] is None


def test_pulse_status_preserves_missing_work_identity_instead_of_faking_home(
    tmp_path, monkeypatch,
):
    """An unresolved Work projection remains an unresolved Work, not Home.

    The actual causal center ID stays in the response, even if a front is
    temporarily absent from the current page list during a future UI merge.
    The only center mapped to Home is the native null center.
    """
    with client_for(tmp_path, pulse=PulseSettings(enabled=False), model_factory=None) as client:
        source = page(client, "Earlier evidence", "Source history")
        target = page(client, "Current synthesis", "Target history")
        connect(client, source, target)
        connect(client, "home", target)

        runtime = client.app.state.pages.runtime
        source_center = runtime.world.get_task_front(source).center_id
        target_center = runtime.world.get_task_front(target).center_id
        fronts = runtime.list_task_fronts()
        assert {front["id"] for front in fronts} >= {source, target}

        # Simulate the target UI having only a partial Work listing. Pulse's
        # graph is still authoritative and must retain the original centers.
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "list_task_fronts", lambda: [
                front for front in fronts if front["id"] != source
            ])
            response = client.get("/api/pulse")
        assert response.status_code == 200, response.text
        connections = response.json()["connections"]
        unresolved = next(edge for edge in connections
                          if edge["source_center_id"] == source_center)
        assert unresolved["sourcePageId"] is None
        assert unresolved["target_center_id"] == target_center
        assert unresolved["targetPageId"] == target
        actual_home = next(edge for edge in connections
                           if edge["source_center_id"] is None)
        assert actual_home["sourcePageId"] == "home"
        assert actual_home["targetPageId"] == target



def test_activity_ui_uses_native_connection_and_status_contract(tmp_path):
    """The UI's connection controls act on the existing durable Pulse graph.

    Reading remains useful without a provider or an enabled background loop:
    the interface never invents continuing turns to fill an empty activity list.
    """
    with client_for(tmp_path, pulse=PulseSettings(enabled=False), model_factory=None) as client:
        source = page(client, "Evidence", "READ_EVIDENCE")
        target = page(client, "Synthesis", "WRITE_SYNTHESIS")
        before = client.get("/api/pulse")
        assert before.status_code == 200
        assert before.json()["enabled"] is False
        assert before.json()["providerConfigured"] is False
        assert before.json()["queue"]["queued"] == 0
        assert before.json()["recentActivations"] == []

        create = client.post("/api/pulse/connections", json={
            "sourcePageId": source, "targetPageId": target,
            "kind": "inhibitory", "weight": 0.6,
        })
        assert create.status_code == 201, create.text
        edge = create.json()["connection"]
        assert edge["sourcePageId"] == source
        assert edge["targetPageId"] == target
        assert edge["kind"] == "inhibitory"
        assert edge["weight"] == 0.6
        snapshot = create.json()["pulse"]
        assert snapshot["running"] is False
        assert len(snapshot["connections"]) == 1
        assert snapshot["connections"][0]["id"] == edge["id"]
        assert snapshot["connections"][0]["sourcePageId"] == source
        assert snapshot["connections"][0]["targetPageId"] == target

        # A second read does not create activity or change the edge identity.
        status = client.get("/api/pulse").json()
        assert status["connections"] == snapshot["connections"]
        assert status["recentActivations"] == []

        remove = client.request("DELETE", "/api/pulse/connections", json={
            "sourcePageId": source, "targetPageId": target,
        })
        assert remove.status_code == 200, remove.text
        assert remove.json()["removed"] is True
        assert remove.json()["pulse"]["connections"] == []
        assert client.get("/api/pulse").json()["connections"] == []

        invalid = client.post("/api/pulse/connections", json={
            "sourcePageId": source, "targetPageId": "missing-work",
            "kind": "excitatory", "weight": 0.6,
        })
        assert invalid.status_code == 404
        assert client.get("/api/pulse").json()["connections"] == []


def test_operator_pause_keeps_foreground_alive_and_delivers_once_after_restart(
    tmp_path, monkeypatch,
):
    """An operator hold gates only native background work in the same World."""
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        refractory_seconds=0, max_propagation_depth=1,
        max_background_pulses_per_hour=1,
    )
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Live evidence", "A_LOCAL_HISTORY")
        target = page(client, "Future synthesis", "B_LOCAL_HISTORY")
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 1)
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 1)
        connect(client, source, target, weight=0.9)
        hold = client.patch("/api/pulse", json={"paused": True})
        assert hold.status_code == 200, hold.text
        assert hold.json()["pulse"]["paused"] is True
        assert hold.json()["pulse"]["running"] is False

        sent = client.post(f"/api/pages/{source}/messages", json={"content": "A_NEW_INPUT"})
        assert sent.status_code == 202
        sent_id = sent.json()["messageId"]
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 2)
        runtime = client.app.state.pages.runtime
        before = runtime.tick_count
        eventually(lambda: runtime.tick_count, lambda tick: tick >= before + 3)
        assert assistant_count(view(client, target)) == 1
        assert len(fixture.requests) == 3, "Foreground work must run while paused."
        status = client.get("/api/pulse").json()
        assert status["paused"] is True
        assert status["budget"]["used"] == 0
        assert status["queue"]["running"] == 0

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        assert client.get("/api/pulse").json()["paused"] is True
        runtime = client.app.state.pages.runtime
        before = runtime.tick_count
        eventually(lambda: runtime.tick_count, lambda tick: tick >= before + 3)
        assert assistant_count(view(client, target)) == 1
        assert len(fixture.requests) == 3
        release = client.patch("/api/pulse", json={"paused": False})
        assert release.status_code == 200, release.text
        assert release.json()["pulse"]["paused"] is False
        final = eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        assert len(fixture.requests) == 4
        propagation = runtime.causal_ledger.list_events(
            world_id=runtime.world_id, kind="propagation", limit=100,
        )
        assert len(propagation) == 1
        source_result = runtime.causal_ledger.get_event(propagation[0].parent_event_id)
        assert source_result.parent_event_id == sent_id
        assert source_result.content == "A_FIXTURE_OUTPUT"
        assert propagation[0].content == source_result.content
        assert client.get("/api/pulse").json()["budget"]["used"] == 1
        incoming = next(m for m in final["messages"] if m["id"] == propagation[0].id)
        assert incoming["sourcePageId"] == source
        assert incoming["sourceMessageId"] == source_result.id

    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        assert client.get("/api/pulse").json()["paused"] is False
        assert len(fixture.requests) == 4
        assert client.get("/api/pulse").json()["budget"]["used"] == 1


def test_operator_pause_rejects_disabled_profile_and_nonboolean_value(tmp_path):
    with client_for(tmp_path, pulse=PulseSettings(enabled=False), model_factory=None) as client:
        status = client.get("/api/pulse").json()
        assert status["paused"] is False
        assert client.patch("/api/pulse", json={"paused": True}).status_code == 409
        assert client.patch("/api/pulse", json={"paused": "true"}).status_code == 422
        assert client.get("/api/pulse").json()["paused"] is False
