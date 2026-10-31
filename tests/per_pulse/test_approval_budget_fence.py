"""Native approval-held spend must not disappear from Pulse admission.

All integration replies are explicit local HTTP test transport, not evidence
of paid provider accuracy or continuous learning.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import threading
import time

import httpx

import per_pulse.service as page_service_module
from per_pulse.execution import PageExecution
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.core.runtime.resource import RuntimeConfig, RuntimeManager
from test_execution import _new_page, _wait
from test_pulse_integration import client_for


def test_pending_provider_usage_is_an_admission_debit_not_a_double_charge():
    manager = RuntimeManager(RuntimeConfig(
        budget_per_tick=3, hourly_token_budget=100, daily_token_budget=100,
    ))
    assert manager.get_budget() == 3
    assert manager.get_budget(unsettled_provider_usage=(100, 0, 0)) == 0
    assert manager.get_budget(unsettled_provider_usage=None) == 0
    assert manager.snapshot()["billable_tokens_this_hour"] == 0
    manager.consume_budget(20, 0)
    assert manager.get_budget(unsettled_provider_usage=(80, 0, 0)) == 0
    assert manager.get_budget(unsettled_provider_usage=(20, 0, 0)) > 0
    # The still-held round is charged once, by its own final causal Future.
    assert manager.snapshot()["billable_tokens_this_hour"] == 20


def test_native_approval_completed_provider_usage_blocks_extra_admission(
    tmp_path, monkeypatch,
):
    """An attached approval with 100 durable reported tokens exhausts 100."""
    monkeypatch.setenv("PER_PULSE_BUDGET_FENCE_KEY", "explicit-test-credential")
    calls = []
    call_lock = threading.Lock()

    def factory(scope):
        def respond(request):
            messages = json.loads(request.content)["messages"]
            continued = any(item.get("role") == "tool" for item in messages)
            if not continued:
                with call_lock:
                    ordinal = len(calls)
                    calls.append(scope.context_id)
                delta, finish = ({
                    "tool_calls": [{
                        "index": 0, "id": "approval-budget-fixture",
                        "type": "function",
                        "function": {
                            "name": "mutate_file",
                            "arguments": json.dumps({
                                "path": f"approval-budget-{ordinal}.txt",
                                "expected_digest": None,
                                "content": "Only after explicit approval.\n",
                            }),
                        },
                    }],
                }, "tool_calls")
            else:
                delta, finish = ({"content": "Approved file completed."}, "stop")
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                {"choices": [], "usage": {
                    "prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100,
                }},
            ]
            stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=stream + "data: [DONE]\n\n",
            )

        return ChatHTTPModelAdapter(
            ChatHTTPConfig(
                "https://fixture.invalid/chat/completions", "budget-fence-synthetic",
                credential_env="PER_PULSE_BUDGET_FENCE_KEY",
            ), transport=httpx.MockTransport(respond),
        )

    config = page_service_module.RuntimeServiceConfig
    monkeypatch.setattr(
        page_service_module, "RuntimeServiceConfig",
        lambda **kwargs: config(
            **kwargs, pulse_worker_capacity=1, hourly_token_budget=100,
        ),
    )
    with client_for(
        tmp_path, pulse=PulseSettings(
            enabled=True, spontaneous_rate=0, check_interval_seconds=0.02,
        ),
        model_factory=factory, allow_workspace_actions=True,
    ) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        first = _new_page(client, "APPROVAL_BUDGET_FIRST")
        original = _wait(
            execution, first,
            lambda state: state["status"] == "needs_attention"
            and len(state["pendingActions"]) == 1,
            timeout=12,
        )
        assert original["pendingActions"][0]["actionId"]
        deadline = time.monotonic() + 5
        while runtime.scheduling_snapshot()["capacity"]["worker_parked"] != 1:
            assert time.monotonic() < deadline
            time.sleep(0.02)

        second = _new_page(client, "APPROVAL_BUDGET_SECOND")
        time.sleep(0.4)
        with call_lock:
            assert len(calls) == 1
        state = execution.snapshot(second)
        assert state["sessionId"] is None and state["turnId"] is None
        assert execution.snapshot(first)["pendingActions"][0]["actionId"] == (
            original["pendingActions"][0]["actionId"]
        )
        assert not (tmp_path / "workspace" / "approval-budget-0.txt").exists()

        # A late human decision is still executed by the ORIGINAL Kernel turn.
        # Parking only releases compute capacity: it must not defer the action
        # to the queued Work or waive its original approval requirement.
        decision = execution.resolve_approval(
            first, original["pendingActions"][0]["actionId"], "allow_once",
        )
        assert decision["accepted"] and not decision["repeated"]
        _wait(
            execution, first,
            lambda state: state["status"] == "idle"
            and state["turnId"] == original["turnId"]
            and not state["pendingActions"],
            timeout=12,
        )
        assert (tmp_path / "workspace" / "approval-budget-0.txt").read_text() == (
            "Only after explicit approval.\n"
        )
        repeated = execution.resolve_approval(
            first, original["pendingActions"][0]["actionId"], "allow_once",
        )
        assert repeated["accepted"] and repeated["repeated"]
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(first), original["pendingActions"][0]["actionId"],
        )
        assert runtime.kernel_service.store.action_terminal_count(
            scope, original["pendingActions"][0]["actionId"],
        ) == 1

        # Both 100-token provider rounds now settle through the sole original
        # Pulse Future. The temporary admission debit must not be charged a
        # second time when that Future finishes.
        deadline = time.monotonic() + 5
        while runtime._engine._runtime.snapshot()["total_pulses"] < 1:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        settled = runtime._engine._runtime.snapshot()
        assert settled["total_pulses"] == 1
        assert settled["billable_tokens_this_hour"] == 200
        assert settled["billable_tokens_today"] == 200
        assert runtime._engine._runtime.get_budget() == 0
        with call_lock:
            assert len(calls) == 1
        assert execution.snapshot(second)["turnId"] is None
