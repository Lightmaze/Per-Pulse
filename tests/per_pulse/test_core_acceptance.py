"""Independent runtime acceptance checks through the public page API.

The real World, causal ledger, Kernel and HTTP adapter run here. Only the
remote HTTP transport is replaced with explicitly authored test responses.
These checks establish execution and persistence behavior, not model quality.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def _client(path, factory=None):
    return TestClient(create_app(path, model_factory=factory), base_url="http://localhost")


def _page(client, title, instruction):
    response = client.post("/api/pages", json={"title": title, "instruction": instruction})
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def _send(client, page, content):
    response = client.post(f"/api/pages/{page}/messages", json={"content": content})
    assert response.status_code == 202, response.text
    return response.json()["messageId"]


def _wait(client, page, predicate, *, timeout=8):
    deadline = time.monotonic() + timeout
    view = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/pages/{page}")
        assert response.status_code == 200, response.text
        view = response.json()
        if predicate(view):
            return view
        time.sleep(0.025)
    pytest.fail(f"Page {page} did not reach the expected state: {view}")


def _assistant_count(view):
    return sum(message["role"] == "assistant" for message in view["messages"])


def _response(content="Acceptance fixture response."):
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}},
    ]
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=body + "data: [DONE]\n\n")


def _factory(monkeypatch, handler):
    monkeypatch.setenv("PER_PULSE_ACCEPTANCE_KEY", "synthetic-acceptance-credential")

    def create(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://acceptance.invalid/chat/completions", "acceptance-fixture",
                           credential_env="PER_PULSE_ACCEPTANCE_KEY"),
            transport=httpx.MockTransport(handler),
        )

    return create


def test_offline_backlog_drains_once_per_context_after_reopen(tmp_path, monkeypatch):
    """Queued work belongs to one subject but retains its own conversation."""
    with _client(tmp_path) as client:
        home_id = _send(client, "home", "HOME_BACKLOG_ONLY")
        first = _page(client, "First context", "FIRST_BACKLOG_ONLY")
        first_followup = _send(client, first, "FIRST_FOLLOWUP_ONLY")
        second = _page(client, "Second context", "SECOND_BACKLOG_ONLY")
        subject = client.app.state.pages.runtime.continuity_engram_id
        assert {client.app.state.pages.runtime.world.get_task_front(page).focal_engram_id
                for page in (first, second)} == {subject}

    requests = []
    lock = threading.Lock()

    def handler(request):
        with lock:
            requests.append(json.loads(request.content))
        return _response()

    factory = _factory(monkeypatch, handler)
    with _client(tmp_path, factory) as client:
        home = _wait(client, "home", lambda value: _assistant_count(value) == 1)
        first_view = _wait(client, first, lambda value: _assistant_count(value) == 2)
        _wait(client, second, lambda value: _assistant_count(value) == 1)
        assert home["messages"][0]["id"] == home_id
        assert first_followup in {message["id"] for message in first_view["messages"]}
        assert len(requests) == 4
        for request in requests:
            messages = json.dumps(request["messages"])
            assert not ("HOME_BACKLOG_ONLY" in messages and "FIRST_BACKLOG_ONLY" in messages)
            assert not ("HOME_BACKLOG_ONLY" in messages and "SECOND_BACKLOG_ONLY" in messages)
            assert not ("FIRST_BACKLOG_ONLY" in messages and "SECOND_BACKLOG_ONLY" in messages)
        continuation = next(request for request in requests
                            if "FIRST_FOLLOWUP_ONLY" in json.dumps(request["messages"]))
        assert "FIRST_BACKLOG_ONLY" in json.dumps(continuation["messages"])
        event_ids = {page: [message["id"] for message in client.get(f"/api/pages/{page}").json()["messages"]]
                     for page in ("home", first, second)}

    with _client(tmp_path, factory) as client:
        # A completed backlog has no queued event for reopening to replay.
        runtime = client.app.state.pages.runtime
        initial_tick = runtime.tick_count
        _wait(client, "home", lambda _value: runtime.tick_count >= initial_tick + 3)
        assert len(requests) == 4
        for page, ids in event_ids.items():
            assert [message["id"] for message in client.get(f"/api/pages/{page}").json()["messages"]] == ids


def test_busy_context_preserves_new_input_while_sibling_context_progresses(tmp_path, monkeypatch):
    """Parallel local contexts share a subject without sharing in-flight input."""
    entered, release = threading.Event(), threading.Event()
    requests = []
    lock = threading.Lock()

    def handler(request):
        payload = json.loads(request.content)
        with lock:
            requests.append(payload)
        messages = json.dumps(payload["messages"])
        if "HELD_CONTEXT_FIRST" in messages and "HELD_CONTEXT_NEXT" not in messages:
            entered.set()
            assert release.wait(6), "The test did not release its held transport."
        return _response()

    with _client(tmp_path, _factory(monkeypatch, handler)) as client:
        try:
            first = _page(client, "Held context", "HELD_CONTEXT_FIRST")
            assert entered.wait(4), "The first context never reached the transport."
            next_id = _send(client, first, "HELD_CONTEXT_NEXT")
            second = _page(client, "Independent context", "FREE_CONTEXT_ONLY")
            _wait(client, second, lambda value: _assistant_count(value) == 1)
            held = client.get(f"/api/pages/{first}").json()
            assert _assistant_count(held) == 0
            assert next_id in {message["id"] for message in held["messages"]}
        finally:
            release.set()
        finished = _wait(client, first, lambda value: _assistant_count(value) == 2)
        assert len(finished["messages"]) == 4
        assert len(requests) == 3
        for request in requests:
            messages = json.dumps(request["messages"])
            assert not ("HELD_CONTEXT_FIRST" in messages and "FREE_CONTEXT_ONLY" in messages)
        followup = next(request for request in requests
                        if "HELD_CONTEXT_NEXT" in json.dumps(request["messages"]))
        assert "HELD_CONTEXT_FIRST" in json.dumps(followup["messages"])


def test_failed_context_does_not_replay_or_block_healthy_context(tmp_path, monkeypatch):
    """A provider failure terminalizes its input, without poisoning a sibling."""
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if "FAILED_CONTEXT_ONLY" in json.dumps(payload["messages"]):
            return httpx.Response(503, text="Synthetic local failure.")
        return _response()

    factory = _factory(monkeypatch, handler)
    with _client(tmp_path, factory) as client:
        failed = _page(client, "Failed context", "FAILED_CONTEXT_ONLY")
        healthy = _page(client, "Healthy context", "HEALTHY_CONTEXT_ONLY")
        failed_view = _wait(client, failed, lambda value: value["page"]["status"] == "error")
        _wait(client, healthy, lambda value: _assistant_count(value) == 1)
        assert _assistant_count(failed_view) == 0
        assert len(requests) == 2
        _send(client, healthy, "HEALTHY_CONTEXT_NEXT")
        _wait(client, healthy, lambda value: _assistant_count(value) == 2)
        assert len(requests) == 3
        assert sum("FAILED_CONTEXT_ONLY" in json.dumps(item["messages"]) for item in requests) == 1

    with _client(tmp_path, factory) as client:
        runtime = client.app.state.pages.runtime
        initial_tick = runtime.tick_count
        _wait(client, failed, lambda _value: runtime.tick_count >= initial_tick + 3)
        assert len(requests) == 3
        assert _assistant_count(client.get(f"/api/pages/{failed}").json()) == 0
