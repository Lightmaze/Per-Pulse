"""Native succession must retain each original conversation's own history.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The HTTP provider is explicitly synthetic; World, Kernel, generation and
restart are real. This checks request boundaries, not model quality.
"""
from __future__ import annotations

import json

import httpx
import pytest

from per_pulse.service import PageService
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


@pytest.mark.parametrize("summary_page", ["home", "source"])
@pytest.mark.parametrize("first_successor_page", ["home", "source", "other"])
def test_native_succession_keeps_summary_and_successor_inputs_in_context(
    tmp_path, monkeypatch, summary_page, first_successor_page,
):
    monkeypatch.setenv("GENERATION_CONTEXT_KEY", "explicit-synthetic-fixture")
    markers = {"home": "HOME_PRIVATE_ORIGINAL", "source": "SOURCE_PRIVATE_ORIGINAL",
               "other": "OTHER_PRIVATE_ORIGINAL"}
    contexts = {"home": ""}
    captured = []
    stage = {"value": "initial"}

    def factory(scope):
        def respond(request):
            messages = json.loads(request.content)["messages"]
            name = next(name for name, context in contexts.items() if context == scope.context_id)
            captured.append((name, scope, stage["value"], messages))
            current = messages[max(i for i, row in enumerate(messages) if row["role"] == "user"):]
            tools = [row for row in current if row["role"] == "tool"]
            tool_name, arguments = None, None
            if stage["value"] in {"successor", "restart"}:
                if not tools:
                    # Browse without sending any old marker in the query: the
                    # Kernel must retrieve the real original scoped history.
                    tool_name, arguments = "search_history", {"query": "", "limit": 10}
                elif len(tools) == 1:
                    result = json.loads(tools[0]["content"])
                    assert result["status"] == "succeeded", result
                    matches = result["output"]["matches"]
                    original = next(row for row in matches
                                    if row["source"]["engram_id"] == predecessor
                                    and row["source"]["role"] == "user"
                                    and row["excerpt"] == markers[name])
                    tool_name, arguments = "read_history", {"message_id": original["source"]["message_id"]}
                else:
                    result = json.loads(tools[-1]["content"])
                    assert result["status"] == "succeeded", result
                    assert result["output"]["content"] == markers[name]
                    assert result["output"]["source"]["engram_id"] == predecessor
            # Even a summary is explicitly local. Echoing its own marker also
            # detects a global successor seed injected into a different Work.
            delta = {"content": f"{markers[name]}: {stage['value']} synthetic result."}
            if tool_name:
                delta = {"tool_calls": [{"index": 0, "id": f"history-{len(captured)}", "type": "function",
                                        "function": {"name": tool_name, "arguments": json.dumps(arguments)}}]}
            payload = {"choices": [{"index": 0, "delta": delta,
                                    "finish_reason": "tool_calls" if tool_name else "stop"}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "generation-context-fixture",
            credential_env="GENERATION_CONTEXT_KEY",
        ), transport=httpx.MockTransport(respond))

    pages = PageService(tmp_path, model_factory=factory)
    page_ids = {"home": "home"}
    original_ids = {}
    results = {}

    def execute(name, event_id):
        runtime = pages.runtime
        return runtime._mgr.pulse(
            runtime.continuity_engram_id,
            pulse_event=runtime.causal_ledger.get_event(event_id),
            execution_context=contexts[name] or "home",
            runtime_fence=runtime._current_runtime_fence(),
        )

    try:
        original_ids["home"] = pages.send("home", markers["home"])["messageId"]
        for name in ("source", "other"):
            created = pages.create_page(name.title(), markers[name])
            page_ids[name] = created["page"]["id"]
            original_ids[name] = created["initialMessageId"]
            contexts[name] = "work:" + pages.runtime.world.get_task_front(page_ids[name]).center_id
        for name in markers:
            results[name] = execute(name, original_ids[name])

        runtime = pages.runtime
        predecessor = runtime.continuity_engram_id
        stage["value"] = "summary"
        preparation = runtime._mgr.prepare_succession(
            predecessor, parent_event_id=results[summary_page].result_event_id,
            runtime_fence=runtime._current_runtime_fence(),
        )
        summary_turn = runtime.causal_ledger.get_turn_for_world(preparation.summary_turn_id, runtime.world_id)
        successor = runtime._mgr.commit_succession(
            preparation, runtime_fence=runtime._current_runtime_fence(),
        ).new_id
        assert successor != predecessor
        stage["value"] = "successor"
        order = [first_successor_page] + [name for name in markers if name != first_successor_page]
        for name in order:
            execute(name, pages.send(page_ids[name], "Continue this original conversation.")["messageId"])
            pages._check_message(page_ids[name], original_ids[name])

        assert pages.runtime.close().clean
        pages = PageService(tmp_path, model_factory=factory)
        assert pages.runtime.continuity_engram_id == successor
        stage["value"] = "restart"
        for name in order:
            execute(name, pages.send(page_ids[name], "Continue after restart.")["messageId"])

        violations = []
        for name, scope, request_stage, messages in captured:
            text = json.dumps(messages)
            if request_stage in {"initial", "summary"}:
                assert markers[name] in text, (name, request_stage, "own history missing")
            foreign = [marker for key, marker in markers.items() if key != name and marker in text]
            if foreign:
                violations.append((name, request_stage, foreign))
            assert scope.engram_id == (predecessor if request_stage in {"initial", "summary"} else successor)
        assert not violations, violations
        assert len(captured) == 22
        assert summary_turn.execution_context == (contexts[summary_page] or "home")
    finally:
        pages.runtime.close()


def test_native_internal_admission_derives_context_before_claiming_a_turn(tmp_path, monkeypatch):
    from test_pulse_integration import TransportFixture

    fixture = TransportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    runtime = pages.runtime
    try:
        created = pages.create_page("Original Work", "A_LOCAL_HISTORY")
        event_id = created["initialMessageId"]
        event = runtime.causal_ledger.get_event(event_id)
        with pytest.raises(ValueError, match="execution context differs"):
            runtime._mgr.pulse(runtime.continuity_engram_id, pulse_event=event,
                               execution_context="home", runtime_fence=runtime._current_runtime_fence())
        assert runtime.causal_ledger.get_event(event_id).status.value == "queued"
        assert fixture.requests == []
        assert runtime.kernel_service.store.sessions_for_world(runtime.world_id) == ()
        result = runtime._mgr.pulse(runtime.continuity_engram_id, pulse_event=event,
                                    runtime_fence=runtime._current_runtime_fence())
        turn = runtime.causal_ledger.get_turn_for_world(result.turn_id, runtime.world_id)
        context = "work:" + runtime.world.get_task_front(created["page"]["id"]).center_id
        assert turn.execution_context == context
        assert len(fixture.requests) == 1 and fixture.scopes[0].context_id == context
    finally:
        runtime.close()
