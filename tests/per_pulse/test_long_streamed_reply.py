"""A long reply streamed one token per chunk reaches the page intact.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Providers usually stream one token per server-sent event. The HTTP model
transport here is scripted; World/Kernel persistence is real.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def _factory(monkeypatch, chunks, *, reasoning=0):
    monkeypatch.setenv("PER_PULSE_LONG_REPLY_KEY", "synthetic-test-credential")

    def handler(_request):
        events = [{"choices": [{"index": 0, "delta": {"reasoning_content": f"r{i} "}, "finish_reason": None}]}
                  for i in range(reasoning)]
        events += [{"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]} for text in chunks]
        events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "long-reply-fixture",
                           credential_env="PER_PULSE_LONG_REPLY_KEY",
                           reasoning_field="reasoning_content" if reasoning else None),
            transport=httpx.MockTransport(handler),
        )

    return factory


def _reply(tmp_path: Path, factory) -> str:
    with TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost") as client:
        created = client.post("/api/pages", json={"title": "Essay", "instruction": "Write a long essay."})
        page_id = created.json()["page"]["id"]
        execution = PageExecution(client.app.state.pages.runtime)
        deadline = time.monotonic() + 15
        while True:
            messages = client.get(f"/api/pages/{page_id}").json()["messages"]
            replies = [m["content"] for m in messages if m["role"] == "assistant"]
            if replies:
                return replies[-1]
            assert execution.snapshot(page_id)["status"] != "error", "the turn failed"
            assert time.monotonic() < deadline, "no reply reached the page"
            time.sleep(0.05)


def test_a_reply_of_thousands_of_chunks_arrives_whole(tmp_path, monkeypatch):
    chunks = [f"token{i} " for i in range(4000)] + ["终", "了。"]
    assert _reply(tmp_path, _factory(monkeypatch, chunks)) == "".join(chunks)


def test_long_reasoning_before_the_reply_does_not_end_the_turn(tmp_path, monkeypatch):
    chunks = [f"word{i} " for i in range(600)]
    assert _reply(tmp_path, _factory(monkeypatch, chunks, reasoning=900)) == "".join(chunks)
