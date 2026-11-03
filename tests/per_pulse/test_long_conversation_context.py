"""A long page keeps working once its history outgrows the model's context.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted and records each request; World/Kernel
persistence is real.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.context_window import OMITTED_NOTE

BUDGET = 2048
TURNS = 8


def test_older_turns_leave_the_request_but_stay_in_the_page(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", str(BUDGET))
    monkeypatch.setenv("PER_PULSE_CONTEXT_KEY", "synthetic-test-credential")
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body["messages"])
        answer = f"Answer {len(requests)}: " + "detail " * 300
        events = [
            {"choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "context-fixture",
                           credential_env="PER_PULSE_CONTEXT_KEY"),
            transport=httpx.MockTransport(handler),
        )

    def replies(client, page_id):
        return [m for m in client.get(f"/api/pages/{page_id}").json()["messages"] if m["role"] == "assistant"]

    def wait_for(client, page_id, count):
        execution = PageExecution(client.app.state.pages.runtime)
        deadline = time.monotonic() + 20
        while len(replies(client, page_id)) < count:
            assert execution.snapshot(page_id)["status"] != "error", "a turn failed"
            assert time.monotonic() < deadline, "no reply"
            time.sleep(0.05)

    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        opening = "OPENING: plan the field study. " + "context " * 200
        page_id = client.post("/api/pages", json={"title": "Study", "instruction": opening}).json()["page"]["id"]
        wait_for(client, page_id, 1)
        for index in range(2, TURNS + 1):
            sent = client.post(f"/api/pages/{page_id}/messages",
                               json={"content": f"Message {index}. " + "more " * 300})
            assert sent.status_code == 202, sent.text
            wait_for(client, page_id, index)

        assert len(replies(client, page_id)) == TURNS  # the page keeps everything
        last = requests[-1]
        contents = [message.get("content") or "" for message in last]
        assert OMITTED_NOTE in contents
        assert any(content.startswith("OPENING:") for content in contents)
        assert any(content.startswith(f"Message {TURNS}.") for content in contents)
        assert not any(content.startswith("Message 2.") for content in contents)
        page_text = "".join(m["content"] for m in client.get(f"/api/pages/{page_id}").json()["messages"])
        assert len(page_text.encode("utf-8")) // 3 > 2 * BUDGET  # the whole history would not fit
        assert len("".join(contents).encode("utf-8")) // 3 <= BUDGET
        # A request that still fit had no note.
        assert OMITTED_NOTE not in [message.get("content") for message in requests[0]]
