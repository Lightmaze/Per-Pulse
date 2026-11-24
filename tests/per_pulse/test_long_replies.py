"""Long replies finish: a reply cut at the output limit is kept, and a slow
steady stream is not ended by a whole-reply deadline.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted (including deliberately slow streams);
the Kernel, World and page service are real.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import OUTPUT_LIMIT_NOTE, ChatHTTPConfig, ChatHTTPModelAdapter


def _chunk(delta: dict, finish: str | None = None) -> bytes:
    return ("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n").encode()


def _client(tmp_path, monkeypatch, body, **config):
    monkeypatch.setenv("PER_PULSE_LONG_REPLY_KEY", "synthetic-test-credential")

    def handler(_request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "long-reply-fixture",
                           credential_env="PER_PULSE_LONG_REPLY_KEY", **config),
            transport=httpx.MockTransport(handler))

    return TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost")


def _settle(client, page_id, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        view = client.get(f"/api/pages/{page_id}").json()
        if view["page"]["status"] not in ("queued", "working"):
            return view, client.get(f"/api/pages/{page_id}/execution").json()
        assert time.monotonic() < deadline, "the turn did not settle"
        time.sleep(0.05)


def _page(client):
    return client.post("/api/pages", json={"title": "Report", "instruction": "Write the full report."}).json()["page"]["id"]


def test_a_reply_cut_at_the_output_limit_is_kept_and_says_so(tmp_path, monkeypatch):
    def body():
        yield _chunk({"content": "Section one is complete. Section two begins"})
        yield _chunk({}, "length")
        yield b"data: [DONE]\n\n"

    with _client(tmp_path, monkeypatch, body) as client:
        view, execution = _settle(client, _page(client))
    replies = [m["content"] for m in view["messages"] if m["role"] == "assistant"]
    assert replies == ["Section one is complete. Section two begins" + OUTPUT_LIMIT_NOTE]
    assert view["page"]["status"] == "idle" and execution["failureCode"] is None


def test_a_limit_inside_a_tool_call_fails_with_its_own_reason(tmp_path, monkeypatch):
    def body():
        yield _chunk({"tool_calls": [{"index": 0, "id": "cut", "type": "function",
                                      "function": {"name": "read_file", "arguments": '{"path": "no'}}]})
        yield _chunk({}, "length")
        yield b"data: [DONE]\n\n"

    with _client(tmp_path, monkeypatch, body) as client:
        _view, execution = _settle(client, _page(client))
    assert execution["failureCode"] == "provider_output_limit"


def test_a_slow_steady_reply_is_not_cut_by_a_whole_reply_deadline(tmp_path, monkeypatch):
    # Before: the 60 s default ended any reply still streaming after 60 s.
    # Here the idle timeout is 0.4 s and the reply takes about 1.2 s.
    def body():
        for index in range(12):
            time.sleep(0.1)
            yield _chunk({"content": f"part {index}. "})
        yield _chunk({}, "stop")
        yield b"data: [DONE]\n\n"

    with _client(tmp_path, monkeypatch, body, timeout_seconds=0.4) as client:
        view, execution = _settle(client, _page(client))
    replies = [m["content"] for m in view["messages"] if m["role"] == "assistant"]
    assert replies and replies[-1].startswith("part 0. ") and replies[-1].endswith("part 11. ")
    assert execution["failureCode"] is None


@pytest.mark.parametrize("finish, config, code", [
    ("content_filter", {}, "provider_content_filtered"),
    (None, {"max_stream_seconds": 0.3}, "provider_stream_too_long"),
])
def test_other_early_endings_have_their_own_reasons(tmp_path, monkeypatch, finish, config, code):
    def body():
        for _ in range(6):
            time.sleep(0.1)
            yield _chunk({"content": "text "})
        yield _chunk({}, finish or "stop")
        yield b"data: [DONE]\n\n"

    with _client(tmp_path, monkeypatch, body, **config) as client:
        _view, execution = _settle(client, _page(client))
    assert execution["failureCode"] == code
