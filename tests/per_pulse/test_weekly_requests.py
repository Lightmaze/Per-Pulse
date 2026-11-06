"""Native World weekly provider request admission (no live model credentials)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import RLock

import pytest

from pulse_system.service.weekly_requests import WeeklyRequestBudget, WeeklyRequestUnavailable


class _Storage:
    """Minimal synchronized World component-state fixture."""
    def __init__(self):
        self._lock = RLock()
        self.values = {}

    def load_component_state(self, key):
        import copy
        return copy.deepcopy(self.values.get(key))

    def save_component_state(self, key, state):
        import copy
        self.values[key] = copy.deepcopy(state)


NOW = datetime(2026, 11, 5, 23, tzinfo=timezone.utc)


def test_disabled_until_explicitly_enabled_and_persists_between_instances():
    store = _Storage()
    ledger = WeeklyRequestBudget(store, "world-A")
    assert ledger.status(now=NOW)["enabled"] is False
    with pytest.raises(WeeklyRequestUnavailable):
        ledger.reserve(source_event_id="root", now=NOW)
    ledger.configure(enabled=True, weekly_limit=2)
    first = ledger.reserve(source_event_id="root", now=NOW)
    ledger.dispatch(first, now=NOW)
    # A crash between dispatch and settlement may already have billed the provider.
    replacement = WeeklyRequestBudget(store, "world-A")
    assert replacement.reconcile(now=NOW) == 1
    assert replacement.status(now=NOW)["states"]["uncertain"] == 1
    second = replacement.reserve(source_event_id="root", now=NOW)
    replacement.dispatch(second, now=NOW)
    replacement.settle(second, "completed", now=NOW)
    with pytest.raises(WeeklyRequestUnavailable):
        replacement.reserve(source_event_id="root", now=NOW)
    assert replacement.status(now=NOW)["consumed"] == 2


def test_retry_is_a_second_physical_attempt_and_cannot_refund_dispatch():
    ledger = WeeklyRequestBudget(_Storage(), "world")
    ledger.configure(enabled=True, weekly_limit=2)
    one = ledger.reserve(source_event_id="SELF-1", now=NOW)
    ledger.dispatch(one, now=NOW)
    ledger.settle(one, "rejected", now=NOW)
    two = ledger.reserve(source_event_id="SELF-1", now=NOW)
    ledger.dispatch(two, now=NOW)
    ledger.settle(two, "uncertain", now=NOW)
    ledger.settle(two, "uncertain", now=NOW)  # idempotent exact outcome
    with pytest.raises(ValueError):
        ledger.settle(two, "completed", now=NOW)
    with pytest.raises(ValueError):
        ledger.abandon(two, now=NOW)
    with pytest.raises(ValueError):
        ledger.dispatch(two, now=NOW)
    assert ledger.status(now=NOW)["remaining"] == 0


def test_pre_dispatch_abandon_and_week_reset_with_no_history_loss():
    store = _Storage()
    ledger = WeeklyRequestBudget(store, "world")
    ledger.configure(enabled=True, weekly_limit=1)
    one = ledger.reserve(source_event_id="SELF-1", now=NOW)
    ledger.abandon(one, now=NOW)
    assert ledger.status(now=NOW)["remaining"] == 1
    two = ledger.reserve(source_event_id="SELF-1", now=NOW)
    ledger.dispatch(two, now=NOW)
    monday = datetime(2026, 11, 9, tzinfo=timezone.utc)
    assert ledger.status(now=monday)["remaining"] == 1
    assert ledger.status(now=NOW)["consumed"] == 1


def test_atomic_admission_under_concurrent_dispatches():
    ledger = WeeklyRequestBudget(_Storage(), "world")
    ledger.configure(enabled=True, weekly_limit=9)

    def take(_):
        try:
            return ledger.reserve(source_event_id="SELF", now=NOW)
        except WeeklyRequestUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(take, range(40)))
    assert sum(item is not None for item in results) == 9
    assert ledger.status(now=NOW)["held"] == 9


def test_rejects_bad_limits_untrusted_source_and_foreign_ticket():
    store = _Storage()
    one = WeeklyRequestBudget(store, "world")
    other = WeeklyRequestBudget(store, "other")
    with pytest.raises(ValueError):
        one.configure(enabled=True, weekly_limit=0)
    with pytest.raises(ValueError):
        one.configure(enabled=True, weekly_limit=True)
    one.configure(enabled=True, weekly_limit=1)
    with pytest.raises(ValueError):
        one.reserve(source_event_id="", now=NOW)
    ticket = one.reserve(source_event_id="SELF", now=NOW)
    with pytest.raises(ValueError):
        other.dispatch(ticket, now=NOW)
    with pytest.raises(ValueError):
        one.dispatch(ticket, now=datetime(2026, 11, 5))


def test_gate_only_charges_host_resolved_autonomous_sources():
    from pulse_harness.core.errors import ProviderUnavailable
    from pulse_system.service.weekly_requests import WeeklyRequestGate

    budget = WeeklyRequestBudget(_Storage(), "world")
    budget.configure(enabled=True, weekly_limit=1)
    gate = WeeklyRequestGate(budget, lambda scope: "SELF-ROOT" if scope == "self" else None)
    assert gate.reserve("user") is None
    ticket = gate.reserve("self")
    gate.dispatch(ticket)
    gate.settle(ticket, "completed")
    with pytest.raises(ProviderUnavailable) as error:
        gate.reserve("self")
    assert error.value.code == "autonomous_weekly_requests_exhausted"


def test_pause_after_reservation_fences_dispatch_without_charging():
    from pulse_harness.core.errors import ProviderUnavailable
    from pulse_system.service.weekly_requests import WeeklyRequestGate

    budget = WeeklyRequestBudget(_Storage(), "world")
    budget.configure(enabled=True, weekly_limit=1)
    gate = WeeklyRequestGate(budget, lambda _scope: "SELF-root")
    ticket = gate.reserve("self")
    budget.configure(enabled=False, weekly_limit=1)
    with pytest.raises(ProviderUnavailable) as error:
        gate.dispatch(ticket)
    assert error.value.code == "autonomous_weekly_requests_paused"
    gate.abandon(ticket)
    assert budget.status(now=NOW)["consumed"] == 0
    budget.configure(enabled=True, weekly_limit=1)
    assert budget.status(now=NOW)["remaining"] == 1


def test_native_world_sqlite_survives_process_reopen(tmp_path):
    from pulse_system.substrate.storage import Storage

    world_path = tmp_path / "world.db"
    first_store = Storage(world_path)
    try:
        first = WeeklyRequestBudget(first_store, "world")
        first.configure(enabled=True, weekly_limit=2)
        ticket = first.reserve(source_event_id="SELF-root", now=NOW)
        first.dispatch(ticket, now=NOW)
        assert first.status(now=NOW)["consumed"] == 1
    finally:
        first_store.close()

    second_store = Storage(world_path)
    try:
        second = WeeklyRequestBudget(second_store, "world")
        assert second.reconcile(now=NOW) == 1
        status = second.status(now=NOW)
        assert status["enabled"] and status["states"]["uncertain"] == 1
        assert status["remaining"] == 1
    finally:
        second_store.close()


def test_trusted_world_ancestry_follows_autonomous_descendants_not_labels():
    from types import SimpleNamespace as Row
    from pulse_harness.core.scope import HarnessScope
    from pulse_harness.core.errors import ProviderUnavailable
    from pulse_system.core.types import CausalEventKind as Kind, CausalEventSource as Source
    from pulse_system.service.weekly_requests import world_source_for_scope

    class Ledger:
        def __init__(self, events, turn_event):
            self.events = {event.id: event for event in events}
            self.turn_event = turn_event

        def get_turn_for_world(self, turn_id, world_id):
            return Row(engram_id="engram", event_id=self.turn_event) if turn_id == "turn" and world_id == "world" else None

        def get_event(self, event_id):
            return self.events.get(event_id)

    scope = HarnessScope("world", "engram", "session", 0, "owner", turn_id="turn")
    user = Row(id="user", world_id="world", kind=Kind.STIMULUS,
               source=Source.USER, parent_event_id=None)
    self_root = Row(id="self", world_id="world", kind=Kind.SPONTANEOUS,
                    source=Source.SELF, parent_event_id=None)
    propagated = Row(id="propagated", world_id="world", kind=Kind.PROPAGATION,
                     source=Source.PROPAGATION, parent_event_id="self")
    delegated = Row(id="delegated", world_id="world", kind=Kind.DELEGATION_REQUEST,
                    source=Source.DELEGATION, parent_event_id="propagated")

    assert world_source_for_scope(Ledger([user], "user"), "world", scope) is None
    # An assistant result or tool receipt is SELF-authored *inside* the
    # original person-directed Work, not an autonomous activation by itself.
    result = Row(id="result", world_id="world", kind=Kind.ASSISTANT_RESULT,
                 source=Source.SELF, parent_event_id="user")
    tool = Row(id="tool", world_id="world", kind=Kind.TOOL_CALL,
               source=Source.SELF, parent_event_id="result")
    assert world_source_for_scope(Ledger([user, result, tool], "tool"),
                                  "world", scope) is None
    # A later native spontaneous reentry *is* a new autonomous origin, even
    # when it descends from a user-requested result.
    reentry = Row(id="reentry", world_id="world", kind=Kind.SPONTANEOUS,
                  source=Source.SELF, parent_event_id="result")
    assert world_source_for_scope(Ledger([user, result, reentry], "reentry"),
                                  "world", scope) == "reentry"
    # Reentering a prior autonomous context with an explicit new user
    # instruction resets the request pool, including after succession.
    # A subsequent SELF reactivation after that instruction spends the pool.
    new_user = Row(id="new-user", world_id="world", kind=Kind.STIMULUS,
                   source=Source.USER, parent_event_id="reentry")
    new_result = Row(id="new-result", world_id="world", kind=Kind.ASSISTANT_RESULT,
                     source=Source.SELF, parent_event_id="new-user")
    assert world_source_for_scope(
        Ledger([user, result, reentry, new_user, new_result], "new-result"),
        "world", scope,
    ) is None
    next_reentry = Row(id="next-reentry", world_id="world", kind=Kind.SPONTANEOUS,
                       source=Source.SELF, parent_event_id="new-result")
    assert world_source_for_scope(
        Ledger([user, result, reentry, new_user, new_result, next_reentry], "next-reentry"),
        "world", scope,
    ) == "next-reentry"
    assert world_source_for_scope(Ledger([self_root, propagated, delegated], "delegated"),
                                  "world", scope) == "self"
    delegated.parent_event_id = "user"
    assert world_source_for_scope(Ledger([user, delegated], "delegated"), "world", scope) is None
    delegated.parent_event_id = "missing"
    with pytest.raises(ProviderUnavailable) as failure:
        world_source_for_scope(Ledger([delegated], "delegated"), "world", scope)
    assert failure.value.code == "autonomous_request_source_unverified"
    delegated.parent_event_id = "delegated"
    with pytest.raises(ProviderUnavailable):
        world_source_for_scope(Ledger([delegated], "delegated"), "world", scope)


def test_lowered_weekly_limit_fences_reserved_tickets_at_dispatch():
    ledger = WeeklyRequestBudget(_Storage(), "world")
    ledger.configure(enabled=True, weekly_limit=3)
    one = ledger.reserve(source_event_id="SELF", now=NOW)
    two = ledger.reserve(source_event_id="SELF", now=NOW)
    three = ledger.reserve(source_event_id="SELF", now=NOW)

    # An already-held ticket is not a promise to spend after the user lowers
    # the allowance. Only the first physical send can still be admitted.
    ledger.configure(enabled=True, weekly_limit=1)
    ledger.dispatch(one, now=NOW)
    with pytest.raises(WeeklyRequestUnavailable, match="lowered"):
        ledger.dispatch(two, now=NOW)
    with pytest.raises(WeeklyRequestUnavailable, match="lowered"):
        ledger.dispatch(three, now=NOW)
    ledger.abandon(two, now=NOW)
    ledger.abandon(three, now=NOW)
    assert ledger.status(now=NOW)["consumed"] == 1
    assert ledger.status(now=NOW)["remaining"] == 0


def test_week_boundary_rejects_stale_reservation_before_http_send():
    ledger = WeeklyRequestBudget(_Storage(), "world")
    ledger.configure(enabled=True, weekly_limit=1)
    sunday = datetime(2026, 11, 8, 23, 59, 59, tzinfo=timezone.utc)
    monday = datetime(2026, 11, 9, 0, 0, 1, tzinfo=timezone.utc)
    stale = ledger.reserve(source_event_id="SELF", now=sunday)
    with pytest.raises(WeeklyRequestUnavailable, match="expired"):
        ledger.dispatch(stale, now=monday)
    ledger.abandon(stale, now=monday)
    assert ledger.status(now=sunday)["consumed"] == 0
    assert ledger.status(now=sunday)["held"] == 0
    fresh = ledger.reserve(source_event_id="SELF", now=monday)
    ledger.dispatch(fresh, now=monday)
    ledger.settle(fresh, "completed", now=monday)
    assert ledger.status(now=monday)["consumed"] == 1
