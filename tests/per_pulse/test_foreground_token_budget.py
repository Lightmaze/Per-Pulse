"""A person's own messages are not held by a hidden hourly token budget.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted and reports synthetic usage the size of
an ordinary long-context request; World, Kernel and the page service are real.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_BUDGET_KEY", "synthetic-test-credential")

    def handler(_request):
        events = [{"choices": [{"index": 0, "delta": {"content": "Synthetic fixture reply."}, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                  # About what one request with a long page history costs.
                  {"choices": [], "usage": {"prompt_tokens": 60_000, "completion_tokens": 1_000,
                                            "total_tokens": 61_000}}]
        body = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "budget-fixture",
                           credential_env="PER_PULSE_BUDGET_KEY"),
            transport=httpx.MockTransport(handler))

    return TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost")


def _replies(client, page_id, count, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        view = client.get(f"/api/pages/{page_id}").json()
        if sum(m["role"] == "assistant" for m in view["messages"]) >= count:
            return view
        assert time.monotonic() < deadline, (
            f"reply {count} never came; page is {view['page']['status']}")
        time.sleep(0.05)


def test_your_next_message_runs_after_heavy_use_within_the_hour(tmp_path, monkeypatch):
    # Before: the World's default 100K-token hourly budget covered every turn,
    # so after two long-context replies a person's next message stayed queued
    # until the hour rolled over, with no explanation.
    with _client(tmp_path, monkeypatch) as client:
        page_id = client.post("/api/pages", json={"title": "Long notes", "instruction": "Start."}).json()["page"]["id"]
        _replies(client, page_id, 1)
        for count in (2, 3):
            assert client.post(f"/api/pages/{page_id}/messages",
                               json={"content": f"Message {count}.", "references": []}).status_code == 202
            _replies(client, page_id, count)
        usage = client.get(f"/api/pages/{page_id}/execution").json()["usage"]
        assert usage["inputTokens"] == 180_000
