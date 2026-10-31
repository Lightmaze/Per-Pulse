"""A parked native approval must not revive after a World restart.

This exercises the same Pulse compute-parking route as the 4+1 admission
regression, but uses a real service stop and a fresh service against the
same World/Kernel/SQLite files. Model answers are explicit HTTP test doubles,
not real provider evidence.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import time

import per_pulse.service as page_service_module
import pytest
from per_pulse.execution import ExecutionError, PageExecution
from per_pulse.service import PulseSettings
from test_execution import _factory, _new_page, _wait
from test_pulse_integration import client_for


def test_shutdown_releases_parked_approval_without_replaying_original_action(
    tmp_path, monkeypatch,
):
    """A reopened subject may work; a stale approval cannot authorize it."""
    requests = []
    factory = _factory(monkeypatch, requests)
    base_config = page_service_module.RuntimeServiceConfig
    monkeypatch.setattr(
        page_service_module, "RuntimeServiceConfig",
        lambda **kwargs: base_config(**kwargs, pulse_worker_capacity=1),
    )
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.02,
    )

    with client_for(
        tmp_path, pulse=settings, model_factory=factory,
        allow_workspace_actions=True,
    ) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        old_work = _new_page(client, "WRITE_ONE")
        pending = _wait(
            execution, old_work,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        old_turn = pending["turnId"]
        old_action = pending["pendingActions"][0]["actionId"]
        deadline = time.monotonic() + 5
        while runtime.scheduling_snapshot()["capacity"]["worker_parked"] != 1:
            assert time.monotonic() < deadline
            time.sleep(0.025)
        assert runtime.scheduling_snapshot()["capacity"]["worker_running"] == 0
        assert not (tmp_path / "workspace" / "one.txt").exists()

    old_calls = len(requests)
    assert old_calls == 1
    with client_for(
        tmp_path, pulse=settings, model_factory=factory,
        allow_workspace_actions=True,
    ) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        stale = execution.snapshot(old_work)
        assert stale["turnId"] == old_turn
        assert not any(action["canResolve"] for action in stale["pendingActions"])
        with pytest.raises(ExecutionError):
            execution.resolve_approval(old_work, old_action, "allow_once")
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert len(requests) == old_calls

        # The old parked thread belongs to the previous process. It cannot
        # consume the new process's compute admission or approve another Work.
        fresh = _new_page(client, "WRITE_TWO")
        current = _wait(
            execution, fresh,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        assert current["turnId"] != old_turn
        assert current["pendingActions"][0]["actionId"] != old_action
        assert len(requests) == old_calls + 1
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert not (tmp_path / "workspace" / "two.txt").exists()
        action = current["pendingActions"][0]["actionId"]
        decision = execution.resolve_approval(fresh, action, "allow_once")
        assert decision["accepted"] and not decision["repeated"]
        _wait(
            execution, fresh,
            lambda state: state["turnId"] == current["turnId"]
            and state["status"] == "idle"
            and not state["pendingActions"],
            timeout=12,
        )
        assert (tmp_path / "workspace" / "two.txt").read_text() == (
            "Written by two.txt.\n"
        )
        repeated = execution.resolve_approval(fresh, action, "allow_once")
        assert repeated["accepted"] and repeated["repeated"]
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(fresh), action,
        )
        assert runtime.kernel_service.store.action_terminal_count(
            scope, action,
        ) == 1
        assert not (tmp_path / "workspace" / "one.txt").exists()
