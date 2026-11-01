"""Concurrent Pulse budget observation must never see a half-settled turn.

This is an in-process allocator regression. It does not establish a durable
provider spending cap across crashes or verify real provider invoices.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from pulse_system.core.runtime.resource import RuntimeConfig, RuntimeManager, RuntimeStats


class _PausingStats(RuntimeStats):
    """Deterministically suspend one writer between its counter increments."""

    def __setattr__(self, name, value):
        if name == "total_input_tokens" and getattr(self, "_armed", False):
            self.entered.set()
            if not self.release.wait(5):
                raise AssertionError("budget test writer was never released")
        super().__setattr__(name, value)


def test_parallel_admission_and_snapshot_cannot_ignore_half_settled_usage():
    manager = RuntimeManager(RuntimeConfig(
        budget_per_tick=3, hourly_token_budget=4, daily_token_budget=4,
    ))
    stats = _PausingStats()
    stats.entered = threading.Event()
    stats.release = threading.Event()
    stats._armed = True
    manager._stats = stats

    def budget_read():
        started.set()
        return manager.get_budget()

    def stats_read():
        snapshot_started.set()
        return manager.snapshot()

    started = threading.Event()
    snapshot_started = threading.Event()
    with ThreadPoolExecutor(max_workers=3) as pool:
        writer = pool.submit(manager.consume_budget, 3, 2, 1, 1)
        assert stats.entered.wait(3), "did not reach the intentionally paused write"
        budget = pool.submit(budget_read)
        snapshot = pool.submit(stats_read)
        assert started.wait(3)
        assert snapshot_started.wait(3)
        try:
            # With the writer frozen mid-update, neither reader may see old
            # remaining capacity or inconsistent pulse/token counters.
            for reader in (budget, snapshot):
                try:
                    reader.result(timeout=0.1)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("reader escaped the budget settlement lock")
        finally:
            stats.release.set()
        writer.result(timeout=5)
        assert budget.result(timeout=5) == 0
        observed = snapshot.result(timeout=5)
    assert observed["total_pulses"] == 1
    assert observed["total_input_tokens"] == 3
    assert observed["total_output_tokens"] == 2
    assert observed["billable_tokens_this_hour"] == 4
    assert observed["billable_tokens_today"] == 4
    assert observed["hourly_budget_remaining"] == 0


def test_multiple_parallel_settlements_keep_one_consistent_total():
    manager = RuntimeManager(RuntimeConfig(
        budget_per_tick=5, hourly_token_budget=100_000,
        daily_token_budget=100_000,
    ))
    workers, increments = 8, 250
    start = threading.Barrier(workers)

    def settle():
        start.wait(timeout=5)
        for _ in range(increments):
            manager.consume_budget(3, 2, 1, 1)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(settle) for _ in range(workers)]
        for future in futures:
            future.result(timeout=20)
    observed = manager.snapshot()
    total = workers * increments
    assert observed["total_pulses"] == total
    assert observed["total_input_tokens"] == 3 * total
    assert observed["total_output_tokens"] == 2 * total
    assert observed["billable_tokens_this_hour"] == 4 * total
    assert observed["billable_tokens_today"] == 4 * total
