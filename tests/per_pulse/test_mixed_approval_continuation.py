"""Approved file effects and later calls share one durable native continuation.

Model choices are explicit TEST_DOUBLEs. Approvals, tools, World publication,
attention admission, files and their persisted action records are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json

import httpx
import pytest

from per_pulse.service import PulseSettings
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.contracts import ActionStatus
from pulse_harness.core.hooks import HookDecision, HookResult, HookSet
from test_execution import _new_page, _wait
from test_pages import client_for, wait_for


NOTE = "Review the actual caller exit after the approved report is complete."


def factory(monkeypatch, requests):
    monkeypatch.setenv("PER_PULSE_MIXED_TEST_KEY", "explicit-synthetic-credential")
    calls = [
        ("first", "mutate_file", {"path": "first.txt", "expected_digest": None, "content": "First approved file.\n"}),
        ("reading", "reading", {"op": "open", "path": "source.txt"}),
        ("attention", "attention", {"op": "hold", "note": NOTE, "drive": .5}),
        ("second", "mutate_file", {"path": "second.txt", "expected_digest": None, "content": "Second approved file.\n"}),
        ("last-reading", "reading", {"op": "open", "path": "source.txt"}),
    ]

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if any(m["role"] == "tool" for m in body["messages"]):
            delta, finish = {"content": "The approved files and local review are complete."}, "stop"
        else:
            delta = {"tool_calls": [{"index": i, "id": identity, "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}
                for i, (identity, name, arguments) in enumerate(calls)]}
            finish = "tool_calls"
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")

    return lambda _: ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "mixed-continuation-fixture",
        credential_env="PER_PULSE_MIXED_TEST_KEY"), transport=httpx.MockTransport(handle))


def start(client, tmp_path):
    (tmp_path / "workspace" / "source.txt").write_text("An existing source, independent of either approved file.\n")
    runtime = client.app.state.pages.runtime
    runtime.context_pulse.set_paused(True)
    execution = PageExecution(runtime)
    page = _new_page(client, "Compare the source, write two files and retain the review.")
    waiting = _wait(execution, page, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
    return runtime, execution, page, waiting


@pytest.mark.parametrize("first_decision", ["allow_once", "deny"])
def test_mixed_calls_resume_in_order_and_each_file_keeps_its_approval(tmp_path, monkeypatch, first_decision):
    requests = []
    with client_for(tmp_path, model_factory=factory(monkeypatch, requests), allow_workspace_actions=True,
                    pulse=PulseSettings(enabled=True, spontaneous_rate=0)) as client:
        runtime, execution, page, first = start(client, tmp_path)
        seen = []

        def observe(context):
            seen.append(context.tool_call.tool_call_id)
            return HookResult(HookDecision.ALLOW)

        runtime.kernel_service._runtimes[first["sessionId"]].runner.hooks = HookSet(before_tool=[observe])
        first_action = first["pendingActions"][0]["actionId"]
        receipt = execution.resolve_approval(page, first_action, first_decision)
        assert receipt["continuationError"] is None, receipt
        second = _wait(execution, page, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        second_action = second["pendingActions"][0]["actionId"]
        assert second_action != first_action
        assert second["pendingActions"][0]["arguments"]["path"] == "second.txt"
        assert not (tmp_path / "workspace" / "second.txt").exists()
        center = runtime.world.get_task_front(page).center_id
        runtime.context_pulse.tick()
        assert runtime.context_pulse.inspect_attention(center)["retained"]["note"] == ""
        assert execution.resolve_approval(page, first_action, first_decision)["repeated"]
        assert len(requests) == 1
        assert execution.snapshot(page)["pendingActions"][0]["actionId"] == second_action

        finished = execution.resolve_approval(page, second_action, "allow_once")
        assert finished["continuationError"] is None, finished
        wait_for(client, page, lambda v: any(m["role"] == "assistant" and m["status"] == "settled" for m in v["messages"]))
        runtime.context_pulse.tick()
        retained = runtime.context_pulse.inspect_attention(center)["retained"]
        assert retained["note"] == NOTE and retained["sourceTurnId"] == first["turnId"]
        assert runtime.context_pulse.snapshot()["budget"]["used"] == 0
        assert (tmp_path / "workspace" / "first.txt").exists() is (first_decision == "allow_once")
        assert (tmp_path / "workspace" / "second.txt").read_text() == "Second approved file.\n"
        scope = runtime.kernel_service.store.action_scope_for_session(execution._scope(page), first_action)
        actions = runtime.kernel_service.store.actions_for_turn(scope)
        assert len(actions) == 5 and all(a.terminal and a.result_delivered for a in actions)
        assert all(a.status is ActionStatus.SUCCEEDED for a in actions[1:])
        for action in actions:
            assert runtime.kernel_service.store.action_terminal_count(scope, action.action_request_id) == 1
        tool_messages = [m for m in requests[-1]["messages"] if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_messages] == ["first", "reading", "attention", "second", "last-reading"]
        assert all(json.loads(m["content"])["status"] not in {"intent", "starting", "running"} for m in tool_messages)
        assert len(requests) == 2
        assert execution.resolve_approval(page, second_action, "allow_once")["repeated"]
        assert len(requests) == 2
        assert seen == (["first"] if first_decision == "allow_once" else []) + ["reading", "attention", "second", "last-reading"]


def test_stopping_the_second_approval_keeps_the_first_file_but_discards_staged_attention(tmp_path, monkeypatch):
    requests = []
    with client_for(tmp_path, model_factory=factory(monkeypatch, requests), allow_workspace_actions=True,
                    pulse=PulseSettings(enabled=True, spontaneous_rate=0)) as client:
        runtime, execution, page, first = start(client, tmp_path)
        action_id = first["pendingActions"][0]["actionId"]
        receipt = execution.resolve_approval(page, action_id, "allow_once")
        assert receipt["continuationError"] is None, receipt
        second = _wait(execution, page, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        assert second["pendingActions"][0]["arguments"]["path"] == "second.txt"
        assert execution.cancel(page, first["turnId"])["accepted"]
        wait_for(client, page, lambda v: all(m["status"] != "running" for m in v["messages"]))
        runtime.context_pulse.tick()
        center = runtime.world.get_task_front(page).center_id
        assert runtime.context_pulse.inspect_attention(center)["retained"]["note"] == ""
        assert (tmp_path / "workspace" / "first.txt").read_text() == "First approved file.\n"
        assert not (tmp_path / "workspace" / "second.txt").exists()
        assert execution.resolve_approval(page, action_id, "allow_once")["repeated"]
        assert len(requests) == 1


def test_resumed_unapproved_call_still_runs_the_before_tool_hook(tmp_path, monkeypatch):
    requests, seen = [], []
    with client_for(tmp_path, model_factory=factory(monkeypatch, requests), allow_workspace_actions=True,
                    pulse=PulseSettings(enabled=True, spontaneous_rate=0)) as client:
        runtime, execution, page, first = start(client, tmp_path)

        def guard(context):
            seen.append(context.tool_call.name)
            return HookResult(HookDecision.DENY if context.tool_call.name == "reading" else HookDecision.ALLOW)

        runner = runtime.kernel_service._runtimes[first["sessionId"]].runner
        runner.hooks = HookSet(before_tool=[guard])
        receipt = execution.resolve_approval(page, first["pendingActions"][0]["actionId"], "allow_once")
        assert receipt["continuationError"] is None, receipt
        _wait(execution, page, lambda p: p["status"] in {"error", "needs_attention"} and not p["pendingActions"])
        assert seen == ["mutate_file", "reading"]
        assert (tmp_path / "workspace" / "first.txt").read_text() == "First approved file.\n"
        assert not (tmp_path / "workspace" / "second.txt").exists()
        scope = execution._scope(page).for_turn(first["turnId"])
        actions = runtime.kernel_service.store.actions_for_turn(scope)
        assert [a.tool_name for a in actions] == ["mutate_file", "reading"]
        assert actions[1].status is ActionStatus.FAILED and actions[1].error_code == "hook_rejected"
        assert len(requests) == 1
