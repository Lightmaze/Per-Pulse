"""Physical process-death regression for a native PulseWorld ownership lease.

Unlike the logical-clock test, this exercises an actual OS-killed Python
process, a second SQLite connection and the real renewal heartbeat. It does
not simulate an active Kernel turn or assert that unfinished work was delivered.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from pulse_system.core.runtime.lease import RuntimeLeaseKeeper
from pulse_system.core.types import (
    RuntimeLeaseConflictError,
    RuntimeLeaseError,
    RuntimeLeaseState,
)
from pulse_system.substrate.storage import Storage


_CHILD = """
from pathlib import Path
import sys
import time
from pulse_system.core.runtime.lease import RuntimeLeaseKeeper
from pulse_system.substrate.storage import Storage

store = Storage(Path(sys.argv[1]))
keeper = RuntimeLeaseKeeper(
    store, ttl_sec=5, renew_interval_sec=1, owner_id="killed-worker",
)
Path(sys.argv[2]).write_text(str(keeper.epoch), encoding="ascii")
while True:
    time.sleep(60)
"""


def test_sigkill_reentry_waits_for_real_expiry_and_fences_old_epoch(tmp_path):
    db = tmp_path / "world.db"
    ready = tmp_path / "lease-ready"
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _CHILD, str(db), str(ready)],
        cwd=tmp_path, env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True,
    )
    storage = None
    successor = None
    third = None
    try:
        deadline = time.monotonic() + 15
        while not ready.is_file():
            if process.poll() is not None:
                _, errors = process.communicate(timeout=2)
                raise AssertionError(f"Lease owner failed before ready: {errors[-2000:]}")
            if time.monotonic() >= deadline:
                raise AssertionError("Child did not acquire a real lease within 15 seconds")
            time.sleep(0.05)

        original_epoch = int(ready.read_text(encoding="ascii"))
        storage = Storage(db)
        live = storage.get_runtime_lease()
        assert live is not None
        assert live.state is RuntimeLeaseState.ACTIVE
        assert live.owner_id == "killed-worker"
        assert live.epoch == original_epoch
        assert process.poll() is None
        with pytest.raises(RuntimeLeaseConflictError):
            RuntimeLeaseKeeper(
                storage, ttl_sec=5, renew_interval_sec=1,
                owner_id="premature-successor",
            )

        # The actual OS terminates the heartbeat and SQLite connection.
        # No Python finally block, close(release=False), or simulated clock
        # participates in the child's exit.
        process.kill()
        assert process.wait(timeout=5) != 0
        unreleased = storage.get_runtime_lease()
        assert unreleased is not None
        assert unreleased.state is RuntimeLeaseState.ACTIVE
        assert unreleased.epoch == original_epoch

        if datetime.now(timezone.utc) < unreleased.expires_at:
            with pytest.raises(RuntimeLeaseConflictError):
                RuntimeLeaseKeeper(
                    storage, ttl_sec=5, renew_interval_sec=1,
                    owner_id="too-early-after-kill",
                )

        # Wait for the *observed durable* expiration, not a guessed time
        # since the handshake: the child may have renewed just before death.
        wait = (unreleased.expires_at + timedelta(milliseconds=200)
                - datetime.now(timezone.utc)).total_seconds()
        assert wait < 7, "Lease expiry unexpectedly exceeds configured TTL"
        time.sleep(max(0, wait))
        successor = RuntimeLeaseKeeper(
            storage, ttl_sec=5, renew_interval_sec=1,
            owner_id="replacement-after-kill",
        )
        assert successor.epoch == original_epoch + 1
        assert successor.assert_owned().state is RuntimeLeaseState.ACTIVE

        # A late assertion or release from the killed process's identity
        # cannot mutate the new epoch.
        with pytest.raises(RuntimeLeaseError):
            storage.assert_runtime_lease(
                "killed-worker", original_epoch, now=datetime.now(timezone.utc),
            )
        with pytest.raises(RuntimeLeaseError):
            storage.release_runtime_lease(
                "killed-worker", original_epoch, now=datetime.now(timezone.utc),
            )
        assert storage.get_runtime_lease().owner_id == "replacement-after-kill"

        assert successor.close().state is RuntimeLeaseState.RELEASED
        successor = None
        third = RuntimeLeaseKeeper(
            storage, ttl_sec=5, renew_interval_sec=1,
            owner_id="clean-next-owner",
        )
        assert third.epoch == original_epoch + 2
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stderr is not None:
            process.stderr.close()
        if third is not None:
            third.close()
        if successor is not None:
            successor.close(release=False)
        if storage is not None:
            storage.close()
