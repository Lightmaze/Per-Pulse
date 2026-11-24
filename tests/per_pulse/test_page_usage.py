"""A page shows the tokens its model service reported, and never estimates.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted and reports synthetic usage the way an
OpenAI-compatible service does (a final usage-only chunk). World, Kernel,
SQLite and the page service are real.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def _client(tmp_path, monkeypatch, usages):
    """Each model call answers with text and, if given, the next usage report."""
    monkeypatch.setenv("PER_PULSE_USAGE_KEY", "synthetic-test-credential")
    remaining = list(usages)

    def handler(_request):
        usage = remaining.pop(0) if remaining else None
        events = [{"choices": [{"index": 0, "delta": {"content": "Synthetic fixture reply."}, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        if usage is not None:
            events.append({"choices": [], "usage": usage})
        body = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "usage-fixture",
                           credential_env="PER_PULSE_USAGE_KEY"),
            transport=httpx.MockTransport(handler))

    return TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost")


def _replies(client, page_id, count):
    deadline = time.monotonic() + 15
    while True:
        view = client.get(f"/api/pages/{page_id}").json()
        if sum(m["role"] == "assistant" for m in view["messages"]) >= count and view["page"]["status"] != "working":
            return
        assert time.monotonic() < deadline, "the reply never finished"
        time.sleep(0.05)


def _usage(client, page_id):
    return client.get(f"/api/pages/{page_id}/execution").json()["usage"]


def test_a_page_adds_up_reported_tokens_across_turns(tmp_path, monkeypatch):
    usages = [
        {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
         "prompt_tokens_details": {"cached_tokens": 100}},
        {"prompt_tokens": 200, "completion_tokens": 40, "total_tokens": 240},
    ]
    with _client(tmp_path, monkeypatch, usages) as client:
        page_id = client.post("/api/pages", json={"title": "Plan", "instruction": "Draft a plan."}).json()["page"]["id"]
        _replies(client, page_id, 1)
        assert _usage(client, page_id) == {
            "inputTokens": 120, "outputTokens": 30, "cachedTokens": 100, "unreportedRounds": 0,
            "lastTurn": {"inputTokens": 120, "outputTokens": 30, "cachedTokens": 100, "unreportedRounds": 0},
        }
        assert client.post(f"/api/pages/{page_id}/messages", json={"content": "Shorter, please.", "references": []}).status_code == 202
        _replies(client, page_id, 2)
        usage = _usage(client, page_id)
        assert (usage["inputTokens"], usage["outputTokens"], usage["cachedTokens"]) == (320, 70, 100)
        assert usage["lastTurn"] == {"inputTokens": 200, "outputTokens": 40, "cachedTokens": 0, "unreportedRounds": 0}
        # Another page's work is not counted here.
        assert _usage(client, "home") == {"inputTokens": 0, "outputTokens": 0, "cachedTokens": 0,
                                          "unreportedRounds": 0, "lastTurn": None}


def test_a_reply_without_reported_usage_is_counted_as_unreported_not_estimated(tmp_path, monkeypatch):
    usages = [{"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}, None]
    with _client(tmp_path, monkeypatch, usages) as client:
        page_id = client.post("/api/pages", json={"title": "Notes", "instruction": "Summarize."}).json()["page"]["id"]
        _replies(client, page_id, 1)
        assert client.post(f"/api/pages/{page_id}/messages", json={"content": "Again.", "references": []}).status_code == 202
        _replies(client, page_id, 2)
        usage = _usage(client, page_id)
        assert (usage["inputTokens"], usage["outputTokens"], usage["unreportedRounds"]) == (50, 10, 1)
        assert usage["lastTurn"] is None
