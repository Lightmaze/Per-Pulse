"""Restart-safe Pulse background admission envelope over native World/Kernel evidence.

The manually settled receipts and HTTP provider replies here are explicit test
doubles. These checks do not establish real provider billing or model quality.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import pulse_system.core.pulse.context as context_module
from per_pulse.service import PageService, PulseSettings
from pulse_system.core.pulse.context import _QuotaReached
from test_pulse_integration import (
    TransportFixture, assistant_count, client_for, connect, eventually, page, view,
)


def settings(*, limit=100, reservation=60):
    return PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        refractory_seconds=0, max_background_pulses_per_hour=12,
        max_background_provider_tokens_per_hour=limit,
        background_provider_token_reservation=reservation,
    )


def seed_source(runtime):
    """Create a real settled foreground World turn without a model call."""
    subject = runtime.continuity_engram_id
    source_id = runtime.inject(subject, "Source experience for local continuation.")
    turn = runtime.causal_ledger.begin_turn(
        source_id, subject, "Source experience for local continuation.",
        runtime_fence=runtime._current_runtime_fence(),
    )
    _, result = runtime.causal_ledger.settle_turn(
        turn.id, "A source result.", metadata={"evidence_class": "TEST_DOUBLE"},
        usage={"input_count": 3, "output_count": 2},
        runtime_fence=runtime._current_runtime_fence(),
    )
    return result.id


def queue_background(runtime, parent_id):
    """The coordinator's guard executes within the original SQLite enqueue."""
    return runtime.causal_ledger.enqueue(
        runtime.world_id, kind="spontaneous", domain="pulse", source="self",
        engram_id=runtime.continuity_engram_id, parent_event_id=parent_id,
        metadata={"context_pulse": "reentry", "source_result_id": parent_id, "depth": 0},
        idempotency_key=f"context-reentry:{runtime.world_id}:{parent_id}",
        admission_guard=runtime.context_pulse._guard,
        runtime_fence=runtime._current_runtime_fence(),
    )


def settle_background(runtime, root, *, evidence="TEST_DOUBLE", inputs=28, outputs=12):
    turn = runtime.causal_ledger.begin_turn(
        root.id, runtime.continuity_engram_id, "Continuation.",
        runtime_fence=runtime._current_runtime_fence(),
    )
    runtime.causal_ledger.settle_turn(
        turn.id, "Result after background activation.",
        metadata={"evidence_class": evidence},
        usage={"input_count": inputs, "output_count": outputs},
        runtime_fence=runtime._current_runtime_fence(),
    )


def test_token_admission_reserves_then_reconciles_from_the_original_turn(tmp_path):
    service = PageService(tmp_path, model_factory=None, pulse=settings())
    try:
        runtime = service.runtime
        parent_id = seed_source(runtime)
        initial = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert initial["charged"] == initial["trackedAdmissions"] == 0

        first = queue_background(runtime, parent_id)
        pending = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert pending["charged"] == 60
        assert pending["unaccountedReservations"] == 60
        assert pending["trackedAdmissions"] == 1
        # A repeated idempotency key is a read, not a second charge.
        assert queue_background(runtime, parent_id).id == first.id
        other_parent = seed_source(runtime)
        with pytest.raises(_QuotaReached):
            queue_background(runtime, other_parent)

        settle_background(runtime, first)
        settled = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert settled["charged"] == settled["accountedTokens"] == 40
        assert settled["unaccountedReservations"] == 0
        assert settled["syntheticTokens"] == 40
        assert settled["remaining"] == 60
        second = queue_background(runtime, other_parent)
        turn = runtime.causal_ledger.begin_turn(
            second.id, runtime.continuity_engram_id, "Continuation.",
            runtime_fence=runtime._current_runtime_fence(),
        )
        runtime.causal_ledger.fail_turn(
            turn.id, acceptance=None, code="provider_usage_unknown", phase="kernel",
            runtime_fence=runtime._current_runtime_fence(),
        )
        guarded = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert guarded["charged"] == 100
        assert guarded["accountedTokens"] == 40
        assert guarded["unaccountedReservations"] == 60
        assert guarded["remaining"] == 0
        third_parent = seed_source(runtime)
        with pytest.raises(_QuotaReached):
            queue_background(runtime, third_parent)
        # The budget gates background admission, not a human foreground input.
        foreground_id = runtime.inject(runtime.continuity_engram_id, "Correct the plan.")
        assert runtime.causal_ledger.get_event(foreground_id).source.value == "user"
    finally:
        service.runtime.close()

    reopened = PageService(tmp_path, model_factory=None, pulse=settings())
    try:
        runtime = reopened.runtime
        old = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert old["charged"] == 100
        assert old["unaccountedReservations"] == 60
        assert old["trackedAdmissions"] == 2
        with pytest.raises(_QuotaReached):
            queue_background(runtime, third_parent)
    finally:
        reopened.runtime.close()


def test_missing_and_oversized_receipts_never_create_free_budget(tmp_path):
    service = PageService(tmp_path, model_factory=None, pulse=settings())
    try:
        runtime = service.runtime
        parent = seed_source(runtime)
        first = queue_background(runtime, parent)
        # A partly reported model response must not be treated as settled billable zero.
        settle_background(runtime, first, evidence="KERNEL_PROVIDER_USAGE_UNACCOUNTED",
                          inputs=1, outputs=1)
        unknown = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert unknown["charged"] == 60
        assert unknown["accountedTokens"] == 0
        second_parent = seed_source(runtime)
        with pytest.raises(_QuotaReached):
            queue_background(runtime, second_parent)
    finally:
        service.runtime.close()

    other = PageService(tmp_path / "overrun", model_factory=None, pulse=settings())
    try:
        runtime = other.runtime
        parent = seed_source(runtime)
        first = queue_background(runtime, parent)
        settle_background(runtime, first, inputs=110, outputs=30)
        overrun = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert overrun["charged"] == 140
        assert overrun["overrunTokens"] == 80
        assert overrun["remaining"] == 0
        assert overrun["hardProviderSpendingCap"] is False
        second_parent = seed_source(runtime)
        with pytest.raises(_QuotaReached):
            queue_background(runtime, second_parent)
    finally:
        other.runtime.close()


def test_old_queued_background_still_reserves_after_one_hour(tmp_path, monkeypatch):
    """A held local activation is a future liability, not a free new hour."""
    service = PageService(tmp_path, model_factory=None, pulse=settings(limit=60))
    try:
        runtime = service.runtime
        parent = seed_source(runtime)
        first = queue_background(runtime, parent)
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        monkeypatch.setattr(context_module, "_now", lambda: future)
        budget = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert budget["charged"] == budget["unaccountedReservations"] == 60
        assert budget["trackedAdmissions"] == 1
        assert budget["remaining"] == 0
        second_parent = seed_source(runtime)
        with pytest.raises(_QuotaReached):
            queue_background(runtime, second_parent)
        assert runtime.causal_ledger.get_event(first.id).status.value == "queued"
    finally:
        service.runtime.close()

    reopened = PageService(tmp_path, model_factory=None, pulse=settings(limit=60))
    try:
        saved = reopened.runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert saved["charged"] == 60
        assert saved["trackedAdmissions"] == 1
        with pytest.raises(_QuotaReached):
            queue_background(reopened.runtime, second_parent)
    finally:
        reopened.runtime.close()


def test_old_admission_settled_now_counts_in_current_hour(tmp_path):
    """Recent real settlement of an earlier reservation is not free usage."""
    service = PageService(tmp_path, model_factory=None, pulse=settings(limit=60))
    try:
        runtime = service.runtime
        source = seed_source(runtime)
        first = queue_background(runtime, source)
        # Simulate the passage of two hours between enqueue and execution
        # without sleeping: only the root's admission timestamp is backdated.
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with runtime.causal_ledger._transaction() as conn:
            conn.execute("UPDATE causal_events SET created_at = ? WHERE id = ?",
                         (old_time, first.id))
        settle_background(runtime, first, inputs=28, outputs=12)
        budget = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert budget["trackedAdmissions"] == 1
        assert budget["charged"] == budget["accountedTokens"] == 40
        assert budget["unaccountedReservations"] == 0
        assert budget["remaining"] == 20
        second_parent = seed_source(runtime)
        with pytest.raises(_QuotaReached):
            queue_background(runtime, second_parent)
    finally:
        service.runtime.close()


def test_old_terminal_uncertainty_ages_out_after_its_actual_failure(tmp_path, monkeypatch):
    """A failed old activation counts near failure but is not held forever."""
    service = PageService(tmp_path, model_factory=None, pulse=settings(limit=60))
    try:
        runtime = service.runtime
        first = queue_background(runtime, seed_source(runtime))
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with runtime.causal_ledger._transaction() as conn:
            conn.execute("UPDATE causal_events SET created_at = ? WHERE id = ?",
                         (old_time, first.id))
        turn = runtime.causal_ledger.begin_turn(
            first.id, runtime.continuity_engram_id, "Continuation.",
            runtime_fence=runtime._current_runtime_fence(),
        )
        runtime.causal_ledger.fail_turn(
            turn.id, acceptance=None, code="provider_usage_unknown",
            phase="kernel", runtime_fence=runtime._current_runtime_fence(),
        )
        near_failure = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert near_failure["charged"] == 60
        assert near_failure["unaccountedReservations"] == 60
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        monkeypatch.setattr(context_module, "_now", lambda: future)
        later = runtime.context_pulse.snapshot()["providerTokenBudget"]
        assert later["charged"] == 0
        assert later["trackedAdmissions"] == 0
        assert later["remaining"] == 60
    finally:
        service.runtime.close()


@pytest.mark.parametrize("kwargs", [
    {"max_background_provider_tokens_per_hour": -1},
    {"max_background_provider_tokens_per_hour": True},
    {"background_provider_token_reservation": 0},
    {"background_provider_token_reservation": False},
])
def test_invalid_token_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        PulseSettings(**kwargs)


def test_http_kernel_usage_releases_only_a_proven_background_reservation(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    profile = settings(limit=90, reservation=60)
    with client_for(tmp_path, pulse=profile, model_factory=fixture.factory) as client:
        source = page(client, "Evidence", "A_LOCAL_HISTORY")
        target = page(client, "Synthesis", "B_LOCAL_HISTORY")
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 1)
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 1)
        connect(client, source, target, weight=0.9)
        sent = client.post(f"/api/pages/{source}/messages", json={"content": "First fact"})
        assert sent.status_code == 202
        eventually(lambda: view(client, target), lambda v: assistant_count(v) == 2)
        budget = eventually(
            lambda: client.get("/api/pulse").json()["providerTokenBudget"],
            lambda b: b["accountedTokens"] == 40,
        )
        assert budget["charged"] == 40
        assert budget["unaccountedReservations"] == 0
        assert budget["remaining"] == 50
        assert budget["trackedAdmissions"] == 1
        sent = client.post(f"/api/pages/{source}/messages", json={"content": "Second fact"})
        assert sent.status_code == 202
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 3)
        eventually(lambda: client.get("/api/pulse").json()["providerTokenBudget"],
                   lambda b: b["trackedAdmissions"] == 1)
        assert assistant_count(view(client, target)) == 2
        assert client.get("/api/pulse").json()["budget"]["used"] == 1

    with client_for(tmp_path, pulse=profile, model_factory=fixture.factory) as client:
        budget = client.get("/api/pulse").json()["providerTokenBudget"]
        assert budget["charged"] == 40
        assert budget["remaining"] == 50
        assert budget["trackedAdmissions"] == 1
        assert assistant_count(view(client, target)) == 2
