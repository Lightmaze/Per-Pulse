"""Pausing local execution retains completed influence in the same body.

Real SQLite and accepted causal turns; all content is synthetic, with no model
or provider calls. Copyright 2026 Lightmaze Contributors. Apache-2.0.
"""
from datetime import datetime, timedelta, timezone

import pytest

from pulse_system.core.causality import CausalTransitionError
from pulse_system.core.pulse.engine import PulseEvent
from pulse_system.core.types import CausalEventStatus
from test_context_recovery import coordinator, result_for, propagated, commit_successor


@pytest.mark.parametrize("paused", ["source", "target", "both"])
def test_pause_retains_influence_without_allowing_paused_execution(coordinator, paused):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    edge = controller.connect(source, target)
    result = result_for(ledger, source)
    for center, role in ((source, "source"), (target, "target")):
        if paused in {role, "both"}:
            world.update_activity_center(center, status="paused")
    controller.tick()
    deliveries = propagated(ledger)
    assert len(deliveries) == 1, "A local pause must not discard another completed result."
    delivery = deliveries[0]
    assert delivery.parent_event_id == result.id
    assert delivery.metadata["context_connection_id"] == edge.id
    assert controller.snapshot()["cursor"] == result.seq
    assert controller.snapshot()["budget"]["used"] == 1
    assert controller.snapshot()["queue"]["queued"] == 1
    if paused in {"target", "both"}:
        assert controller.snapshot()["recentActivations"][0]["deferredReason"] == "context_paused"
        assert not controller.admits(PulseEvent.from_causal_event(delivery), datetime.now(timezone.utc))
        with pytest.raises(CausalTransitionError, match="active subject-owned context"):
            ledger.begin_turn(delivery.id, "subject", delivery.content, execution_context=target)
        assert ledger.get_event(delivery.id).attempts == 0
    # A new coordinator must find the same delivery and keep its one charge.
    restored = build()
    restored.tick()
    assert [event.id for event in propagated(ledger)] == [delivery.id]
    world.update_activity_center(target, status="active")
    assert restored.admits(PulseEvent.from_causal_event(delivery), datetime.now(timezone.utc))
    turn = ledger.begin_turn(delivery.id, "subject", delivery.content, execution_context=target)
    ledger.settle_turn(turn.id, "Synthetic retained evidence was received.")
    assert ledger.get_event(delivery.id).status is CausalEventStatus.SETTLED
    assert restored.snapshot()["budget"]["used"] == 1
    assert restored.snapshot()["queue"] == {"queued": 0, "running": 0, "oldestQueuedAt": None}


def test_paused_influence_is_revoked_before_resume_and_never_replayed(coordinator):
    _storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connect(source, target)
    world.update_activity_center(target, status="paused")
    result_for(ledger, source)
    controller.tick()
    assert len(propagated(ledger)) == 1
    delivery = propagated(ledger)[0]
    restored = build()
    assert restored.disconnect(source, target)
    assert ledger.get_event(delivery.id).status is CausalEventStatus.CANCELLED
    restored.connect(source, target)
    world.update_activity_center(target, status="active")
    restored.tick()
    assert [event.id for event in propagated(ledger)] == [delivery.id]
    assert restored.snapshot()["budget"]["used"] == 1


def test_paused_inhibitory_target_keeps_original_impulse_time(coordinator):
    _storage, ledger, world, (source, target, _), build = coordinator
    controller = build(max_background_pulses_per_hour=0)
    controller.connect(source, target, weight=0.8, kind="inhibitory")
    world.update_activity_center(target, status="paused")
    result = result_for(ledger, source)
    observed = result.settled_at + timedelta(seconds=60)
    controller.tick(now=observed)
    level = controller.connections.inhibition_level(target, now=observed)
    assert 0.29 < level < 0.30, "A minute-old impulse must already have decayed."
    restored = build(max_background_pulses_per_hour=0)
    restored.tick(now=observed)
    assert restored.connections.inhibition_level(target, now=observed) == level
    assert propagated(ledger) == []


def test_paused_delivery_keeps_committed_succession_and_original_source(coordinator):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connect(source, target)
    result = result_for(ledger, source)
    world.update_activity_center(target, status="paused")
    commit_successor(storage, ledger, world)
    successor = build(subject_id="successor")
    successor.tick()
    assert len(propagated(ledger)) == 1
    delivery = propagated(ledger)[0]
    assert delivery.engram_id == "successor"
    assert delivery.parent_event_id == result.id
    assert delivery.metadata["source_engram_id"] == "subject"
    world.update_activity_center(target, status="active")
    assert successor.admits(PulseEvent.from_causal_event(delivery), datetime.now(timezone.utc))
    turn = ledger.begin_turn(delivery.id, "successor", delivery.content, execution_context=target)
    assert turn.event_id == delivery.id


@pytest.mark.parametrize("state", ["archived", "completed", "dormant"])
def test_pause_retention_does_not_reopen_other_inactive_states(coordinator, state):
    _storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connect(source, target)
    world.update_activity_center(target, status=state)
    result_for(ledger, source)
    controller.tick()
    assert propagated(ledger) == []


def test_queue_summary_counts_beyond_recent_sample_and_does_not_mutate(coordinator):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build(max_background_pulses_per_hour=40)
    controller.connect(source, target)
    world.update_activity_center(target, status="paused")
    for _ in range(27):
        result_for(ledger, source)
    controller.tick()
    deliveries = propagated(ledger)
    before = storage._conn.total_changes
    snapshot = controller.snapshot()
    assert storage._conn.total_changes == before
    assert snapshot["queue"] == {
        "queued": 27, "running": 0, "oldestQueuedAt": deliveries[0].created_at.isoformat(),
    }
    assert len(snapshot["recentActivations"]) == 20
    assert {item["deferredReason"] for item in snapshot["recentActivations"]} == {"context_paused"}
    assert snapshot["budget"]["used"] == 27


def test_deferral_explanation_uses_the_execution_gate(coordinator):
    _storage, ledger, _world, (source, target, _), build = coordinator
    controller = build()
    controller.connect(source, target)
    result_for(ledger, source)
    controller.tick()
    delivery = propagated(ledger)[0]
    event = PulseEvent.from_causal_event(delivery)
    now = datetime.now(timezone.utc)
    assert controller.deferred_reason(event, now) is None
    assert build(enabled=False).deferred_reason(event, now) == "profile_paused"
    controller.connections.apply_inhibition(target, 2, now=now)
    assert controller.deferred_reason(event, now) == "inhibited"
    assert not controller.admits(event, now)
    controller.connections.disconnect(source, target)
    assert controller.deferred_reason(event, now) == "connection_revoked"


def test_paused_label_does_not_hide_changed_subject_ownership(coordinator):
    storage, ledger, world, (source, target, _), build = coordinator
    controller = build()
    controller.connect(source, target)
    result_for(ledger, source)
    controller.tick()
    delivery = propagated(ledger)[0]
    world.update_activity_center(target, status="paused")
    # An uncommitted ownership change cannot authorize this subject to keep
    # using the context. Merely resuming it would not restore that authority.
    storage.create_engram(engram_id="another-subject")
    world.update_focal_succession("subject", "another-subject")
    event = PulseEvent.from_causal_event(delivery)
    assert controller.deferred_reason(event, datetime.now(timezone.utc)) == "context_inactive"
    world.update_activity_center(target, status="active")
    assert not controller.admits(event, datetime.now(timezone.utc))
    with pytest.raises(CausalTransitionError, match="active subject-owned context"):
        ledger.begin_turn(delivery.id, "subject", delivery.content, execution_context=target)
