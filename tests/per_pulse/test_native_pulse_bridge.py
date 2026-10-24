"""Native context-return contracts through real Kernel turns and synthetic HTTP.

The transport deliberately produces test text and tool calls. These tests
check retained provenance and routing, not the quality of a live model.
"""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.errors import ScopeConflict
from pulse_system.service.kernel_runtime import KernelHarnessRuntime


def stream_response(*, text=None, tool=None, arguments=None, call_id="test-call"):
    delta = ({"content": text} if tool is None else {"tool_calls": [{
        "index": 0, "id": call_id, "type": "function",
        "function": {"name": tool, "arguments": json.dumps(arguments)},
    }]})
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {},
                      "finish_reason": "stop" if tool is None else "tool_calls"}]},
    ]
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=body + "data: [DONE]\n\n")


def factory_for(monkeypatch, handler):
    monkeypatch.setenv("PER_PULSE_NATIVE_TEST_KEY", "synthetic-native-credential")

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "native-fixture",
                           credential_env="PER_PULSE_NATIVE_TEST_KEY"),
            transport=httpx.MockTransport(handler),
        )
    return factory


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.025)
    pytest.fail("Native pulse bridge did not reach the expected state.")


def report_handler(requests, *, fail_after_report=False):
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]
        last = messages[-1]
        if last["role"] == "tool":
            if "selected_work_report" in last["content"]:
                return stream_response(text="Home considered the selected evidence.")
            if fail_after_report:
                return httpx.Response(503, text="Synthetic failure after staging.")
            return stream_response(text="Work settled after selecting its finding.")
        notice = next((m["content"] for m in reversed(messages)
                       if m["role"] == "system" and "work_report_notice" in m["content"]), None)
        if notice:
            event_id = json.loads(notice.split("\n")[-1])["event_id"]
            return stream_response(tool="work", arguments={"op": "read_report", "event_id": event_id})
        return stream_response(tool="work", arguments={
            "op": "report", "kind": "finding", "note": "SELECTED_WORK_EVIDENCE",
        })
    return handler


def test_work_report_reenters_home_as_source_addressed_tool_evidence(tmp_path, monkeypatch):
    requests = []
    factory = factory_for(monkeypatch, report_handler(requests))
    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        runtime.harness._allow_work_mutations = True
        created = client.post("/api/pages", json={
            "title": "TITLE_MUST_REMAIN_EVIDENCE", "instruction": "WORK_PRIVATE_CONTEXT",
        })
        assert created.status_code == 201, created.text
        subject = runtime.continuity_engram_id
        wait_until(lambda: any(m["role"] == "assistant" for m in
                              client.get("/api/pages/home").json()["messages"]))
        records = runtime.storage.native_work_requests(subject=subject, limit=32)
        assert len(records) == 1
        report = records[0]
        assert report["status"] == "delivered"
        event_id = report["event_id"]
        evidence = runtime._native_work.read_report(subject, event_id)
        assert evidence["note"] == "SELECTED_WORK_EVIDENCE"
        assert evidence["not_user_instruction"] is True
        assert evidence["goal_complete"] is False
        notices = [m for request in requests for m in request["messages"]
                   if m["role"] == "system" and "work_report_notice" in m["content"]]
        assert notices
        assert all("SELECTED_WORK_EVIDENCE" not in m["content"] for m in notices)
        assert all("TITLE_MUST_REMAIN_EVIDENCE" not in m["content"] for m in notices)
        home_request = requests[-1]
        assert any(m["role"] == "tool" and "SELECTED_WORK_EVIDENCE" in m["content"]
                   for m in home_request["messages"])
        assert not any(m["role"] in {"user", "system"} and "SELECTED_WORK_EVIDENCE" in m["content"]
                       for m in home_request["messages"])
        assert "WORK_PRIVATE_CONTEXT" not in json.dumps(home_request["messages"])
        tool_names = {t["function"]["name"] for t in home_request["tools"]}
        assert "work" in tool_names
        assert "write_file" not in tool_names and "run_command" not in tool_names
        with pytest.raises(ScopeConflict, match="settled selection"):
            runtime._native_work.read_report("foreign-subject", event_id)
        with pytest.raises(ScopeConflict, match="settled selection"):
            runtime._native_work.read_report(subject, created.json()["initialMessageId"])
        runtime._native_work.drain()
        assert runtime.storage.native_work_requests(subject=subject, limit=32)[0]["event_id"] == event_id

    # A later visit reads the original selection, even when no model is connected.
    with TestClient(create_app(tmp_path, model_factory=None), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        retained = runtime._native_work.read_report(runtime.continuity_engram_id, event_id)
        assert retained == evidence
        assert len(requests) == 4


def test_failed_source_never_activates_home_with_staged_report(tmp_path, monkeypatch):
    requests = []
    factory = factory_for(monkeypatch, report_handler(requests, fail_after_report=True))
    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        runtime.harness._allow_work_mutations = True
        created = client.post("/api/pages", json={"title": "Failure", "instruction": "Stage then fail."})
        assert created.status_code == 201
        subject = runtime.continuity_engram_id
        records = wait_until(lambda: [record for record in
            runtime.storage.native_work_requests(subject=subject, limit=32)
            if record["status"] == "cancelled"])
        assert records[0]["error_code"] == "source_did_not_settle"
        assert records[0]["event_id"] is None
        assert client.get("/api/pages/home").json()["messages"] == []
        assert len(requests) == 2


def test_internal_work_permission_is_independent_of_filesystem_mutation(tmp_path):
    closed = KernelHarnessRuntime(db_path=tmp_path / "closed.db", workspace=tmp_path / "files",
                                  allow_unconfigured=True)
    opened = KernelHarnessRuntime(db_path=tmp_path / "open.db", workspace=tmp_path / "files",
                                  allow_unconfigured=True, allow_work_mutations=True)
    legacy = KernelHarnessRuntime(db_path=tmp_path / "legacy.db", workspace=tmp_path / "files",
                                  allow_unconfigured=True, allow_mutations=True)
    try:
        assert not closed._allow_work_mutations
        assert opened._allow_work_mutations and not opened._allow_mutations
        assert legacy._allow_work_mutations and legacy._allow_mutations
    finally:
        closed.close()
        opened.close()
        legacy.close()


def test_attention_tool_uses_exact_live_context_without_filesystem_permission(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["messages"][-1]["role"] == "tool":
            return stream_response(text="The local thought is retained.")
        return stream_response(tool="attention", arguments={
            "op": "hold", "note": "One unresolved synthetic observation.", "drive": 0.4,
        })

    class AttentionFixture:
        def __init__(self, runtime):
            self.runtime, self.calls = runtime, []

        def input_for_turn(self, engram_id, turn_id):
            return None

        def execute(self, scope, call, cancellation, *, action_request_id):
            self.runtime.harness.authorize_attention(scope, action_request_id, call)
            cancellation.raise_if_cancelled()
            self.calls.append((scope, call, action_request_id))
            return {"retained": True, "note": call.arguments["note"]}

    factory = factory_for(monkeypatch, handler)
    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        fixture = AttentionFixture(runtime)
        runtime.harness.attention = fixture
        created = client.post("/api/pages", json={"title": "Local thought", "instruction": "Consider this."})
        page_id = created.json()["page"]["id"]
        wait_until(lambda: any(m["role"] == "assistant" for m in
                              client.get(f"/api/pages/{page_id}").json()["messages"]))
        assert len(fixture.calls) == 1
        scope, call, action_id = fixture.calls[0]
        front = runtime.world.get_task_front(page_id)
        assert scope.engram_id == runtime.continuity_engram_id
        assert scope.context_id == "work:" + front.center_id
        assert call.arguments["drive"] == 0.4
        tool_names = {t["function"]["name"] for t in requests[0]["tools"]}
        assert "attention" in tool_names and "write_file" not in tool_names
        with pytest.raises(ScopeConflict, match="exact active"):
            runtime.harness.authorize_attention(scope, action_id, call)


def test_real_attention_hold_then_release_in_one_turn_keeps_the_final_decision(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return stream_response(tool="attention", arguments={
                "op": "hold", "note": "A provisional synthetic thought.", "drive": 0.8,
            }, call_id="hold-call")
        if len(requests) == 2:
            return stream_response(tool="attention", arguments={"op": "release"}, call_id="release-call")
        return stream_response(text="The thought no longer needs another activation.")

    with TestClient(create_app(tmp_path, model_factory=factory_for(monkeypatch, handler)),
                    base_url="http://localhost") as client:
        pages = client.app.state.pages
        client.post("/api/pages/home/messages", json={"content": "Consider whether to retain this."})
        state = wait_until(lambda: (s if len(s["appliedActions"]) == 2 else None)
                           if (s := pages.runtime.storage.load_component_state(pages.context_body.key)) else None)
        assert state["pendingUpdates"] == {}
        assert state["contexts"]["home"]["note"] == ""
        assert state["contexts"]["home"]["drive"] == 0.0
        assert state["contexts"]["home"]["revision"] == 1
        assert len(requests) == 3


def test_real_attention_hold_is_discarded_when_its_source_turn_fails(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return stream_response(tool="attention", arguments={
                "op": "hold", "note": "This turn will not settle.", "drive": 0.9,
            })
        return httpx.Response(503, text="Synthetic failure after an attention action.")

    with TestClient(create_app(tmp_path, model_factory=factory_for(monkeypatch, handler)),
                    base_url="http://localhost") as client:
        pages = client.app.state.pages
        client.post("/api/pages/home/messages", json={"content": "Try one thought."})
        state = wait_until(lambda: (s if len(s["appliedActions"]) == 1 else None)
                           if (s := pages.runtime.storage.load_component_state(pages.context_body.key)) else None)
        assert state["pendingUpdates"] == {}
        assert state["contexts"]["home"]["note"] == ""
        assert state["contexts"]["home"]["drive"] == 0.0
        assert state["contexts"]["home"]["revision"] == 0
        assert client.get("/api/pages/home").json()["page"]["status"] == "error"
        assert len(requests) == 2


def test_later_user_attention_overrides_an_inflight_model_hold(tmp_path, monkeypatch):
    requests = []
    staged, finish = threading.Event(), threading.Event()

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return stream_response(tool="attention", arguments={
                "op": "hold", "note": "Earlier model focus.", "drive": 0.9,
            })
        staged.set()
        if not finish.wait(5):
            raise AssertionError("The test did not release its synthetic model response.")
        return stream_response(text="The earlier turn settled.")

    with TestClient(create_app(tmp_path, model_factory=factory_for(monkeypatch, handler)),
                    base_url="http://localhost") as client:
        pages = client.app.state.pages
        frozen = pages.context_body.clock()
        pages.context_body.clock = lambda: frozen
        try:
            client.post("/api/pages/home/messages", json={"content": "Consider the old focus."})
            assert staged.wait(5)
            before = next(field for field in pages.context_body.view()["contexts"] if field["pageId"] == "home")
            pages.context_body.hold("home", "The user's later focus.", 0.8)
            finish.set()
            state = wait_until(lambda: (s if len(s["appliedActions"]) == 1 else None)
                               if (s := pages.runtime.storage.load_component_state(pages.context_body.key)) else None)
            assert state["contexts"]["home"]["note"] == "The user's later focus."
            assert state["contexts"]["home"]["drive"] == 0.8
            assert state["contexts"]["home"]["excitation"] == pytest.approx(before["excitation"])
            assert state["contexts"]["home"]["revision"] == 1
            assert state["pendingUpdates"] == {}
        finally:
            finish.set()
