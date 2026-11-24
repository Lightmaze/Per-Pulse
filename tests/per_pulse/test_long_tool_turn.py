"""One turn can take many tool steps before it answers.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted; tools, World/Kernel persistence and the
workspace are real.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from per_pulse.execution import PageExecution
from per_pulse.service import FOREGROUND_MODEL_ROUNDS
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

STEPS = 30


def test_a_turn_can_read_thirty_times_before_answering(tmp_path, monkeypatch):
    assert STEPS + 1 < FOREGROUND_MODEL_ROUNDS
    monkeypatch.setenv("PER_PULSE_STEPS_KEY", "synthetic-test-credential")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.md").write_text("Step notes.\n", encoding="utf-8")
    requests = []

    def handler(request):
        messages = json.loads(request.content)["messages"]
        requests.append(messages)
        done = sum(1 for message in messages if message.get("role") == "tool")
        if done < STEPS:
            delta = {"tool_calls": [{"index": 0, "id": f"read-{done}", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "notes.md"})}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": f"Read the notes {done} times."}, "stop"
        events = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "steps-fixture",
                           credential_env="PER_PULSE_STEPS_KEY"),
            transport=httpx.MockTransport(handler),
        )

    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        page_id = client.post("/api/pages", json={"title": "Steps", "instruction": "Read the notes."}).json()["page"]["id"]
        execution = PageExecution(client.app.state.pages.runtime)
        deadline = time.monotonic() + 30
        while True:
            replies = [m["content"] for m in client.get(f"/api/pages/{page_id}").json()["messages"]
                       if m["role"] == "assistant"]
            if replies:
                break
            assert execution.snapshot(page_id)["status"] != "error", "the turn stopped before answering"
            assert time.monotonic() < deadline, "no reply"
            time.sleep(0.05)
    assert replies == [f"Read the notes {STEPS} times."]
    assert len(requests) == STEPS + 1
