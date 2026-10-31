"""Stopping one human-held turn must free its parked slot without touching peers.

Uses the actual World, Kernel, SQLite, scheduler, file action and original
approval controls. Model responses are explicit local HTTP test doubles.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import time

import per_pulse.service as page_service_module
from per_pulse.execution import PageExecution
from per_pulse.service import PulseSettings
from test_execution import _factory, _new_page, _wait
from test_pulse_integration import client_for


def test_stop_one_of_three_attached_approvals_admits_next_work_without_replaying_peers(
    tmp_path, monkeypatch,
):
    """Bounded parked capacity must be recyclable by a scoped explicit Stop.

    At compute capacity one, the executor has one compute position plus two
    additional parked positions. THREE original human approvals fill those
    three physical threads. A FOURTH Work cannot enter yet. Stopping only
    the first original turn must admit the fourth, while the other two
    original approvals retain their identities and later effects.
    """
    requests = []
    base_config = page_service_module.RuntimeServiceConfig
    monkeypatch.setattr(
        page_service_module, "RuntimeServiceConfig",
        lambda **kwargs: base_config(**kwargs, pulse_worker_capacity=1),
    )
    with client_for(
        tmp_path,
        pulse=PulseSettings(
            enabled=True, spontaneous_rate=0, check_interval_seconds=0.02,
        ),
        model_factory=_factory(monkeypatch, requests),
        allow_workspace_actions=True,
    ) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)

        first = _new_page(client, "WRITE_ONE")
        original_first = _wait(
            execution, first,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        second = _new_page(client, "WRITE_ONE")
        original_second = _wait(
            execution, second,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        third_held = _new_page(client, "WRITE_ONE")
        original_third = _wait(
            execution, third_held,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        first_turn = original_first["turnId"]
        second_turn = original_second["turnId"]
        third_turn = original_third["turnId"]
        second_action = original_second["pendingActions"][0]["actionId"]
        assert first_turn and second_turn and third_turn
        assert len({first_turn, second_turn, third_turn}) == 3
        assert second_action != original_first["pendingActions"][0]["actionId"]

        deadline = time.monotonic() + 5
        while True:
            slots = runtime.scheduling_snapshot()["capacity"]
            if slots["worker_parked"] == 3:
                break
            assert time.monotonic() < deadline, slots
            time.sleep(0.025)
        assert slots["worker_limit"] == 1
        assert slots["worker_running"] == 0
        assert slots["worker_attached"] == 3
        assert slots["approval_parked_limit"] == 2

        queued = _new_page(client, "WRITE_TWO")
        time.sleep(0.3)
        assert execution.snapshot(queued)["turnId"] is None
        assert len(requests) == 3
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert not (tmp_path / "workspace" / "two.txt").exists()

        stopped = execution.cancel(first, first_turn)
        assert stopped["accepted"] and not stopped["repeated"]
        assert execution.cancel(first, first_turn)["repeated"]
        _wait(
            execution, first,
            lambda state: state["turnId"] == first_turn
            and state["status"] == "stopped"
            and not state["pendingActions"],
            timeout=12,
        )

        third_wait = _wait(
            execution, queued,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        assert third_wait["turnId"] not in {first_turn, second_turn, third_turn}
        peer = execution.snapshot(second)
        assert peer["turnId"] == second_turn
        assert peer["status"] == "needs_attention"
        assert peer["pendingActions"][0]["actionId"] == second_action
        still_waiting = execution.snapshot(third_held)
        assert still_waiting["turnId"] == third_turn
        assert still_waiting["status"] == "needs_attention"
        assert still_waiting["pendingActions"][0]["actionId"] == (
            original_third["pendingActions"][0]["actionId"]
        )
        assert len(requests) == 4
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert not (tmp_path / "workspace" / "two.txt").exists()

        approved = execution.resolve_approval(second, second_action, "allow_once")
        assert approved["accepted"] and not approved["repeated"]
        _wait(
            execution, second,
            lambda state: state["status"] == "idle"
            and state["turnId"] == second_turn
            and not state["pendingActions"],
            timeout=12,
        )
        assert (tmp_path / "workspace" / "one.txt").read_text() == (
            "Written by one.txt.\n"
        )
        assert not (tmp_path / "workspace" / "two.txt").exists()
        repeated = execution.resolve_approval(second, second_action, "allow_once")
        assert repeated["accepted"] and repeated["repeated"]
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(second), second_action,
        )
        assert runtime.kernel_service.store.action_terminal_count(
            scope, second_action,
        ) == 1
        assert execution.snapshot(first)["status"] == "stopped"
        assert execution.snapshot(third_held)["turnId"] == third_turn
        assert execution.snapshot(queued)["pendingActions"][0]["actionId"] == (
            third_wait["pendingActions"][0]["actionId"]
        )
