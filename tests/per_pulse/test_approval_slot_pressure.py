"""Native 2+1 / 4+1 parked-approval regression for the single Pulse/Kernel lane.

Real World, Kernel, SQLite and original source-scoped approval actions.
Explicit scripted HTTP model replies are not real provider learning evidence.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

import per_pulse.service as page_service_module
from per_pulse.execution import PageExecution
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from test_execution import _new_page, _wait
from test_pulse_integration import client_for


@pytest.mark.parametrize("capacity", (2, 4))
def test_waiting_approvals_park_without_starving_new_work_or_replaying_approval(
    tmp_path, monkeypatch, capacity,
):
    """An unapproved original turn keeps causal ownership but not compute.

    The fifth Work runs without cancellation of any existing approval;
    a late original decision performs its exact effect only once.
    """
    monkeypatch.setenv("PER_PULSE_APPROVAL_PRESSURE_KEY", "explicit-test-credential")
    calls = []
    calls_lock = threading.Lock()

    def factory(scope):
        def respond(request):
            messages = json.loads(request.content)["messages"]
            has_result = any(message.get("role") == "tool" for message in messages)
            if has_result:
                delta, finish = (
                    {"content": "The previously approved operation is complete."}, "stop"
                )
            else:
                with calls_lock:
                    ordinal = len(calls)
                    calls.append((scope.context_id, ordinal))
                delta, finish = ({
                    "tool_calls": [{
                        "index": 0, "id": "fixture-review",
                        "type": "function",
                        "function": {
                            "name": "mutate_file",
                            "arguments": json.dumps({
                                "path": f"approval-pressure-{ordinal}.txt",
                                "expected_digest": None,
                                "content": f"Only written after explicit approval {ordinal}.\n",
                            }),
                        },
                    }],
                }, "tool_calls")
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=stream + "data: [DONE]\n\n",
            )

        return ChatHTTPModelAdapter(
            ChatHTTPConfig(
                "https://fixture.invalid/chat/completions",
                "approval-pressure-synthetic",
                credential_env="PER_PULSE_APPROVAL_PRESSURE_KEY",
            ),
            transport=httpx.MockTransport(respond),
        )

    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.02,
    )
    # Keep the real World/Kernel/SQLite construction but vary only the
    # runtime-owned execution capacity. This is not a simulated queue state.
    base_config = page_service_module.RuntimeServiceConfig
    monkeypatch.setattr(
        page_service_module, "RuntimeServiceConfig",
        lambda **kwargs: base_config(**kwargs, pulse_worker_capacity=capacity),
    )
    with client_for(
        tmp_path, pulse=settings, model_factory=factory,
        allow_workspace_actions=True,
    ) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        waiting = []
        for index in range(capacity):
            work = _new_page(client, f"WRITE_APPROVAL_PRESSURE_{index}")
            observed = _wait(
                execution, work,
                lambda state: state["status"] == "needs_attention"
                and len(state["pendingActions"]) == 1
                and state["pendingActions"][0]["canResolve"],
                timeout=12,
            )
            waiting.append((work, observed["turnId"], observed["pendingActions"][0]["actionId"]))

        # WAITING_APPROVAL is durable before the Kernel runner returns its
        # compute permit. Observe the separate, bounded parked population.
        deadline = time.monotonic() + 5
        while True:
            before = runtime.scheduling_snapshot()
            if before["capacity"]["worker_parked"] == capacity:
                break
            assert time.monotonic() < deadline, before
            time.sleep(0.02)
        assert before["capacity"]["worker_limit"] == capacity
        assert before["capacity"]["worker_running"] == 0
        assert before["capacity"]["worker_available"] > 0
        assert before["capacity"]["worker_attached"] == capacity
        assert len(calls) == capacity
        assert not list((tmp_path / "workspace").glob("approval-pressure-*.txt"))

        newcomer = _new_page(client, f"WRITE_APPROVAL_PRESSURE_{capacity}")
        admitted = _wait(
            execution, newcomer,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        assert admitted["turnId"]
        assert len(calls) == capacity + 1
        after = runtime.scheduling_snapshot()
        assert after["capacity"]["worker_limit"] == capacity
        assert after["capacity"]["worker_attached"] >= capacity + 1
        for old_work, old_turn, old_action in waiting:
            observed = execution.snapshot(old_work)
            assert observed["turnId"] == old_turn
            assert observed["status"] == "needs_attention"
            assert observed["pendingActions"][0]["actionId"] == old_action
        assert not list((tmp_path / "workspace").glob("approval-pressure-*.txt"))
        print(json.dumps({
            "transport": "TEST_DOUBLE",
            "computeLimit": capacity,
            "humanHeldBefore": before["capacity"]["worker_parked"],
            "computeRunningBefore": before["capacity"]["worker_running"],
            "newcomerAdmittedWithoutCancellation": bool(admitted["turnId"]),
            "modelRequests": len(calls),
        }, sort_keys=True))

        # A *late* user decision on one of the original waiting Works still
        # reaches its original durable Kernel action, not the newcomer.
        original_work, original_turn, original_action = waiting[1]
        result = execution.resolve_approval(original_work, original_action, "allow_once")
        assert result["accepted"] and not result["repeated"], result
        final = _wait(
            execution, original_work,
            lambda state: state["turnId"] == original_turn
            and state["status"] == "idle"
            and state["pendingActions"] == [],
            timeout=12,
        )
        assert final["turnId"] == original_turn
        changed = tmp_path / "workspace" / "approval-pressure-1.txt"
        assert changed.read_text() == "Only written after explicit approval 1.\n"
        repeated = execution.resolve_approval(original_work, original_action, "allow_once")
        assert repeated["accepted"] and repeated["repeated"]
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(original_work), original_action,
        )
        assert runtime.kernel_service.store.action_terminal_count(scope, original_action) == 1
        for other_work, other_turn, other_action in waiting[:1] + waiting[2:]:
            observed = execution.snapshot(other_work)
            assert observed["turnId"] == other_turn
            assert observed["status"] == "needs_attention"
            assert observed["pendingActions"][0]["actionId"] == other_action
        assert execution.snapshot(newcomer)["turnId"] == admitted["turnId"]
        assert sorted(path.name for path in (tmp_path / "workspace").glob("approval-pressure-*.txt")) == [
            "approval-pressure-1.txt",
        ]
