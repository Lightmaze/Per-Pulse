"""First post-rollover provider settlement must still count at Pulse admission.

The transport and timestamps here are synthetic controls over the allocator;
this does not prove actual provider billing or crash-safe budget reservation.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import pulse_system.core.runtime.resource as resource_module
from pulse_system.core.runtime.resource import RuntimeConfig, RuntimeManager


@pytest.mark.parametrize("read_before_settlement", [False, True])
def test_first_settlement_in_new_hour_is_not_erased_at_next_admission(
    monkeypatch, read_before_settlement,
):
    start = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(resource_module, "_now", lambda: start)
    allocator = RuntimeManager(RuntimeConfig(
        budget_per_tick=3, hourly_token_budget=10, daily_token_budget=100,
    ))
    allocator.consume_budget(2, 1)
    assert allocator.snapshot()["billable_tokens_this_hour"] == 3

    next_hour = start + timedelta(hours=1, seconds=1)
    monkeypatch.setattr(resource_module, "_now", lambda: next_hour)
    if read_before_settlement:
        assert allocator.snapshot()["billable_tokens_this_hour"] == 0

    # A completed provider call can settle before the scheduler's first
    # read in the new hour. The new hour must retain its actual 11 tokens.
    allocator.consume_budget(8, 3)
    observed = allocator.snapshot()
    assert observed["billable_tokens_this_hour"] == 11
    assert observed["billable_tokens_today"] == 14
    assert observed["total_input_tokens"] == 10
    assert observed["total_output_tokens"] == 4
    assert observed["hourly_budget_remaining"] == 0
    assert allocator.get_budget() == 0


def test_first_settlement_after_day_rollover_is_charged_to_new_day(monkeypatch):
    start = datetime(2026, 11, 1, 23, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(resource_module, "_now", lambda: start)
    allocator = RuntimeManager(RuntimeConfig(
        budget_per_tick=3, hourly_token_budget=100, daily_token_budget=5,
    ))
    allocator.consume_budget(2, 1)
    assert allocator.snapshot()["billable_tokens_today"] == 3

    next_day = start + timedelta(days=1, seconds=1)
    monkeypatch.setattr(resource_module, "_now", lambda: next_day)
    # No monitoring or admission read between the boundary and settlement.
    allocator.consume_budget(4, 2)
    observed = allocator.snapshot()
    assert observed["billable_tokens_today"] == 6
    assert observed["billable_tokens_this_hour"] == 6
    assert observed["daily_budget_remaining"] == 0
    assert observed["total_pulses"] == 2
    assert allocator.get_budget() == 0
