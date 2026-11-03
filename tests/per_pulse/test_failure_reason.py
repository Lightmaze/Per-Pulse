"""A failed turn reports the Kernel's reason, and the message can be sent again.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted; World/Kernel persistence is real.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app


def test_a_provider_rejection_is_reported_with_its_code(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_FAILURE_TEST_KEY", "synthetic-test-credential")
    calls = []

    def handler(_request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(401, json={"error": {"message": "Synthetic: invalid API key."}})
        events = [{"choices": [{"index": 0, "delta": {"content": "Answered."}, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        data = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "failure-fixture",
                           credential_env="PER_PULSE_FAILURE_TEST_KEY"),
            transport=httpx.MockTransport(handler))

    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        page_id = client.post("/api/pages", json={"title": "Q", "instruction": "A question."}).json()["page"]["id"]
        deadline = time.monotonic() + 10
        while (view := client.get(f"/api/pages/{page_id}").json())["page"]["status"] != "error":
            assert time.monotonic() < deadline, view["page"]
            time.sleep(0.05)
        assert view["execution"]["failureCode"] == "provider_http_401"

        # Sending the same message again starts a new turn that can succeed.
        again = client.post(f"/api/pages/{page_id}/messages", json={"content": "A question."})
        assert again.status_code == 202, again.text
        while not any(m["role"] == "assistant" for m in client.get(f"/api/pages/{page_id}").json()["messages"]):
            assert time.monotonic() < deadline + 10
            time.sleep(0.05)
        view = client.get(f"/api/pages/{page_id}").json()
        assert view["page"]["status"] != "error"
        assert view["execution"]["failureCode"] is None


def test_a_turn_with_an_action_in_doubt_is_not_displaced_by_a_new_message(tmp_path, monkeypatch):
    from per_pulse.execution import PageExecution
    from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

    monkeypatch.setenv("PER_PULSE_FAILURE_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        messages = json.loads(request.content)["messages"]
        if not any(m.get("role") == "tool" for m in messages) and "WRITE_DRAFT" in json.dumps(messages):
            delta = {"tool_calls": [{"index": 0, "id": "doubt-file", "type": "function", "function": {
                "name": "mutate_file", "arguments": json.dumps(
                    {"path": "draft.md", "expected_digest": None, "content": "Draft.\n"})}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": "Answered."}, "stop"
        events = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        data = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "failure-fixture",
                           credential_env="PER_PULSE_FAILURE_TEST_KEY"),
            transport=httpx.MockTransport(handler))

    with TestClient(create_app(tmp_path, model_factory=factory, allow_workspace_actions=True,
                               auto_apply_edits=False), base_url="http://localhost") as client:
        page_id = client.post("/api/pages", json={"title": "W", "instruction": "WRITE_DRAFT"}).json()["page"]["id"]
        execution = PageExecution(client.app.state.pages.runtime)
        deadline = time.monotonic() + 10
        while not execution.snapshot(page_id)["pendingActions"]:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        waiting = execution.snapshot(page_id)
        client.post(f"/api/pages/{page_id}/messages", json={"content": "Something else."})
        time.sleep(1.0)
        still = execution.snapshot(page_id)
        assert still["turnId"] == waiting["turnId"]
        pending, = still["pendingActions"]
        assert pending["canResolve"], "a pending approval must not be displaced"
        assert not (tmp_path / "workspace" / "draft.md").exists()
