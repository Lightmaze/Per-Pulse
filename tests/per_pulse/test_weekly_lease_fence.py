"""Separate SQLite connections and lease generations cannot lose admission.

HTTP replies are explicit fixtures. Copyright 2026 Lightmaze Contributors.
Licensed under Apache-2.0.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.errors import ProviderUnavailable
from pulse_system.core.causality.ledger import RuntimeFence
from pulse_system.core.types import RuntimeLeaseLostError
from pulse_system.service.weekly_requests import WeeklyRequestBudget, WeeklyRequestGate, WeeklyRequestUnavailable
from pulse_system.substrate.storage import Storage
from test_weekly_http_attempts import _response, _run


@pytest.fixture
def world(tmp_path):
    first, second = Storage(tmp_path / "world.db"), Storage(tmp_path / "world.db")
    lease = first.acquire_runtime_lease("old-owner", now=datetime.now(timezone.utc), ttl_sec=600)
    try:
        yield first, second, lease
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("limit", [1, 2])
def test_separate_connections_atomically_compete_without_losing_attempts(world, limit):
    first, second, lease = world
    fence = RuntimeFence(lease.owner_id, lease.epoch)
    budgets = [WeeklyRequestBudget(store, "world", fence=lambda: fence) for store in (first, second)]
    budgets[0].configure(enabled=True, weekly_limit=limit)
    with pytest.raises(RuntimeLeaseLostError):
        WeeklyRequestBudget(first, "world").configure(enabled=True, weekly_limit=limit)
    ready = Barrier(2)
    def attempt(budget):
        ready.wait(timeout=5)
        try:
            ticket = budget.reserve(source_event_id="SELF-root")
            budget.dispatch(ticket)
            return ticket
        except WeeklyRequestUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        tickets = list(pool.map(attempt, budgets))
    assert sum(ticket is not None for ticket in tickets) == limit
    status = budgets[1].status()
    assert status["attempts"] == status["consumed"] == status["states"]["dispatched"] == limit
    assert status["remaining"] == 0


def _adapter(budget, sent, owner, gate=None):
    def handler(_request):
        sent.append(owner)
        return _response()
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "lease-bound-fixture",
        credential_env="WEEKLY_TEST_KEY", max_retries=0),
        transport=httpx.MockTransport(handler),
        attempt_accounting=gate or WeeklyRequestGate(budget, lambda _scope: "SELF-root"))


def test_takeover_fences_old_wire_attempt_and_preserves_both_receipts(world, monkeypatch):
    first, second, lease = world
    monkeypatch.setenv("WEEKLY_TEST_KEY", "synthetic-fixture-credential")
    old_fence = RuntimeFence(lease.owner_id, lease.epoch)
    old = WeeklyRequestBudget(first, "world", fence=lambda: old_fence)
    old.configure(enabled=True, weekly_limit=1)
    unsent = old.reserve(source_event_id="SELF-root")
    first.release_runtime_lease(lease.owner_id, lease.epoch, now=datetime.now(timezone.utc))
    replacement = second.acquire_runtime_lease("new-owner", now=datetime.now(timezone.utc), ttl_sec=600)
    new_fence = RuntimeFence(replacement.owner_id, replacement.epoch)
    new = WeeklyRequestBudget(second, "world", fence=lambda: new_fence)
    assert new.reconcile() == 1
    with pytest.raises(RuntimeLeaseLostError):
        old.dispatch(unsent)
    with pytest.raises(RuntimeLeaseLostError):
        old.reconcile()
    sent, ready = [], Barrier(2)
    def send(item):
        budget, owner = item
        adapter = _adapter(budget, sent, owner)
        try:
            ready.wait(timeout=5)
            try:
                _run(adapter)
                return "sent"
            except RuntimeLeaseLostError:
                return "fenced"
        finally:
            adapter.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(send, [(old, "old-owner"), (new, "new-owner")]))
    assert outcomes == ["fenced", "sent"]
    assert sent == ["new-owner"]
    status = new.status()
    assert status["attempts"] == 2
    assert status["states"]["abandoned"] == status["states"]["completed"] == status["consumed"] == 1


def test_independent_pause_after_reserve_prevents_the_physical_send(world, monkeypatch):
    first, second, lease = world
    monkeypatch.setenv("WEEKLY_TEST_KEY", "synthetic-fixture-credential")
    fence = RuntimeFence(lease.owner_id, lease.epoch)
    budget = WeeklyRequestBudget(first, "world", fence=lambda: fence)
    control = WeeklyRequestBudget(second, "world", fence=lambda: fence)
    budget.configure(enabled=True, weekly_limit=1)
    class PausingGate(WeeklyRequestGate):
        def reserve(self, scope):
            ticket = super().reserve(scope)
            control.configure(enabled=False, weekly_limit=1)
            return ticket
    sent = []
    adapter = _adapter(budget, sent, "old-owner", PausingGate(budget, lambda _scope: "SELF-root"))
    try:
        with pytest.raises(ProviderUnavailable):
            _run(adapter)
        assert sent == []
        assert control.status()["consumed"] == control.status()["held"] == 0
        assert control.status()["states"]["abandoned"] == 1
    finally:
        adapter.close()


def test_expired_owner_cannot_commit_a_component_transaction(world, monkeypatch):
    first, _second, lease = world
    with pytest.raises(RuntimeLeaseLostError):
        with first.component_state_transaction(runtime_owner_id=lease.owner_id, runtime_lease_epoch=lease.epoch) as state:
            state.save_component_state("uncommitted-receipt", {"reserved": 1})
            monkeypatch.setattr("pulse_system.substrate.storage.store._now", lambda: lease.expires_at + timedelta(seconds=1))
    assert first.load_component_state("uncommitted-receipt") is None
