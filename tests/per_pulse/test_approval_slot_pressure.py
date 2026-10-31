"""Native 4+1 approval-pressure reproduction for the shared Pulse/Kernel lane.

This is an intentionally non-fixing regression: real World, Kernel, SQLite,
approved-action ownership and Pulse scheduling; explicitly scripted HTTP
model replies, not a real provider or learned behavior.
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
def test_unapproved_kernel_turns_occupy_pulse_capacity_until_one_is_cancelled(
    tmp_path, monkeypatch, capacity,
):
    """Observe the same native bottleneck at capacities two and four.

    A queued newcomer has no Kernel turn or model round. Cancellation is
    solely diagnostic, not a product fix; a remaining original approval
    must still act on its exact turn and perform its file effect only once.
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

        before = runtime.scheduling_snapshot()
        assert before["capacity"]["worker_limit"] == capacity
        assert before["capacity"]["worker_running"] == capacity
        assert before["capacity"]["worker_available"] == 0
        assert len(calls) == capacity
        assert not list((tmp_path / "workspace").glob("approval-pressure-*.txt"))

        newcomer = _new_page(client, f"WRITE_APPROVAL_PRESSURE_{capacity}")
        # A small bounded observation window captures the real pressure:
        # the new user request is durable, but no model execution slot exists.
        time.sleep(0.25)
        saturated = runtime.scheduling_snapshot()
        assert saturated["capacity"]["worker_running"] == capacity
        assert saturated["capacity"]["worker_available"] == 0
        assert len(calls) == capacity
        newcomer_before = execution.snapshot(newcomer)
        assert newcomer_before["pendingActions"] == []
        # A queued Work has no Kernel session yet, so the read-only page
        # execution projection legitimately reports "idle". That is not
        # evidence of a completed or admitted turn; the provider call count,
        # absence of a turn, and saturated native capacity prove the hold.
        assert newcomer_before["status"] == "idle"
        assert newcomer_before["sessionId"] is None
        assert newcomer_before["turnId"] is None
        print(json.dumps({
            "transport": "TEST_DOUBLE",
            "limit": before["capacity"]["worker_limit"],
            "held": len(waiting),
            "newcomerBefore": newcomer_before["status"],
            "runningBefore": before["capacity"]["worker_running"],
            "runningSaturated": saturated["capacity"]["worker_running"],
            "modelCallsBeforeRelease": len(calls),
        }, sort_keys=True))

        # Demonstrate capacity causality without granting any file action.
        # The first Work is explicitly cancelled: every other original
        # approval retains its source turn and action ID.
        work, turn, _action = waiting[0]
        stopped = execution.cancel(work, turn)
        assert stopped["accepted"], stopped
        admitted = _wait(
            execution, newcomer,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        assert admitted["turnId"]
        assert len(calls) == capacity + 1
        for other_work, other_turn, other_action in waiting[1:]:
            observed = execution.snapshot(other_work)
            assert observed["turnId"] == other_turn
            assert observed["status"] == "needs_attention"
            assert observed["pendingActions"][0]["actionId"] == other_action
        assert not list((tmp_path / "workspace").glob("approval-pressure-*.txt"))

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
        assert changed.read_text() == "Only written after explicit approval 1.\\n"
        repeated = execution.resolve_approval(original_work, original_action, "allow_once")
        assert repeated["accepted"] and repeated["repeated"]
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(original_work), original_action,
        )
        assert runtime.kernel_service.store.action_terminal_count(scope, original_action) == 1
        for other_work, other_turn, other_action in waiting[2:]:
            observed = execution.snapshot(other_work)
            assert observed["turnId"] == other_turn
            assert observed["status"] == "needs_attention"
            assert observed["pendingActions"][0]["actionId"] == other_action
        assert execution.snapshot(newcomer)["turnId"] == admitted["turnId"]
        assert sorted(path.name for path in (tmp_path / "workspace").glob("approval-pressure-*.txt")) == [
            "approval-pressure-1.txt",
        ]
