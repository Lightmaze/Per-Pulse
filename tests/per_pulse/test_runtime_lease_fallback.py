"""Durable runtime lease recovery after a non-releasing owner exits.

The test uses the actual SQLite Storage and RuntimeLeaseKeeper, not a fake
lease ledger. A stopped heartbeat represents confirmed owner-process exit;
the controlled clock avoids waiting five wall-clock seconds in CI. This is
a lease-boundary test, not a substitute for the native browser restart trip.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import threading

import pytest

from pulse_system.core.runtime.lease import RuntimeLeaseKeeper
from pulse_system.core.types import (
    RuntimeLeaseConflictError,
    RuntimeLeaseError,
    RuntimeLeaseLostError,
    RuntimeLeaseState,
)
from pulse_system.substrate.storage import Storage


def test_unreleased_owner_requires_expiry_before_successor_can_open(tmp_path):
    current = [datetime(2026, 10, 28, tzinfo=timezone.utc)]

    def clock():
        return current[0]

    db = tmp_path / "world.db"
    prior_storage = Storage(db)
    successor_storage = Storage(db)
    prior = None
    successor = None
    final = None
    try:
        prior = RuntimeLeaseKeeper(
            prior_storage, ttl_sec=5, renew_interval_sec=1,
            owner_id="prior-process", clock=clock,
        )
        first_epoch = prior.epoch
        active = prior_storage.get_runtime_lease()
        assert active is not None
        assert active.state is RuntimeLeaseState.ACTIVE
        assert active.owner_id == "prior-process"

        # Even a distinct connection or a reused process name cannot steal a
        # live epoch. A constructor conflict must not create a heartbeat.
        with pytest.raises(RuntimeLeaseConflictError) as live_conflict:
            RuntimeLeaseKeeper(
                successor_storage, ttl_sec=5, renew_interval_sec=1,
                owner_id="new-process", clock=clock,
            )
        assert live_conflict.value.lease.epoch == first_epoch
        assert successor_storage.get_runtime_lease().owner_id == "prior-process"

        # An uncooperative / escaped owner does not receive a clean release.
        # Only after the heartbeat has stopped can the clock move forward;
        # the prior Storage connection is then closed as on process exit.
        stopped = prior.close(release=False)
        assert stopped.state is RuntimeLeaseState.ACTIVE
        prior_storage.close()
        prior_storage = None
        prior = None
        with pytest.raises(RuntimeLeaseConflictError):
            RuntimeLeaseKeeper(
                successor_storage, ttl_sec=5, renew_interval_sec=1,
                owner_id="new-process", clock=clock,
            )
        # At the exact expiry boundary, a *new* epoch may be acquired.
        current[0] += timedelta(seconds=5)
        successor = RuntimeLeaseKeeper(
            successor_storage, ttl_sec=5, renew_interval_sec=1,
            owner_id="new-process", clock=clock,
        )
        assert successor.epoch == first_epoch + 1
        assert successor.assert_owned().owner_id == "new-process"

        # No stale owner may publish or release the successor's fencing epoch.
        with pytest.raises(RuntimeLeaseError):
            successor_storage.assert_runtime_lease(
                "prior-process", first_epoch, now=clock(),
            )
        with pytest.raises(RuntimeLeaseError):
            successor_storage.release_runtime_lease(
                "prior-process", first_epoch, now=clock(),
            )
        assert successor.assert_owned().epoch == first_epoch + 1

        # Cooperative close is different: it releases promptly, so a third
        # owner must not wait out another five-second expiry window.
        released = successor.close()
        assert released.state is RuntimeLeaseState.RELEASED
        successor = None
        final = RuntimeLeaseKeeper(
            successor_storage, ttl_sec=5, renew_interval_sec=1,
            owner_id="third-process", clock=clock,
        )
        assert final.epoch == first_epoch + 2
        assert final.assert_owned().owner_id == "third-process"
    finally:
        if final is not None:
            final.close()
        if successor is not None:
            successor.close(release=False)
        if prior is not None:
            prior.close(release=False)
        if prior_storage is not None:
            prior_storage.close()
        successor_storage.close()


class _OneRenewalFailure:
    """Inject the Windows failure shape around actual durable SQLite renewal."""

    def __init__(self, backing: Storage, error: sqlite3.OperationalError):
        self.backing = backing
        self.error = error
        self.failed = threading.Event()
        self.recovered = threading.Event()
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.backing, name)

    def renew_runtime_lease(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.failed.set()
            raise self.error
        renewed = self.backing.renew_runtime_lease(*args, **kwargs)
        self.recovered.set()
        return renewed


def test_transient_sqlite_busy_during_heartbeat_keeps_same_durable_owner(tmp_path):
    """One busy renewal must recover before TTL, without replacing the epoch."""
    storage = Storage(tmp_path / "world.db")
    shim = _OneRenewalFailure(storage, sqlite3.OperationalError("database is locked"))
    keeper = RuntimeLeaseKeeper(
        shim, ttl_sec=10, renew_interval_sec=1, owner_id="owner",
    )
    try:
        epoch = keeper.epoch
        assert shim.failed.wait(3), "heartbeat did not attempt renewal"
        assert shim.recovered.wait(3), "heartbeat did not retry a busy database"
        assert keeper.health().healthy
        assert keeper.assert_owned().epoch == epoch
        durable = storage.get_runtime_lease()
        assert durable is not None and durable.owner_id == "owner"
        assert durable.epoch == epoch
    finally:
        keeper.close()
        storage.close()


def test_non_busy_sqlite_renewal_failure_still_fences_runtime(tmp_path):
    """An unreadable database must not be treated as transient contention."""
    storage = Storage(tmp_path / "world.db")
    shim = _OneRenewalFailure(storage, sqlite3.OperationalError("disk I/O error"))
    lost = threading.Event()
    keeper = RuntimeLeaseKeeper(
        shim, ttl_sec=10, renew_interval_sec=1, owner_id="owner",
        on_lost=lambda _error: lost.set(),
    )
    try:
        assert shim.failed.wait(3)
        assert lost.wait(3)
        assert not keeper.health().healthy
        assert not shim.recovered.is_set()
        with pytest.raises(RuntimeLeaseLostError):
            keeper.assert_owned()
        durable = storage.get_runtime_lease()
        assert durable is not None and durable.state is RuntimeLeaseState.ACTIVE
    finally:
        keeper.close(release=False)
        storage.close()
