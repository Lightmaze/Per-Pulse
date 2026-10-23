"""Context diffusion uses real causal turns, persistence, and membership checks.

All text and turns in this module are explicit synthetic fixtures; no model is
called. Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

import pytest

from pulse_system.core.causality import CausalLedger, CausalTransitionError
from pulse_system.core.causality.ledger import RuntimeFence
from pulse_system.core.runtime.scheduling import CenterAdmissionCandidate, DurableCenterScheduler
from pulse_system.core.types import (
    ActivityCenterStatus,
    CausalEventDomain,
    CausalEventFlow,
    CausalEventKind,
    CausalEventSource,
    CausalEventStatus,
    CenterReservationOutcome,
    CenterReservationState,
    RuntimeLeaseLostError,
)
from pulse_system.core.world.registry import WorldRegistry
from pulse_system.substrate.storage import Storage


@pytest.fixture
def body(tmp_path):
    storage = Storage(tmp_path / "world.db")
    subject = storage.create_engram("continuing-subject")
    world = WorldRegistry(storage)
    first = world.create_task_bundle_for_existing_engram("Reading", subject.id)
    second = world.create_task_bundle_for_existing_engram("Writing", subject.id)
    yield storage, CausalLedger(storage), subject.id, first.center.id, second.center.id
    storage.close()


def settled_result(ledger, subject, center, *, metadata=None, result_metadata=None):
    root = ledger.enqueue(
        "world", CausalEventFlow.CONTENT, CausalEventDomain.PULSE,
        CausalEventKind.STIMULUS, CausalEventSource.USER,
        "Synthetic input for this local context.", engram_id=subject,
        center_id=center, metadata=metadata,
    )
    turn = ledger.begin_turn(root.id, subject, root.content, execution_context=center or "home")
    _turn, result = ledger.settle_turn(
        turn.id, "Synthetic finding with a durable source.", metadata=result_metadata,
    )
    return root, result


@pytest.mark.parametrize("marker", ["propagation", "convergence", "reentry"])
def test_reserved_context_metadata_cannot_mint_a_harness_turn(body, marker):
    _storage, ledger, subject, first, _second = body
    _root, result = settled_result(ledger, subject, first)
    metadata = {"context_pulse": marker, "source_result_id": result.id, "depth": 0}
    if marker == "propagation":
        metadata.update(context_connection_id="forged", source_center_id=first,
                        source_engram_id=subject)
    forged = ledger.enqueue(
        "world", None, CausalEventDomain.PULSE, CausalEventKind.SPONTANEOUS,
        CausalEventSource.SELF, None, parent_event_id=result.id,
        engram_id=subject, center_id=first, metadata=metadata,
    )
    with pytest.raises(CausalTransitionError, match="context"):
        ledger.begin_turn(forged.id, subject, execution_context=first)
    assert ledger.get_event(forged.id).status is CausalEventStatus.QUEUED
    assert ledger.get_event(forged.id).attempts == 0


def test_canonical_null_flow_reentry_is_claimable(body):
    _storage, ledger, subject, first, _second = body
    _root, result = settled_result(ledger, subject, first)
    reentry = ledger.enqueue(
        "world", None, CausalEventDomain.PULSE, CausalEventKind.SPONTANEOUS,
        CausalEventSource.SELF, None, parent_event_id=result.id,
        engram_id=subject, center_id=first,
        metadata={"context_pulse": "reentry", "source_result_id": result.id, "depth": 0},
        idempotency_key=f"context-reentry:world:{result.id}",
    )
    turn = ledger.begin_turn(reentry.id, subject, execution_context=first)
    assert turn.event_id == reentry.id
    assert ledger.get_event(reentry.id).status is CausalEventStatus.RUNNING


def test_context_paused_after_enqueue_is_rechecked_at_claim(body):
    storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    delivery = ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=second,
        connection_id="pause-before-claim", depth=1,
    )
    storage.update_activity_center(second, status=ActivityCenterStatus.PAUSED)
    with pytest.raises(CausalTransitionError, match="active subject-owned context"):
        ledger.begin_turn(delivery.id, subject, delivery.content, execution_context=second)
    assert ledger.get_event(delivery.id).status is CausalEventStatus.QUEUED
    assert ledger.get_event(delivery.id).attempts == 0


def test_context_transfer_preserves_source_and_subject_with_explicit_target(body):
    storage, ledger, subject, first, second = body
    root, result = settled_result(ledger, subject, first)
    delivery = ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=second,
        connection_id="reading-to-writing", depth=1,
    )
    assert delivery.content == result.content
    assert delivery.parent_event_id == result.id
    assert delivery.causal_id == root.id
    assert delivery.center_id == second
    assert delivery.engram_id == subject
    assert delivery.status is CausalEventStatus.QUEUED
    assert delivery.flow is CausalEventFlow.CONTENT
    assert delivery.metadata["source_center_id"] == first
    assert ledger.dendritic_candidate_signature(delivery) == ("propagation", f"context:center:{first}")
    assert len(storage.list_engrams()) == 1

    # The old general-purpose path must not acquire a center-switch bypass.
    with pytest.raises(CausalTransitionError, match="inherit parent center"):
        ledger.enqueue(
            "world", CausalEventFlow.CONTENT, CausalEventDomain.PULSE,
            CausalEventKind.PROPAGATION, CausalEventSource.PROPAGATION,
            result.content, parent_event_id=result.id,
            engram_id=subject, center_id=second,
        )


def test_home_is_explicit_destination_and_can_be_the_source(body):
    _storage, ledger, subject, first, _second = body
    _root, result = settled_result(ledger, subject, first)
    returned = ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=None,
        connection_id="reading-to-home", depth=1,
    )
    assert returned.center_id is None
    _home_root, home_result = settled_result(ledger, subject, None)
    outbound = ledger.enqueue_context_propagation(
        source_result_id=home_result.id, target_center_id=first,
        connection_id="home-to-reading", depth=1,
    )
    assert outbound.center_id == first
    assert outbound.metadata["source_center_id"] is None


def test_redelivery_is_idempotent_after_restart_and_under_concurrent_observation(body, tmp_path):
    storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    args = dict(source_result_id=result.id, target_center_id=second,
                connection_id="one-edge", depth=1)
    with ThreadPoolExecutor(max_workers=4) as workers:
        deliveries = list(workers.map(lambda _i: ledger.enqueue_context_propagation(**args), range(12)))
    assert len({event.id for event in deliveries}) == 1
    storage.close()
    reopened = Storage(tmp_path / "world.db")
    try:
        recovered_ledger = CausalLedger(reopened)
        recovered = recovered_ledger.enqueue_context_propagation(**args)
        assert recovered.id == deliveries[0].id
        assert len(recovered_ledger.get_children(result.id)) == 1
        with pytest.raises(CausalTransitionError, match="another identity"):
            recovered_ledger.enqueue_context_propagation(**{**args, "target_center_id": None})
    finally:
        reopened.close()


def test_guard_rolls_back_delivery_and_is_not_spent_on_idempotent_retry(body):
    _storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    args = dict(source_result_id=result.id, target_center_id=second,
                connection_id="budgeted-edge", depth=1)
    attempts = []

    def reject(_conn):
        attempts.append("rejected")
        raise CausalTransitionError("background budget exhausted")

    with pytest.raises(CausalTransitionError, match="budget exhausted"):
        ledger.enqueue_context_propagation(**args, admission_guard=reject)
    assert ledger.get_children(result.id) == []
    created = ledger.enqueue_context_propagation(**args)
    assert ledger.enqueue_context_propagation(**args, admission_guard=reject).id == created.id
    assert attempts == ["rejected"]


def test_inactive_or_unrelated_context_cannot_receive_or_emit(body):
    storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    args = dict(source_result_id=result.id, target_center_id=second,
                connection_id="active-edge", depth=1)
    storage.update_activity_center(second, status=ActivityCenterStatus.PAUSED)
    with pytest.raises(CausalTransitionError, match="active subject memberships"):
        ledger.enqueue_context_propagation(**args)
    storage.update_activity_center(second, status=ActivityCenterStatus.ACTIVE)
    storage.update_activity_center(first, status=ActivityCenterStatus.ARCHIVED)
    with pytest.raises(CausalTransitionError, match="active subject memberships"):
        ledger.enqueue_context_propagation(**args)
    storage.update_activity_center(first, status=ActivityCenterStatus.ACTIVE)
    other = storage.create_engram("unrelated-subject")
    with pytest.raises(CausalTransitionError, match="switch subjects"):
        ledger.enqueue_context_propagation(**args, engram_id=other.id)
    unrelated = WorldRegistry(storage).create_task_bundle_for_existing_engram("Other", other.id)
    with pytest.raises(CausalTransitionError, match="active subject memberships"):
        ledger.enqueue_context_propagation(**{**args, "target_center_id": unrelated.center.id})
    assert ledger.get_children(result.id) == []


def test_fabricated_result_without_accepted_harness_turn_cannot_propagate(body):
    _storage, ledger, subject, first, second = body
    root, _result = settled_result(ledger, subject, first)
    fabricated = ledger.record_child(
        root.id, kind=CausalEventKind.ASSISTANT_RESULT,
        domain=CausalEventDomain.HARNESS, source=CausalEventSource.SELF,
        content="A labeled fact is not an accepted Harness turn.",
    )
    with pytest.raises(CausalTransitionError, match="accepted source Harness turn"):
        ledger.enqueue_context_propagation(
            source_result_id=fabricated.id, target_center_id=second,
            connection_id="forged-edge", depth=1,
        )


def test_replay_and_depth_reset_cannot_start_unbounded_new_cascades(body):
    _storage, ledger, subject, first, second = body
    _root, replay = settled_result(ledger, subject, first, result_metadata={"execution_regime": "replay"})
    with pytest.raises(CausalTransitionError, match="ordinary source result"):
        ledger.enqueue_context_propagation(
            source_result_id=replay.id, target_center_id=second,
            connection_id="replay-edge", depth=1,
        )
    _root, source = settled_result(ledger, subject, first)
    delivery = ledger.enqueue_context_propagation(
        source_result_id=source.id, target_center_id=second,
        connection_id="forward", depth=1,
    )
    turn = ledger.begin_turn(delivery.id, subject, delivery.content, execution_context=second)
    _turn, result = ledger.settle_turn(turn.id, "Synthetic later consequence.")
    with pytest.raises(CausalTransitionError, match="advance the source causal depth"):
        ledger.enqueue_context_propagation(
            source_result_id=result.id, target_center_id=first,
            connection_id="back", depth=1,
        )
    back = ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=first,
        connection_id="back", depth=2,
    )
    assert back.causal_id == source.causal_id
    assert back.metadata["depth"] == 2


def test_connection_revocation_cancels_only_unstarted_own_world_deliveries(body):
    _storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    args = dict(source_result_id=result.id, target_center_id=second,
                connection_id="revocable-edge", depth=1)
    pending = ledger.enqueue_context_propagation(**args)
    user = ledger.enqueue(
        "world", CausalEventFlow.CONTENT, CausalEventDomain.PULSE,
        CausalEventKind.STIMULUS, CausalEventSource.USER, "An independent user request.",
        engram_id=subject, center_id=second,
        metadata={"context_connection_id": "revocable-edge"},
    )
    assert ledger.cancel_context_connection("revocable-edge", world_id="another-world") == 0
    assert ledger.cancel_context_connection("revocable-edge", world_id="world") == 1
    assert ledger.cancel_context_connection("revocable-edge", world_id="world") == 0
    cancelled = ledger.get_event(pending.id)
    assert cancelled.status is CausalEventStatus.CANCELLED
    assert cancelled.metadata["context_cancel_reason"] == "connection_removed"
    assert ledger.get_event(user.id).status is CausalEventStatus.QUEUED
    assert ledger.enqueue_context_propagation(**args).status is CausalEventStatus.CANCELLED
    with pytest.raises(CausalTransitionError, match="expected queued"):
        ledger.begin_turn(pending.id, subject, pending.content)


def test_connection_revocation_preserves_started_and_settled_work(body):
    _storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    delivery = ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=second,
        connection_id="started-edge", depth=1,
    )
    turn = ledger.begin_turn(delivery.id, subject, delivery.content, execution_context=second)
    assert ledger.cancel_context_connection("started-edge", world_id="world") == 0
    assert ledger.get_event(delivery.id).status is CausalEventStatus.RUNNING
    ledger.settle_turn(turn.id, "The previously accepted turn settled.")
    assert ledger.cancel_context_connection("started-edge", world_id="world") == 0
    assert ledger.get_event(delivery.id).status is CausalEventStatus.SETTLED


def test_reserved_but_unclaimed_delivery_is_revocable(body):
    storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    delivery = ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=second,
        connection_id="reserved-edge", depth=1,
    )
    now = datetime.now(timezone.utc)
    lease = storage.acquire_runtime_lease("test-owner", now=now, ttl_sec=60)
    scheduler = DurableCenterScheduler(storage, "world", lease.owner_id, lease.epoch)
    plan = scheduler.plan([
        CenterAdmissionCandidate(delivery.id, subject, second, 0.8, delivery.created_at, second),
    ], budget=1, tick=1)
    assert plan.admitted_event_ids == (delivery.id,)
    fence = RuntimeFence(lease.owner_id, lease.epoch)
    assert ledger.cancel_context_connection("reserved-edge", world_id="world", runtime_fence=fence) == 1
    with pytest.raises(CausalTransitionError, match="expected queued"):
        ledger.begin_turn(delivery.id, subject, delivery.content, execution_context=second, runtime_fence=fence)
    settled = scheduler.settle(plan.reservations[0].id, CenterReservationOutcome.SKIPPED)
    assert settled.state is CenterReservationState.SETTLED


def test_claim_and_revocation_have_one_atomic_winner(body):
    _storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    delivery = ledger.enqueue_context_propagation(
        source_result_id=result.id, target_center_id=second,
        connection_id="racing-edge", depth=1,
    )
    ready = threading.Barrier(2)

    def claim():
        ready.wait()
        try:
            return ledger.begin_turn(delivery.id, subject, delivery.content, execution_context=second)
        except CausalTransitionError:
            return None

    def revoke():
        ready.wait()
        return ledger.cancel_context_connection("racing-edge", world_id="world")

    with ThreadPoolExecutor(max_workers=2) as workers:
        claimed_future = workers.submit(claim)
        revoked_future = workers.submit(revoke)
        claimed, revoked = claimed_future.result(), revoked_future.result()
    assert (claimed is not None) != bool(revoked)
    assert ledger.get_event(delivery.id).status is (
        CausalEventStatus.RUNNING if claimed is not None else CausalEventStatus.CANCELLED
    )


def test_context_mutations_reject_a_stale_runtime_fence(body):
    storage, ledger, subject, first, second = body
    _root, result = settled_result(ledger, subject, first)
    args = dict(source_result_id=result.id, target_center_id=second,
                connection_id="fenced-edge", depth=1)
    lease = storage.acquire_runtime_lease("current-owner", now=datetime.now(timezone.utc), ttl_sec=60)
    stale = RuntimeFence(lease.owner_id, lease.epoch + 1)
    with pytest.raises(RuntimeLeaseLostError):
        ledger.enqueue_context_propagation(**args, runtime_fence=stale)
    assert ledger.get_children(result.id) == []
    good = RuntimeFence(lease.owner_id, lease.epoch)
    delivery = ledger.enqueue_context_propagation(**args, runtime_fence=good)
    with pytest.raises(RuntimeLeaseLostError):
        ledger.cancel_context_connection("fenced-edge", world_id="world", runtime_fence=stale)
    assert ledger.get_event(delivery.id).status is CausalEventStatus.QUEUED
