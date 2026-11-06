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
