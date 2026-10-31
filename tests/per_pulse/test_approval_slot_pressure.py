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

from per_pulse.execution import PageExecution
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from test_execution import _new_page, _wait
from test_pulse_integration import client_for


def test_four_unapproved_kernel_turns_occupy_four_pulse_slots_until_one_is_cancelled(
    tmp_path, monkeypatch,
):
    """Prove the actual shared-capacity bottleneck before changing its ownership.

    A fifth Work must not be misrepresented as waiting for approval: it has
    not reached its first model round while all four native workers wait.
    Cancellation is a diagnostic release, *not* the intended product fix.
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
    with client_for(
        tmp_path, pulse=settings, model_factory=factory,
        allow_workspace_actions=True,
    ) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        waiting = []
        for index in range(4):
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
        assert before["capacity"]["worker_limit"] == 4
        assert before["capacity"]["worker_running"] == 4
        assert before["capacity"]["worker_available"] == 0
        assert len(calls) == 4
        assert not list((tmp_path / "workspace").glob("approval-pressure-*.txt"))

        fifth = _new_page(client, "WRITE_APPROVAL_PRESSURE_4")
        # A small bounded observation window captures the real pressure:
        # the fifth request is durable, but no model execution slot exists.
        time.sleep(0.25)
        saturated = runtime.scheduling_snapshot()
        assert saturated["capacity"]["worker_running"] == 4
        assert saturated["capacity"]["worker_available"] == 0
        assert len(calls) == 4
        fifth_before = execution.snapshot(fifth)
        assert fifth_before["pendingActions"] == []
        # A queued Work has no Kernel session yet, so the read-only page
        # execution projection legitimately reports "idle". That is not
        # evidence of a completed or admitted turn; the provider call count,
        # absence of a turn, and saturated native capacity prove the hold.
        assert fifth_before["status"] == "idle"
        assert fifth_before["sessionId"] is None
        assert fifth_before["turnId"] is None
        print(json.dumps({
            "transport": "TEST_DOUBLE",
            "limit": before["capacity"]["worker_limit"],
            "held": len(waiting),
            "fifthBefore": fifth_before["status"],
            "runningBefore": before["capacity"]["worker_running"],
            "runningSaturated": saturated["capacity"]["worker_running"],
            "modelCallsBeforeRelease": len(calls),
        }, sort_keys=True))

        # Demonstrate capacity causality without granting any file action.
        # The first Work is explicitly cancelled: the other three remain
        # in their original WAITING_APPROVAL turns, with unchanged action IDs.
        work, turn, _action = waiting[0]
        stopped = execution.cancel(work, turn)
        assert stopped["accepted"], stopped
        admitted = _wait(
            execution, fifth,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1
            and state["pendingActions"][0]["canResolve"],
            timeout=12,
        )
        assert admitted["turnId"]
        assert len(calls) == 5
        for other_work, other_turn, other_action in waiting[1:]:
            observed = execution.snapshot(other_work)
            assert observed["turnId"] == other_turn
            assert observed["status"] == "needs_attention"
            assert observed["pendingActions"][0]["actionId"] == other_action
        assert not list((tmp_path / "workspace").glob("approval-pressure-*.txt"))
