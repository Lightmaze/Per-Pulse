"""Stopping one human-held turn must free its parked slot without touching peers.

Uses the actual World, Kernel, SQLite, scheduler, file action and original
approval controls. Model responses are explicit local HTTP test doubles.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import time

import pytest

import per_pulse.service as page_service_module
from per_pulse.execution import PageExecution
from per_pulse.service import PulseSettings
from test_execution import _factory, _new_page, _wait
from test_pulse_integration import client_for


def test_three_detached_approvals_leave_foreground_capacity_and_keep_peer_authority(
    tmp_path, monkeypatch,
):
    """Three durable human approvals free every physical worker.

    A fourth Work must execute without cancelling an earlier approval.
    A later scoped Stop and a separate explicit approval still affect only
    the original Kernel actions, with one terminal file effect.
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
            if slots["worker_parked"] == 3 and slots["worker_attached"] == 0:
                break
            assert time.monotonic() < deadline, slots
            time.sleep(0.025)
        assert slots["worker_limit"] == 1
        assert slots["worker_running"] == 0
        assert slots["worker_attached"] == 0
        assert slots["worker_available"] == 1

        queued = _new_page(client, "WRITE_TWO")
        third_wait = _wait(
            execution, queued,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        assert third_wait["turnId"] not in {first_turn, second_turn, third_turn}
        assert len(requests) == 4
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


@pytest.mark.parametrize("capacity", (2, 4))
def test_full_approval_fleet_detaches_and_admits_foreground_without_release(
    tmp_path, monkeypatch, capacity,
):
    """At capacity 2/4, all original approvals remain live but detached.

    The newcomer must execute before Stop while every original turn/action
    remains source-scoped and no file effect is approved implicitly.
    """
    requests = []
    base_config = page_service_module.RuntimeServiceConfig
    monkeypatch.setattr(
        page_service_module, "RuntimeServiceConfig",
        lambda **kwargs: base_config(**kwargs, pulse_worker_capacity=capacity),
    )
    physical_limit = capacity + min(16, 2 * capacity)
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
        held = []
        for _ in range(physical_limit):
            work = _new_page(client, "WRITE_ONE")
            state = _wait(
                execution, work,
                lambda item: item["status"] == "needs_attention"
                and len(item["pendingActions"]) == 1
                and item["pendingActions"][0]["canResolve"],
                timeout=15,
            )
            held.append((
                work, state["turnId"], state["pendingActions"][0]["actionId"],
            ))
        assert len({turn for _, turn, _ in held}) == physical_limit
        assert len({action for _, _, action in held}) == physical_limit

        deadline = time.monotonic() + 6
        while True:
            slots = runtime.scheduling_snapshot()["capacity"]
            if slots["worker_parked"] == physical_limit and slots["worker_attached"] == 0:
                break
            assert time.monotonic() < deadline, slots
            time.sleep(0.025)
        assert slots["worker_limit"] == capacity
        assert slots["approval_parked_limit"] == physical_limit - capacity
        assert slots["worker_running"] == 0
        assert slots["worker_attached"] == 0
        assert slots["worker_available"] == capacity
        assert len(requests) == physical_limit

        incoming = _new_page(client, "WRITE_TWO")
        arrived = _wait(
            execution, incoming,
            lambda item: item["status"] == "needs_attention"
            and len(item["pendingActions"]) == 1
            and item["pendingActions"][0]["canResolve"],
            timeout=15,
        )
        assert arrived["turnId"] not in {turn for _, turn, _ in held}
        assert len(requests) == physical_limit + 1
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert not (tmp_path / "workspace" / "two.txt").exists()

        first, first_turn, _ = held[0]
        stopped = execution.cancel(first, first_turn)
        assert stopped["accepted"] and not stopped["repeated"]
        assert execution.cancel(first, first_turn)["repeated"]
        _wait(
            execution, first,
            lambda item: item["turnId"] == first_turn
            and item["status"] == "stopped"
            and not item["pendingActions"],
            timeout=15,
        )
        for peer, original_turn, original_action in held[1:]:
            current = execution.snapshot(peer)
            assert current["turnId"] == original_turn
            assert current["status"] == "needs_attention"
            assert current["pendingActions"][0]["actionId"] == original_action
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert not (tmp_path / "workspace" / "two.txt").exists()

        second, second_turn, second_action = held[1]
        accepted = execution.resolve_approval(second, second_action, "allow_once")
        assert accepted["accepted"] and not accepted["repeated"]
        _wait(
            execution, second,
            lambda item: item["turnId"] == second_turn
            and item["status"] == "idle"
            and not item["pendingActions"],
            timeout=15,
        )
        assert (tmp_path / "workspace" / "one.txt").read_text() == (
            "Written by one.txt.\n"
        )
        assert not (tmp_path / "workspace" / "two.txt").exists()
        assert execution.resolve_approval(second, second_action, "allow_once")["repeated"]
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(second), second_action,
        )
        assert runtime.kernel_service.store.action_terminal_count(
            scope, second_action,
        ) == 1
        for peer, original_turn, original_action in held[2:]:
            current = execution.snapshot(peer)
            assert current["turnId"] == original_turn
            assert current["status"] == "needs_attention"
            assert current["pendingActions"][0]["actionId"] == original_action
