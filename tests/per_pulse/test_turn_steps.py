"""While a turn works, the page shows the tool steps it has taken.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted and can be held mid-turn; tools,
World/Kernel persistence and the workspace are real.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def test_steps_are_listed_while_the_turn_works_and_cleared_after(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_STEPS_VIEW_KEY", "synthetic-test-credential")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.md").write_text("Notes.\n", encoding="utf-8")
    held, release = threading.Event(), threading.Event()

    def handler(request):
        messages = json.loads(request.content)["messages"]
        tools = sum(1 for m in messages if m.get("role") == "tool")
        if tools == 0:
            delta = {"tool_calls": [{"index": 0, "id": "steps-read", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "notes.md"})}}]}
            finish = "tool_calls"
        else:
            held.set()
            assert release.wait(10)
            delta, finish = {"content": "Done reading."}, "stop"
        events = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        data = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "steps-view-fixture",
                           credential_env="PER_PULSE_STEPS_VIEW_KEY"),
            transport=httpx.MockTransport(handler))

    try:
        with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
            page_id = client.post("/api/pages", json={"title": "Read", "instruction": "Read the notes."}).json()["page"]["id"]
            assert held.wait(10), "the turn did not reach its second model round"
            execution = client.get(f"/api/pages/{page_id}/execution").json()
            assert execution["status"] == "working"
            assert execution["steps"] == [{
                "actionId": execution["steps"][0]["actionId"], "toolName": "read_file",
                "status": "succeeded", "target": "notes.md",
            }]
            release.set()
            deadline = time.monotonic() + 10
            while not any(m["role"] == "assistant" for m in client.get(f"/api/pages/{page_id}").json()["messages"]):
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert client.get(f"/api/pages/{page_id}/execution").json()["steps"] == []
    finally:
        release.set()
