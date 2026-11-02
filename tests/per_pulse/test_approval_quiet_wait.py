"""Long-lived human approvals must not hot-poll the durable Kernel.

The real World/Kernel/SQLite and explicit synthetic HTTP model preserve the
original action, exact scope, and explicit user decision. This verifies idle
CPU/SQLite polling behavior, NOT physical thread-free parking or model quality.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import threading
import time

from per_pulse.execution import PageExecution
from per_pulse.service import PulseSettings
from test_execution import _factory, _new_page, _wait
from test_pulse_integration import client_for


def test_human_approval_waits_quietly_and_wakes_on_original_denial(tmp_path, monkeypatch):
    requests = []
    with client_for(
        tmp_path,
        pulse=PulseSettings(enabled=True, spontaneous_rate=0, check_interval_seconds=0.02),
        model_factory=_factory(monkeypatch, requests),
        allow_workspace_actions=True,
    ) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        work = _new_page(client, "WRITE_ONE")
        waiting = _wait(
            execution, work,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        harness = runtime.harness
        with harness._lock:
            scopes = tuple(harness._active.values())
        assert len(scopes) == 1
        scope = scopes[0]
        runner = harness.kernel._runtimes[scope.session_id].runner
        original_outcome = runner._outcome
        lock = threading.Lock()
        observations = [0]

        def count_outcome(*args, **kwargs):
            with lock:
                observations[0] += 1
            return original_outcome(*args, **kwargs)

        monkeypatch.setattr(runner, "_outcome", count_outcome)
        # The former sleep(0.025) loop produced about 30+ SQLite reads
        # in this interval. A notified, bounded wait should stay below 8.
        time.sleep(0.75)
        with lock:
            reads = observations[0]
        assert reads <= 8, f"human-held Kernel busy-polled {reads} times"
        assert execution.snapshot(work)["pendingActions"][0]["actionId"] == (
            waiting["pendingActions"][0]["actionId"]
        )
        assert not (tmp_path / "workspace" / "one.txt").exists()

        start = time.monotonic()
        receipt = execution.resolve_approval(
            work, waiting["pendingActions"][0]["actionId"], "deny",
        )
        assert receipt["accepted"] and not receipt["repeated"]
        settled = _wait(
            execution, work,
            lambda state: state["turnId"] == waiting["turnId"]
            and state["status"] in {"idle", "error", "stopped"}
            and not state["pendingActions"],
            timeout=8,
        )
        assert time.monotonic() - start < 5
        assert settled["turnId"] == waiting["turnId"]
        assert not (tmp_path / "workspace" / "one.txt").exists()
