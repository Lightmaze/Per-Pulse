"""Reserved capacity preserves context exclusion and existing Center fairness.

World, SQLite, causal events and the scheduler are real; text is synthetic.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from datetime import datetime, timezone

import pytest

from pulse_system.core.causality import CausalLedger
from pulse_system.core.causality.ledger import RuntimeFence
from pulse_system.core.runtime.scheduling import CenterAdmissionCandidate, DurableCenterScheduler
from pulse_system.core.types import (
    CausalEventDomain, CausalEventFlow, CausalEventKind, CausalEventSource,
    CenterReservationOutcome,
)
from pulse_system.core.world.registry import WorldRegistry
from pulse_system.service.weekly_requests import world_source_for_event
from pulse_system.substrate.storage import Storage


@pytest.fixture
def scheduler_world(tmp_path):
    storage = Storage(tmp_path / "world.db")
    subject = storage.create_engram("continuing-subject").id
    world = WorldRegistry(storage)
    work = world.create_task_bundle_for_existing_engram("Work", subject).center.id
    other = world.create_task_bundle_for_existing_engram("Other work", subject).center.id
    life = world.create_center_for_existing_engram("hobby", "Life", subject).center.id
    ledger = CausalLedger(storage)
    lease = storage.acquire_runtime_lease("test-owner", now=datetime.now(timezone.utc), ttl_sec=60)
    scheduler = DurableCenterScheduler(storage, "world", lease.owner_id, lease.epoch)
    fence = RuntimeFence(lease.owner_id, lease.epoch)

    def enqueue(center, priority, *, user=False):
        event = ledger.enqueue(
            "world", CausalEventFlow.CONTENT if user else None, CausalEventDomain.PULSE,
            CausalEventKind.STIMULUS if user else CausalEventKind.SPONTANEOUS,
            CausalEventSource.USER if user else CausalEventSource.SELF,
            "Synthetic user input." if user else None, engram_id=subject, center_id=center,
            runtime_fence=fence,
        )
        return CenterAdmissionCandidate(
            event.id, subject, center, priority, event.created_at, center,
            foreground=world_source_for_event(ledger, "world", event.id) is None,
        )

    def complete(plan):
        for admission in plan.admissions:
            row = admission.candidate
            event = ledger.get_event(row.event_id)
            turn = ledger.begin_turn(row.event_id, subject, event.content,
                                     execution_context=row.execution_context, runtime_fence=fence)
            ledger.settle_turn(turn.id, "Synthetic completed turn.", runtime_fence=fence)
            scheduler.settle(admission.reservation.id, CenterReservationOutcome.SUCCEEDED)

    yield scheduler, enqueue, complete, work, other, life
    storage.close()


@pytest.mark.parametrize("consume_in_same_plan", [False, True])
def test_background_budget_rejection_does_not_hide_same_context_user(scheduler_world, consume_in_same_plan):
    scheduler, enqueue, complete, work, other, _life = scheduler_world
    background = enqueue(work, 0.8)
    user = enqueue(work, 0.7, user=True)
    candidates = [background, user]
    expected = [user.event_id]
    if consume_in_same_plan:
        first = enqueue(other, 1.0)
        candidates.append(first)
        expected.insert(0, first.event_id)
    plan = scheduler.plan(candidates, budget=2 if consume_in_same_plan else 1, tick=1,
                          background_budget=1 if consume_in_same_plan else 0)
    assert plan.admitted_event_ids == tuple(expected)
    complete(plan)


def test_admitted_background_still_excludes_same_context_user(scheduler_world):
    scheduler, enqueue, complete, work, _other, _life = scheduler_world
    background = enqueue(work, 0.8)
    user = enqueue(work, 0.7, user=True)
    plan = scheduler.plan([background, user], budget=2, tick=1, background_budget=1)
    assert plan.admitted_event_ids == (background.event_id,)
    complete(plan)
    next_plan = scheduler.plan([user], budget=2, tick=2, background_budget=1)
    assert next_plan.admitted_event_ids == (user.event_id,)
    complete(next_plan)


def test_lane_reservation_can_reconsider_user_after_background_budget_is_spent(scheduler_world):
    scheduler, enqueue, complete, work, other, life = scheduler_world
    work_background = enqueue(work, 1.0)
    life_background = enqueue(life, 0.9)
    other_user = enqueue(other, 0.85, user=True)
    life_user = enqueue(life, 0.8, user=True)
    plan = scheduler.plan([work_background, life_background, other_user, life_user],
                          budget=2, tick=1, background_budget=1)
    assert plan.admitted_event_ids == (work_background.event_id, life_user.event_id)
    complete(plan)


@pytest.mark.parametrize("priorities", [(0.3, 0.3), (1.0, 0.3), (0.3, 1.0)])
def test_single_background_slot_keeps_both_lanes_moving(scheduler_world, priorities):
    scheduler, enqueue, complete, work, _other, life = scheduler_world
    centers = (work, life)
    pending = {center: enqueue(center, priority) for center, priority in zip(centers, priorities)}
    admitted = []
    for tick in range(1, 49):
        plan = scheduler.plan(pending.values(), budget=2, tick=tick, background_budget=1)
        assert len(plan.admissions) == 1
        center = plan.admissions[0].candidate.center_id
        admitted.append(center)
        complete(plan)
        pending[center] = enqueue(center, priorities[centers.index(center)])
    # With the existing .05 debt boost, a .7 base-priority gap is overcome
    # within 16 decisions. Replenishing the winner must not starve either lane.
    for start in range(len(admitted) - 15):
        assert set(admitted[start:start + 16]) == set(centers), admitted
