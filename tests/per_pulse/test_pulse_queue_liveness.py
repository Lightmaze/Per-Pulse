"""Bounded queue discovery must keep foreground and sibling contexts live.

The large backlogs are explicit synthetic causal fixtures, never model replies.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from datetime import datetime, timedelta, timezone

import pytest

from pulse_system.core.causality import CausalLedger
from pulse_system.core.connection.network import ConnectionNetwork
from pulse_system.core.dendrite.processor import DendriteConfig, DendriteProcessor
from pulse_system.core.pulse.engine import PulseEngine, PulseEngineConfig, PulseReason
from pulse_system.core.runtime.resource import RuntimeConfig, RuntimeManager
from pulse_system.core.types import (
    CausalEvent, CausalEventDomain, CausalEventKind, CausalEventSource,
    CausalEventStatus,
)
from pulse_system.core.world.registry import WorldRegistry
from pulse_system.substrate.storage import Storage


def make_engine(storage, ledger, *, admission_filter=None, silence=1, max_wait=2):
    manager = object()  # Queue discovery and scheduling only: no model execution.
    return PulseEngine(
        storage, manager, ConnectionNetwork(storage),
        DendriteProcessor(manager, DendriteConfig(
            silence_threshold=silence, default_max_wait=max_wait,
        )),
        RuntimeManager(RuntimeConfig(budget_per_tick=0)),
        PulseEngineConfig(base_spontaneous_rate=0, succession_token_threshold=None),
        causal_ledger=ledger, world_id="world",
        execution_context=lambda event: event.center_id or "home",
        admission_filter=admission_filter,
    )


def synthetic_backlog(ledger, count, *, center=None, world="world", at=None):
    """Bulk-insert valid synthetic inputs in one fixture transaction."""
    at = at or datetime.now(timezone.utc) - timedelta(minutes=5)
    events = [CausalEvent(
        world_id=world, domain=CausalEventDomain.PULSE,
        kind=CausalEventKind.SPONTANEOUS, source=CausalEventSource.SELF,
        engram_id="subject", center_id=center, created_at=at, updated_at=at,
    ) for _ in range(count)]
    with ledger._transaction() as conn:
        for event in events:
            ledger._insert_event_uncommitted(conn, event)
    return [ledger.get_event(event.id) for event in events]


def foreground(ledger, *, center=None):
    return ledger.enqueue(
        "world", domain=CausalEventDomain.PULSE, kind=CausalEventKind.STIMULUS,
        source=CausalEventSource.USER, content="Continue my current work.",
        engram_id="subject", center_id=center,
    )


def test_five_hundred_deferred_inputs_do_not_hide_new_foreground(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    world = WorldRegistry(storage)
    held = world.create_task_bundle_for_existing_engram("Held background", "subject").center.id
    backlog = synthetic_backlog(ledger, 500, center=held)
    user = foreground(ledger)
    now = user.created_at + timedelta(seconds=3)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    engine = make_engine(
        storage, ledger,
        admission_filter=lambda event, _at: event.reason is PulseReason.EXTERNAL,
    )
    try:
        engine._refresh_durable_event_cache()
        selected, _ = engine._select_events(now, 2)
        assert [event.event_id for event in selected] == [user.id]
        assert all(ledger.get_event(event.id).status is CausalEventStatus.QUEUED for event in backlog)
    finally:
        monkeypatch.undo()
        engine.close()
        storage.close()


def test_other_world_backlog_never_enters_local_dendrite(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    foreign = synthetic_backlog(ledger, 500, world="another-world")
    user = foreground(ledger)
    now = user.created_at + timedelta(seconds=3)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    engine = make_engine(storage, ledger)
    try:
        engine._refresh_durable_event_cache()
        selected, _ = engine._select_events(now, 1)
        assert [event.event_id for event in selected] == [user.id]
        assert storage._conn.execute(
            "SELECT COUNT(*) FROM dendritic_input_policy_snapshots WHERE world_id = ?",
            ("another-world",),
        ).fetchone()[0] == 0
        assert all(ledger.get_event(event.id).status is CausalEventStatus.QUEUED for event in foreign)
    finally:
        monkeypatch.undo()
        engine.close()
        storage.close()


@pytest.mark.parametrize("metadata", [
    {"dendritic_integration_version": 1},
    {"generation_id": "synthetic-held-generation", "generation_stage": "summary"},
])
def test_non_raw_queued_rows_do_not_fill_window_validation_prefix(tmp_path, monkeypatch, metadata):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    held = synthetic_backlog(ledger, 500)
    # These explicit metadata fixtures exercise raw-window classification;
    # they are never passed to a Harness or used as result provenance.
    import json
    with ledger._transaction() as conn:
        conn.executemany(
            "UPDATE causal_events SET metadata = ? WHERE id = ?",
            [(json.dumps(metadata), event.id) for event in held],
        )
    user = foreground(ledger)
    now = user.created_at + timedelta(seconds=3)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    engine = make_engine(storage, ledger,
                         admission_filter=lambda event, _at: event.reason is PulseReason.EXTERNAL)
    try:
        engine._refresh_durable_event_cache()
        selected, _ = engine._select_events(now, 1)
        assert [event.event_id for event in selected] == [user.id]
        assert all(ledger.get_event(event.id).status is CausalEventStatus.QUEUED for event in held)
    finally:
        monkeypatch.undo()
        engine.close()
        storage.close()


def test_rotating_scan_reaches_sibling_without_unbounded_cache(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    world = WorldRegistry(storage)
    held, sibling = [world.create_task_bundle_for_existing_engram(title, "subject").center.id
                     for title in ("Held background", "Active sibling")]
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    first = synthetic_backlog(ledger, 500, center=held, at=start)
    second = synthetic_backlog(ledger, 500, center=held, at=start + timedelta(seconds=5))
    active = synthetic_backlog(ledger, 1, center=sibling, at=start + timedelta(seconds=10))[0]
    now = start + timedelta(seconds=20)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    calls = []
    list_events = ledger.list_events

    def observed_query(*args, **kwargs):
        calls.append(kwargs)
        return list_events(*args, **kwargs)

    monkeypatch.setattr(ledger, "list_events", observed_query)
    engine = make_engine(storage, ledger, admission_filter=lambda event, _at: event.center_id == sibling)
    try:
        selected = []
        for _ in range(3):
            before = len(calls)
            engine._refresh_durable_event_cache()
            selected.extend(engine._select_events(now, 1)[0])
            assert len(calls) - before <= 3
            assert len(engine._pending_events) <= 500
            assert len(engine._durable_waiting_event_ids) <= 500
        assert [event.event_id for event in selected] == [active.id]
        assert all(call["limit"] <= 500 and call["world_id"] == "world" for call in calls)
        assert calls[0]["after_seq"] == 0
        assert any(call.get("after_seq", 0) >= first[-1].seq for call in calls)
        assert all(ledger.get_event(event.id).status is CausalEventStatus.QUEUED
                   for event in first + second)
    finally:
        monkeypatch.undo()
        engine.close()
        storage.close()


def source_result(ledger, center, content):
    root = foreground(ledger, center=center)
    turn = ledger.begin_turn(root.id, "subject", root.content, execution_context=center)
    return ledger.settle_turn(turn.id, content)[1]


def test_new_arrivals_cannot_extend_scan_forever_and_hide_resumed_context(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    world = WorldRegistry(storage)
    resumed, busy = [world.create_task_bundle_for_existing_engram(title, "subject").center.id
                     for title in ("Resumed work", "Busy background")]
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    oldest = synthetic_backlog(ledger, 500, center=resumed, at=start)
    synthetic_backlog(ledger, 500, center=busy, at=start + timedelta(seconds=5))
    now = start + timedelta(seconds=30)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    enabled = False
    engine = make_engine(storage, ledger,
                         admission_filter=lambda event, _at: enabled and event.center_id == resumed)
    try:
        engine._refresh_durable_event_cache()
        assert engine._select_events(now, 1)[0] == []
        synthetic_backlog(ledger, 500, center=busy, at=start + timedelta(seconds=10))
        engine._refresh_durable_event_cache()
        assert engine._select_events(now, 1)[0] == []
        synthetic_backlog(ledger, 500, center=busy, at=start + timedelta(seconds=15))
        enabled = True
        engine._refresh_durable_event_cache()
        selected, _ = engine._select_events(now, 1)
        assert selected and selected[0].event_id in {event.id for event in oldest}
    finally:
        monkeypatch.undo()
        engine.close()
        storage.close()


def test_same_context_cache_keeps_foreground_ahead_of_held_high_priority_work(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    world = WorldRegistry(storage)
    source, target = [world.create_task_bundle_for_existing_engram(title, "subject").center.id
                      for title in ("Evidence", "Current work")]
    result = source_result(ledger, source, "Synthetic evidence to revisit.")
    queued = [ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=target,
        connection_id=f"synthetic-edge-{index}", depth=1, priority=1.0,
    ) for index in range(64)]
    user = foreground(ledger, center=target)
    now = user.created_at + timedelta(seconds=3)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    engine = make_engine(storage, ledger,
                         admission_filter=lambda event, _at: event.reason is PulseReason.EXTERNAL)
    try:
        engine._refresh_durable_event_cache()
        selected, _ = engine._select_events(now, 1)
        assert [event.event_id for event in selected] == [user.id]
        assert all(ledger.get_event(event.id).status is CausalEventStatus.QUEUED for event in queued)
    finally:
        monkeypatch.undo()
        engine.close()
        storage.close()


@pytest.mark.parametrize("continuation", ["continue", "restart", "cancel"])
def test_page_boundary_retains_whole_native_convergence(tmp_path, monkeypatch, continuation):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    world = WorldRegistry(storage)
    held, source_a, source_b, target = [
        world.create_task_bundle_for_existing_engram(title, "subject").center.id
        for title in ("Held background", "Evidence A", "Evidence B", "Synthesis")
    ]
    a = source_result(ledger, source_a, "Synthetic local finding A.")
    b = source_result(ledger, source_b, "Synthetic local finding B.")
    synthetic_backlog(ledger, 499, center=held)
    left = ledger.enqueue_context_propagation(
        source_result_id=a.id, target_center_id=target, connection_id="edge-a", depth=1,
    )
    right = ledger.enqueue_context_propagation(
        source_result_id=b.id, target_center_id=target, connection_id="edge-b", depth=1,
    )
    now = right.created_at + timedelta(milliseconds=10)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    gate = lambda event, _at: event.center_id == target
    engine = make_engine(storage, ledger, admission_filter=gate)
    try:
        engine._refresh_durable_event_cache()
        assert engine._select_events(now, 1)[0] == []
        assert engine._durable_waiting_event_ids == (left.id,)
        policy_before = storage._conn.execute(
            "SELECT * FROM dendritic_input_policy_snapshots WHERE event_id = ?", (left.id,),
        ).fetchone()
        if continuation == "restart":
            monkeypatch.undo()
            engine.close()
            engine = make_engine(storage, ledger, admission_filter=gate)
        elif continuation == "cancel":
            ledger.cancel_context_connection("edge-a", world_id="world")
        now = right.created_at + timedelta(seconds=3)
        monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
        selected = []
        for _ in range(2 if continuation == "restart" else 1):
            engine._refresh_durable_event_cache()
            selected.extend(engine._select_events(now, 1)[0])
        assert len(selected) == 1
        received = ledger.get_event(selected[0].event_id)
        if continuation == "cancel":
            assert received.id == right.id
            assert ledger.get_event(left.id).status is CausalEventStatus.CANCELLED
            assert ledger.get_dendritic_integration_for_event(right.id) is None
        else:
            integration = ledger.get_dendritic_integration_for_event(received.id)
            assert {member.event_id for member in integration.members} == {left.id, right.id}
            assert received.content == "Synthetic local finding A.\n\nSynthetic local finding B."
            assert received.metadata["context_connection_ids"] == ["edge-a", "edge-b"]
            assert ledger.get_event(left.id).status is CausalEventStatus.RECONCILED
            assert ledger.get_event(right.id).status is CausalEventStatus.RECONCILED
        assert storage._conn.execute(
            "SELECT * FROM dendritic_input_policy_snapshots WHERE event_id = ?", (left.id,),
        ).fetchone() == policy_before
    finally:
        monkeypatch.undo()
        engine.close()
        storage.close()
