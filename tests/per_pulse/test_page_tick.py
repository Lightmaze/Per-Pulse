"""/tick: explicit, bounded quiet continuation of one page.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The model is a local HTTP test transport; World, Kernel and SQLite are real.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


class Transport:
    def __init__(self, monkeypatch, *, delay: float = 0.0):
        monkeypatch.setenv("PER_PULSE_TICK_TEST_KEY", "synthetic-test-credential")
        self.requests: list[list[dict]] = []
        self.lock = threading.Lock()
        self.delay = delay

    def factory(self, scope):
        def respond(request):
            body = json.loads(request.content)
            if self.delay:
                time.sleep(self.delay)
            with self.lock:
                self.requests.append(body["messages"])
                output = f"STEP_{len(self.requests)}"
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": output}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}},
            ]
            stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=stream + "data: [DONE]\n\n")

        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "tick-fixture",
                           credential_env="PER_PULSE_TICK_TEST_KEY"),
            transport=httpx.MockTransport(respond),
        )


def client_for(path, **kwargs):
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def eventually(probe, predicate, *, timeout=10):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = probe()
        if predicate(value):
            return value
        time.sleep(0.025)
    pytest.fail(f"condition did not settle: {value!r}")


def view(client, page_id):
    response = client.get(f"/api/pages/{page_id}")
    assert response.status_code == 200, response.text
    return response.json()


def replies(payload):
    return [m["content"] for m in payload["messages"] if m["role"] == "assistant"]


def new_work(client, instruction="LOCAL_HISTORY"):
    response = client.post("/api/pages", json={"title": "Thinking", "instruction": instruction})
    assert response.status_code == 201, response.text
    page_id = response.json()["page"]["id"]
    eventually(lambda: view(client, page_id), lambda v: len(replies(v)) == 1)
    return page_id


def test_tick_gives_exactly_n_quiet_turns_from_the_page_history(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    # Background pulses are off: /tick is an explicit request, not background work.
    with client_for(tmp_path, model_factory=transport.factory) as client:
        work = new_work(client)
        response = client.post(f"/api/pages/{work}/tick", json={"count": 3})
        assert response.status_code == 201, response.text
        assert response.json()["tick"]["status"] == "running"
        done = eventually(lambda: view(client, work), lambda v: v["tick"]["status"] == "complete")
        assert done["tick"]["completed"] == done["tick"]["target"] == 3
        assert replies(done) == ["STEP_1", "STEP_2", "STEP_3", "STEP_4"]
        time.sleep(0.2)
        assert len(transport.requests) == 4, "A finished run never makes a fourth step."
        for step in transport.requests[1:]:
            text = json.dumps(step)
            assert "LOCAL_HISTORY" in text
            assert step[-1] == {"role": "user", "content": ""}, "A time step carries no new goal."
            assert "tick" not in text.lower()
        assert all(m["role"] != "user" for m in done["messages"][1:]), "Steps are not shown as your messages."
        pulse = client.get("/api/pulse").json()
        assert pulse["budget"]["used"] == 0
        assert [run["pageId"] for run in pulse["tickRuns"]] == [work]
        home = view(client, "home")
        assert home["messages"] == [] and home["tick"] is None

    with client_for(tmp_path, model_factory=transport.factory) as client:
        time.sleep(0.2)
        assert len(transport.requests) == 4
        assert view(client, work)["tick"]["status"] == "complete"


def test_tick_needs_a_finished_reply_a_model_and_a_valid_count(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    with client_for(tmp_path / "connected", model_factory=transport.factory) as client:
        empty_home = client.post("/api/pages/home/tick", json={"count": 1})
        assert empty_home.status_code == 409
        assert empty_home.json()["error"]["code"] == "tick_needs_conversation"
        work = new_work(client)
        for count in (0, 257):
            invalid = client.post(f"/api/pages/{work}/tick", json={"count": count})
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "tick_count_invalid"
        assert client.post("/api/pages/missing/tick", json={"count": 1}).status_code == 404
    with client_for(tmp_path / "offline", model_factory=None) as client:
        response = client.post("/api/pages/home/tick", json={"count": 1})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "tick_model_unavailable"


def test_stop_and_a_new_message_end_the_run_before_the_next_step(tmp_path, monkeypatch):
    transport = Transport(monkeypatch, delay=0.15)
    with client_for(tmp_path, model_factory=transport.factory, pulse=PulseSettings(enabled=True)) as client:
        work = new_work(client)
        assert client.post(f"/api/pages/{work}/tick", json={"count": 20}).status_code == 201
        busy = client.post(f"/api/pages/{work}/tick", json={"count": 1})
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "tick_running"
        stopped = client.post(f"/api/pages/{work}/tick/stop", json={})
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["tick"]["status"] == "stopped"
        assert stopped.json()["tick"]["reason"] == "operator"
        # A step already running may finish; a queued, unstarted step is cancelled.
        time.sleep(0.5)
        stopped_at = len(transport.requests)
        assert stopped_at in (1, 2)
        time.sleep(0.5)
        assert len(transport.requests) == stopped_at, "Stop takes effect before the next step."

        assert client.post(f"/api/pages/{work}/tick", json={"count": 20}).status_code == 201
        eventually(lambda: view(client, work), lambda v: v["tick"]["completed"] >= 1)
        message = client.post(f"/api/pages/{work}/messages", json={"content": "I'll take it from here."})
        assert message.status_code == 202
        ended = eventually(lambda: view(client, work), lambda v: v["tick"]["status"] == "stopped")
        assert ended["tick"]["reason"] == "user_message"
        eventually(lambda: view(client, work), lambda v: v["page"]["status"] == "idle")
        count = len(transport.requests)
        time.sleep(0.4)
        assert len(transport.requests) == count


def test_stop_cancels_a_queued_step_that_has_not_started(tmp_path, monkeypatch):
    transport = Transport(monkeypatch)
    with client_for(tmp_path, model_factory=transport.factory) as client:
        work = new_work(client)
        runtime = client.app.state.pages.runtime
        dispatch = runtime._engine.tick
        monkeypatch.setattr(runtime._engine, "tick", lambda *args, **kwargs: [])
        started = client.post(f"/api/pages/{work}/tick", json={"count": 5})
        assert started.status_code == 201 and started.json()["tick"]["delivered"] == 1
        steps = runtime.causal_ledger.list_events(kind="spontaneous", limit=10)
        assert [event.status.value for event in steps] == ["queued"]
        stopped = client.post(f"/api/pages/{work}/tick/stop", json={}).json()["tick"]
        assert stopped["status"] == "stopped" and stopped["delivered"] == stopped["completed"] == 0
        assert runtime.causal_ledger.get_event(steps[0].id).status.value == "cancelled"
        monkeypatch.setattr(runtime._engine, "tick", dispatch)
        time.sleep(0.4)
        assert len(transport.requests) == 1, "A cancelled step never reaches the model."
        assert replies(view(client, work)) == ["STEP_1"]
