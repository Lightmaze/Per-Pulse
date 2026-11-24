"""Fault and lifecycle checks for the native local-context coordinator.

Uses real SQLite, causal turns, lease fencing, and synthetic content. It does
not replace the model path or contact a provider.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest

from pulse_system.core.causality.ledger import CausalLedger, RuntimeFence
from pulse_system.core.pulse.context import ContextPulseConfig, ContextPulseController
from pulse_system.core.pulse.engine import PulseEvent
from pulse_system.core.types import CausalEventDomain, CausalEventKind, CausalEventSource
from pulse_system.core.world.registry import WorldRegistry
from pulse_system.substrate.storage.store import Storage


@pytest.fixture
def coordinator(tmp_path):
    storage = Storage(tmp_path / "world.db")
    storage.save_component_state("pulse.world.v1", {
        "version": 1, "world_id": "context-world", "continuity_engram_id": "subject",
    })
    storage.create_engram(engram_id="subject")
    world = WorldRegistry(storage)
    centers = [world.create_task_bundle_for_existing_engram(title, "subject").center.id
               for title in ("Source", "Target", "Neighbor")]
    lease = storage.acquire_runtime_lease("context-test", now=datetime.now(timezone.utc), ttl_sec=600)
    ledger = CausalLedger(storage)

    def build(*, subject_id="subject", **options):
        config = {"enabled": True, "spontaneous_rate": 0, "refractory_seconds": 0,
                  "check_interval_seconds": 0.01, **options}
        return ContextPulseController(
            storage, ledger, world, world_id="context-world", subject=lambda: subject_id,
            fence=lambda: RuntimeFence(lease.owner_id, lease.epoch),
            config=ContextPulseConfig(**config),
        )

    yield storage, ledger, world, centers, build
    storage.close()


def result_for(ledger, center_id, *, execution_regime="ordinary"):
    event = ledger.enqueue(
        "context-world", domain=CausalEventDomain.PULSE, kind=CausalEventKind.STIMULUS,
        source=CausalEventSource.USER, content="Explore the synthetic evidence.",
        engram_id="subject", center_id=center_id,
    )
    turn = ledger.begin_turn(event.id, "subject", event.content,
                             execution_context=center_id or "home")
    _, result = ledger.settle_turn(
        turn.id, "A synthetic result to carry forward.",
        metadata={"execution_regime": execution_regime},
    )
    return result


def propagated(ledger):
    return ledger.list_events(world_id="context-world", kind=CausalEventKind.PROPAGATION, limit=100)


def test_crash_after_delivery_before_checkpoint_does_not_repeat_or_refill_quota(coordinator, monkeypatch):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connections.connect(source, target)
    result = result_for(ledger, source)

    def fail_checkpoint(_now):
        raise OSError("Synthetic checkpoint failure")

    monkeypatch.setattr(controller, "_save", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint failure"):
        controller.tick()
    assert len(propagated(ledger)) == 1
    delivered_id = propagated(ledger)[0].id
    restarted = build()
    restarted.tick()
    assert [item.id for item in propagated(ledger)] == [delivered_id]
    assert restarted.snapshot()["cursor"] == result.seq
    assert restarted.snapshot()["budget"]["used"] == 1


def test_semantic_link_edit_during_partial_fanout_does_not_replay_or_stall(coordinator):
    storage, ledger, world, (source, target, neighbor), build = coordinator
    controller = build(max_background_pulses_per_hour=1)
    original = controller.connections.connect(source, target, weight=0.6)
    controller.connections.connect(source, neighbor, weight=0.7)
    result = result_for(ledger, source)
    controller.tick()
    assert controller.snapshot()["cursor"] < result.seq
    assert len(propagated(ledger)) == 1
    first_delivery = propagated(ledger)[0]
    revised = controller.connections.connect(source, target, weight=0.9)
    assert revised.id != original.id
    restarted = build(max_background_pulses_per_hour=2)
    restarted.tick()
    deliveries = propagated(ledger)
    assert len(deliveries) == 2
    assert {item.center_id for item in deliveries} == {target, neighbor}
    assert ledger.get_event(first_delivery.id).metadata["priority"] == 0.6
    assert restarted.snapshot()["cursor"] == result.seq


def test_paused_and_archived_targets_cannot_admit_background_delivery(coordinator):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connections.connect(source, target)
    result_for(ledger, source)
    controller.tick()
    delivery = PulseEvent.from_causal_event(propagated(ledger)[0])
    now = datetime.now(timezone.utc)
    assert controller.admits(delivery, now)
    world.update_activity_center(target, status="paused")
    assert not controller.admits(delivery, now)
    world.update_activity_center(target, status="active")
    assert controller.admits(delivery, now)
    world.update_activity_center(target, status="archived")
    assert not controller.admits(delivery, now)


def test_replay_result_never_stalls_diffusion_or_seeds_fresh_reentry(coordinator):
    storage, ledger, world, (source, target, neighbor), build = coordinator
    controller = build()
    controller.connections.connect(source, target)
    replay = result_for(ledger, source, execution_regime="replay")
    ordinary = result_for(ledger, neighbor)
    controller.tick()
    assert propagated(ledger) == []
    snapshot = controller.snapshot()
    assert snapshot["cursor"] == ordinary.seq > replay.seq
    assert source not in {entry["center_id"] for entry in snapshot["contexts"]}
    assert neighbor in {entry["center_id"] for entry in snapshot["contexts"]}


def test_disconnect_prevents_queued_connection_from_admission(coordinator):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connections.connect(source, target)
    result_for(ledger, source)
    controller.tick()
    delivery = PulseEvent.from_causal_event(propagated(ledger)[0])
    controller.connections.disconnect(source, target)
    assert not controller.admits(delivery, datetime.now(timezone.utc))


def test_exhausted_content_quota_does_not_suppress_inhibitory_effect(coordinator):
    storage, ledger, world, (source, target, neighbor), build = coordinator
    controller = build(max_background_pulses_per_hour=0)
    controller.connections.connect(source, target)
    controller.connections.connect(source, neighbor, weight=0.8, kind="inhibitory")
    result_for(ledger, source)
    controller.tick()
    assert propagated(ledger) == []
    assert controller.connections.inhibition_level(neighbor) > 0.79


def test_result_settled_after_projection_still_enforces_local_refractory(coordinator):
    _storage, ledger, _world, (source, target, _), build = coordinator
    controller = build(refractory_seconds=60)
    controller.connect(source, target)
    result_for(ledger, source)
    controller.tick()
    pending = PulseEvent.from_causal_event(propagated(ledger)[0])
    assert controller.admits(pending, datetime.now(timezone.utc))
    # Simulate the worker settling after the controller's observation pass.
    result_for(ledger, target)
    assert target not in {item["center_id"] for item in controller.snapshot()["contexts"]}
    assert not controller.admits(pending, datetime.now(timezone.utc))


def test_participant_membership_does_not_authorize_another_focal_context(coordinator):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connections.connect(source, target)
    result_for(ledger, source)
    controller.tick()
    delivery = PulseEvent.from_causal_event(propagated(ledger)[0])
    storage.create_engram(engram_id="other-focus")
    world.update_focal_succession("subject", "other-focus")
    world.add_membership(target, "subject", relation="participant")
    assert not controller.admits(delivery, datetime.now(timezone.utc))


def commit_successor(storage, ledger, world):
    generation = ledger.begin_generation("subject", world_id="context-world")
    ledger.transition_generation(generation.id, "summarizing")
    storage.create_provisional_engram("successor", None, [], token_count=0)
    ledger.transition_generation(generation.id, "rotating", successor_id="successor")
    world.update_focal_succession("subject", "successor")
    ledger.commit_succession_publication(
        generation.id, "subject", "successor", summary_turn_id=None, runtime_fence=None,
    )
    storage.save_component_state("pulse.world.v1", {
        "version": 1, "world_id": "context-world", "continuity_engram_id": "successor",
    })


def test_partial_fanout_resume_after_succession_preserves_settled_predecessor_delivery(coordinator):
    storage, ledger, world, (source, target, neighbor), build = coordinator
    controller = build(max_background_pulses_per_hour=1)
    edge = controller.connections.connect(source, target)
    controller.connections.connect(source, neighbor)
    source_result = result_for(ledger, source)
    controller.tick()
    delivered = propagated(ledger)[0]
    turn = ledger.begin_turn(delivered.id, "subject", delivered.content, execution_context=target)
    _, target_result = ledger.settle_turn(turn.id, "The predecessor used the transferred result.")
    commit_successor(storage, ledger, world)
    resumed = build(subject_id="successor", max_background_pulses_per_hour=2)
    resumed.tick()
    assert len(propagated(ledger)) == 2
    assert ledger.get_event(delivered.id).engram_id == "subject"
    assert ledger.get_event(delivered.id).status.value == "settled"
    assert ledger.get_event(target_result.id).engram_id == "subject"
    remaining = next(item for item in propagated(ledger) if item.center_id == neighbor)
    assert remaining.engram_id == "successor"
    assert remaining.parent_event_id == source_result.id
    assert resumed.connections.outgoing(source)[0].id == edge.id
    assert resumed.snapshot()["cursor"] >= source_result.seq


def test_local_numeric_memory_survives_committed_subject_succession(coordinator):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    edge = controller.connections.connect(source, target, kind="inhibitory", weight=0.8)
    source_result = result_for(ledger, source)
    controller.tick()
    before = controller.connections.inhibition_level(target)
    commit_successor(storage, ledger, world)
    resumed = build(subject_id="successor")
    resumed.tick()
    assert resumed.connections.outgoing(source)[0].id == edge.id
    assert 0.7 < resumed.connections.inhibition_level(target) <= before
    assert resumed.snapshot()["contexts"][0]["result_event_id"] == source_result.id


def test_late_projection_after_downtime_does_not_refresh_old_inhibition(coordinator, monkeypatch):
    import pulse_system.core.causality.ledger as ledger_module

    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connections.connect(source, target, kind="inhibitory", weight=0.8)
    before_downtime = datetime.now(timezone.utc) - timedelta(hours=1)
    with monkeypatch.context() as clocks:
        clocks.setattr(ledger_module, "_now", lambda: before_downtime)
        result_for(ledger, source)
    # The result was durably settled, but its sideband had not yet observed
    # it when the process stopped. Recovery must use the original activity
    # time rather than create a fresh inhibitory impulse an hour later.
    resumed = build()
    resumed.tick()
    assert resumed.connections.inhibition_level(target) == 0


@pytest.mark.parametrize("corruption", [
    "negative_observed_cursor", "boolean_version", "future_cursor", "cursor_order",
    "naive_check_time", "wrong_context_key", "wrong_result", "changed_activity_time",
])
def test_invalid_projection_checkpoint_is_rejected_before_skipping_history(coordinator, corruption):
    storage, ledger, world, (source, _, _), build = coordinator
    controller = build()
    result_for(ledger, source)
    controller.tick()
    state = deepcopy(storage.load_component_state(controller.key))
    context_key = controller._key(source)
    if corruption == "negative_observed_cursor":
        state["observed_cursor"] = -1
    elif corruption == "boolean_version":
        state["version"] = True
    elif corruption == "future_cursor":
        state["cursor"] = state["observed_cursor"] = 10**9
    elif corruption == "cursor_order":
        state["observed_cursor"] = 0
    elif corruption == "naive_check_time":
        state["last_check_at"] = "2026-10-23T10:00:00"
    elif corruption == "wrong_context_key":
        state["contexts"]["home"] = state["contexts"].pop(context_key)
    elif corruption == "wrong_result":
        state["contexts"][context_key]["result_event_id"] = "missing-result"
    elif corruption == "changed_activity_time":
        state["contexts"][context_key]["settled_at"] = "2099-01-01T00:00:00+00:00"
    storage.save_component_state(controller.key, state)
    with pytest.raises(ValueError, match="checkpoint"):
        build()
    # Rejection does not overwrite the original artifact or invent progress.
    assert storage.load_component_state(controller.key) == state


def test_backward_check_clock_reanchors_without_fresh_restart_opportunity(coordinator, monkeypatch):
    import pulse_system.core.pulse.context as context_module

    storage, ledger, _world, (source, _, _), build = coordinator
    controller = build()
    result_for(ledger, source)
    controller.tick()
    state = deepcopy(storage.load_component_state(controller.key))
    recovered_at = datetime.now(timezone.utc)
    # The saved check was written before a wall-clock correction. Its source
    # result time stays untouched; only the sampling schedule can be rebased.
    state["last_check_at"] = (recovered_at + timedelta(hours=1)).isoformat()
    storage.save_component_state(controller.key, state)
    monkeypatch.setattr(context_module.random, "random", lambda: 0.0)
    resumed = build(spontaneous_rate=1, check_interval_seconds=10)
    resumed.tick(now=recovered_at)
    assert storage.load_component_state(controller.key)["last_check_at"] == recovered_at.isoformat()
    assert ledger.list_events(kind=CausalEventKind.SPONTANEOUS) == []

    restarted_again = build(spontaneous_rate=1, check_interval_seconds=10)
    restarted_again.tick(now=recovered_at + timedelta(seconds=9))
    assert ledger.list_events(kind=CausalEventKind.SPONTANEOUS) == []
    restarted_again.tick(now=recovered_at + timedelta(seconds=10))
    assert len(ledger.list_events(kind=CausalEventKind.SPONTANEOUS)) == 1
    assert storage.load_component_state(controller.key)["contexts"] == state["contexts"]


def test_legacy_checkpoint_cursor_restores_without_replaying_delivery(coordinator):
    storage, ledger, _world, (source, target, _), build = coordinator
    controller = build()
    controller.connect(source, target)
    result_for(ledger, source)
    controller.tick()
    delivered = propagated(ledger)[0]
    state = deepcopy(storage.load_component_state(controller.key))
    del state["observed_cursor"]
    storage.save_component_state(controller.key, state)
    resumed = build()
    resumed.tick()
    assert [event.id for event in propagated(ledger)] == [delivered.id]
    assert resumed.snapshot()["observedCursor"] == state["cursor"]
