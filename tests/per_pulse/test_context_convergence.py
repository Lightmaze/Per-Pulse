"""Two local contexts fuse their settled outputs in one native dendritic turn."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from pulse_system.core.causality import CausalLedger
from pulse_system.core.causality.ledger import CausalTransitionError, DendriticWindowConflictError
from pulse_system.core.connection.network import ConnectionNetwork
from pulse_system.core.dendrite.processor import DendriteConfig, DendriteProcessor
from pulse_system.core.pulse.engine import PulseEngine, PulseEngineConfig, PulseEvent, PulseReason
from pulse_system.core.runtime.resource import RuntimeManager
from pulse_system.core.types import CausalEventDomain, CausalEventFlow, CausalEventKind, CausalEventSource, CausalEventStatus
from pulse_system.core.world.registry import WorldRegistry
from pulse_system.substrate.storage import Storage


@pytest.fixture
def convergence_body(tmp_path):
    storage = Storage(tmp_path / "world.db")
    subject = storage.create_engram("subject")
    world = WorldRegistry(storage)
    centers = [
        world.create_task_bundle_for_existing_engram(title, subject.id).center.id
        for title in ("Reading", "Experiment", "Synthesis", "Other work")
    ]
    yield storage, CausalLedger(storage), subject.id, centers
    storage.close()


def result_at(ledger, subject, center, finding):
    root = ledger.enqueue(
        "world", CausalEventFlow.CONTENT, CausalEventDomain.PULSE,
        CausalEventKind.STIMULUS, CausalEventSource.USER, "PRIVATE_HISTORY_SHOULD_STAY_LOCAL",
        engram_id=subject, center_id=center,
    )
    turn = ledger.begin_turn(root.id, subject, root.content, execution_context=center or "home")
    return ledger.settle_turn(turn.id, finding)[1]


def make_engine(storage, ledger, **kwargs):
    manager = object()  # These tests inspect native queue/integration, not a model.
    return PulseEngine(
        storage, manager, ConnectionNetwork(storage),
        DendriteProcessor(manager, DendriteConfig(silence_threshold=1, default_max_wait=2)),
        RuntimeManager(), PulseEngineConfig(base_spontaneous_rate=0, succession_token_threshold=None),
        causal_ledger=ledger, world_id="world", **kwargs,
    )


def enqueue_pair(storage, ledger, subject, centers, *, same_source=False, split_targets=False):
    first, second, target, other = centers
    source_a = result_at(ledger, subject, first, "Synthetic finding A.")
    source_b = result_at(ledger, subject, first if same_source else second, "Synthetic finding B.")
    left = ledger.enqueue_context_propagation(
        source_result_id=source_a.id, target_center_id=target,
        connection_id="edge-a", depth=1,
    )
    right = ledger.enqueue_context_propagation(
        source_result_id=source_b.id, target_center_id=other if split_targets else target,
        connection_id="edge-b", depth=1,
    )
    return left, right


def form_window(engine, events, monkeypatch):
    observed = max(event.created_at for event in events) + timedelta(seconds=3)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: observed)
    engine._refresh_durable_event_cache()
    monkeypatch.undo()


def test_distinct_local_outputs_fuse_with_provenance_and_survive_restart(convergence_body, tmp_path, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    left, right = enqueue_pair(storage, ledger, subject, centers)
    engine = make_engine(storage, ledger)
    form_window(engine, (left, right), monkeypatch)
    assert len(engine._pending_events) == 1
    projected = engine._pending_events[0]
    assert projected.reason is PulseReason.PROPAGATION
    aggregate = ledger.get_event(projected.event_id)
    assert aggregate.content == "Synthetic finding A.\n\nSynthetic finding B."
    assert "PRIVATE_HISTORY" not in aggregate.content
    assert aggregate.engram_id == subject
    assert aggregate.center_id == centers[2]
    assert aggregate.metadata["context_pulse"] == "convergence"
    assert aggregate.metadata["context_connection_ids"] == ["edge-a", "edge-b"]
    assert aggregate.metadata["depth"] == 1
    assert ledger.get_event(left.id).status is CausalEventStatus.RECONCILED
    integration = ledger.get_dendritic_integration_for_event(aggregate.id)
    assert {member.source_identity for member in integration.members} == {
        f"context:center:{centers[0]}", f"context:center:{centers[1]}",
    }
    engine.close()
    storage.close()
    reopened = Storage(tmp_path / "world.db")
    try:
        recovered = CausalLedger(reopened)
        restored = make_engine(reopened, recovered)
        restored._refresh_durable_event_cache()
        assert [event.event_id for event in restored._pending_events] == [aggregate.id]
        assert recovered.get_dendritic_integration_for_event(aggregate.id).id == integration.id
        turn = recovered.begin_turn(aggregate.id, subject, aggregate.content, execution_context=centers[2])
        _turn, result = recovered.settle_turn(turn.id, "Synthetic synthesis from both local findings.")
        next_delivery = recovered.enqueue_context_propagation(
            source_result_id=result.id, target_center_id=None,
            connection_id="synthesis-home", depth=2,
        )
        assert next_delivery.metadata["depth"] == 2
        assert next_delivery.parent_event_id == result.id
        restored.close()
    finally:
        reopened.close()


@pytest.mark.parametrize("mode", ["same-source", "different-targets"])
def test_fusion_requires_distinct_sources_and_one_target(convergence_body, monkeypatch, mode):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(
        storage, ledger, subject, centers,
        same_source=mode == "same-source", split_targets=mode == "different-targets",
    )
    engine = make_engine(storage, ledger)
    form_window(engine, events, monkeypatch)
    assert {event.event_id for event in engine._pending_events} == {event.id for event in events}
    assert all(ledger.get_dendritic_integration_for_event(event.id) is None for event in events)
    engine.close()


def test_removed_connection_revokes_a_fused_pending_turn_without_destroying_evidence(convergence_body, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(storage, ledger, subject, centers)
    engine = make_engine(storage, ledger)
    form_window(engine, events, monkeypatch)
    aggregate_id = engine._pending_events[0].event_id
    assert ledger.cancel_context_connection("edge-a", world_id="world") == 1
    assert ledger.get_event(aggregate_id).status is CausalEventStatus.CANCELLED
    integration = ledger.get_dendritic_integration_for_event(aggregate_id)
    assert len(integration.members) == 2
    assert all(ledger.get_event(event.id).status is CausalEventStatus.RECONCILED for event in events)
    engine._refresh_durable_event_cache()
    assert engine._pending_events == []
    assert ledger.cancel_context_connection("edge-b", world_id="world") == 0
    engine.close()


def test_fused_turn_cannot_bypass_the_context_admission_filter(convergence_body, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(storage, ledger, subject, centers)
    seen = []

    def admits(event, _at):
        durable = ledger.get_event(event.event_id)
        seen.append(durable.metadata)
        return durable.metadata.get("context_pulse") != "convergence"

    engine = make_engine(storage, ledger, admission_filter=admits)
    form_window(engine, events, monkeypatch)
    projected = engine._pending_events[0]
    assert engine._select_events(projected.created_at, 1)[0] == []
    assert seen[0]["context_connection_ids"] == ["edge-a", "edge-b"]
    assert engine._pending_events == [projected]
    engine.close()


def test_fake_context_source_metadata_cannot_enter_native_convergence(convergence_body, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(storage, ledger, subject, centers)
    # Model a malformed historical row: the integration transaction must prove
    # metadata against the real source result, not trust its context label.
    with storage._lock:
        storage._conn.execute(
            "UPDATE causal_events SET metadata=json_set(metadata, '$.source_center_id', ?) WHERE id=?",
            (centers[3], events[1].id),
        )
        storage._conn.commit()
    engine = make_engine(storage, ledger)
    with pytest.raises(CausalTransitionError, match="source lineage"):
        form_window(engine, events, monkeypatch)
    monkeypatch.undo()
    assert all(ledger.get_event(event.id).status is CausalEventStatus.QUEUED for event in events)
    engine.close()


def commit_successor(storage, ledger):
    generation = ledger.begin_generation("subject", world_id="world")
    ledger.transition_generation(generation.id, "summarizing")
    storage.create_provisional_engram("successor", None, [], token_count=0)
    ledger.transition_generation(generation.id, "rotating", successor_id="successor")
    WorldRegistry(storage).update_focal_succession("subject", "successor")
    ledger.commit_succession_publication(
        generation.id, "subject", "successor", summary_turn_id=None, runtime_fence=None,
    )


def test_fused_pending_input_hands_off_without_rewriting_predecessor_evidence(convergence_body, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(storage, ledger, subject, centers)
    engine = make_engine(storage, ledger)
    form_window(engine, events, monkeypatch)
    aggregate_id = engine._pending_events[0].event_id
    old_integration = ledger.get_dendritic_integration_for_event(aggregate_id)
    engine.close()
    commit_successor(storage, ledger)
    restored = make_engine(storage, ledger)
    restored._refresh_durable_event_cache()
    assert [(event.event_id, event.engram_id) for event in restored._pending_events] == [(aggregate_id, "successor")]
    integration = ledger.get_dendritic_integration_for_event(aggregate_id)
    assert integration == old_integration
    assert all(ledger.get_event(event.id).engram_id == subject for event in events)
    # The stable receipt remains valid even though its current consumer changed.
    for event in events:
        receipt = ledger.enqueue_context_propagation(
            source_result_id=event.parent_event_id, target_center_id=event.center_id,
            connection_id=event.metadata["context_connection_id"], depth=1, engram_id="successor",
        )
        assert receipt.id == event.id and receipt.engram_id == subject
    assert ledger.cancel_context_connection("edge-a", world_id="world") == 1
    assert ledger.get_dendritic_integration_for_event(aggregate_id).id == integration.id
    restored.close()


def test_unclosed_input_preserves_frozen_window_policy_through_succession(convergence_body, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(storage, ledger, subject, centers)
    engine = make_engine(storage, ledger)
    engine._refresh_durable_event_cache()  # Freeze opening policies without closing the one-second window.
    assert engine._pending_events == []
    engine.close()
    commit_successor(storage, ledger)
    restored = make_engine(storage, ledger)
    form_window(restored, events, monkeypatch)
    assert len(restored._pending_events) == 1
    aggregate = ledger.get_event(restored._pending_events[0].event_id)
    assert aggregate.engram_id == "successor"
    assert ledger.get_dendritic_integration_for_event(aggregate.id).formation_engram_id == "successor"
    restored.close()


def test_sealed_unfused_window_keeps_auditable_policy_after_handoff(convergence_body, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(storage, ledger, subject, centers, same_source=True)
    engine = make_engine(storage, ledger)
    ready_windows = []
    materialize = ledger.materialize_dendritic_window

    def capture(ready, groups, **kwargs):
        ready_windows.append(ready)
        return materialize(ready, groups, **kwargs)

    monkeypatch.setattr(ledger, "materialize_dendritic_window", capture)
    form_window(engine, events, monkeypatch)
    assert len(engine._pending_events) == 2
    engine.close()
    commit_successor(storage, ledger)
    durable_window, integrations = materialize(ready_windows[0], ())
    assert durable_window.formation_engram_id == subject
    assert integrations == ()
    assert all(ledger.get_event(event.id).engram_id == "successor" for event in events)


def test_revocation_and_convergence_commit_atomically(convergence_body, monkeypatch):
    storage, ledger, subject, centers = convergence_body
    events = enqueue_pair(storage, ledger, subject, centers)
    engine = make_engine(storage, ledger)
    observed = max(event.created_at for event in events) + timedelta(seconds=3)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: observed)
    monkeypatch.setattr(engine, "_materialize_ready_dendritic_integrations", lambda *_args: set())
    engine._refresh_durable_event_cache()
    ready = engine._dendrite.get_ready_event_windows(observed)[0]
    monkeypatch.undo()
    barrier = threading.Barrier(2)

    def converge():
        barrier.wait()
        try:
            return ledger.materialize_dendritic_window(ready, (tuple(event.id for event in events),))
        except DendriticWindowConflictError:
            return None

    def revoke():
        barrier.wait()
        return ledger.cancel_context_connection("edge-a", world_id="world")

    with ThreadPoolExecutor(max_workers=2) as workers:
        fused_future = workers.submit(converge)
        cancelled_future = workers.submit(revoke)
        fused, cancelled = fused_future.result(), cancelled_future.result()
    assert cancelled == 1
    if fused is None:
        assert ledger.get_event(events[0].id).status is CausalEventStatus.CANCELLED
        assert ledger.get_event(events[1].id).status is CausalEventStatus.QUEUED
    else:
        aggregate = fused[1][0][1]
        assert ledger.get_event(aggregate.id).status is CausalEventStatus.CANCELLED
        assert ledger.get_dendritic_integration_for_event(aggregate.id).member_count == 2
    engine.close()
