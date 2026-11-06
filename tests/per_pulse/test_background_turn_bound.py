"""Your own turns may run long; unattended turns stay bounded in time.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

A stuck background step (here a /tick step whose provider hangs) is ended at
the background bound, while a newer foreground Work that takes longer than
that bound still finishes and a pending approval in a person's own turn stays
open. Each Center reservation settles exactly once. The HTTP model transport
is scripted; World/Kernel persistence and Pulse admission are real.
"""
from __future__ import annotations

from collections import Counter
import json
import threading
import time

from fastapi.testclient import TestClient
import httpx

import per_pulse.service as service_module
from per_pulse.app import create_app
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

BACKGROUND_BOUND = 1.0
HANG = 4.0
SLOW_FOREGROUND = 2.0


def _factory(monkeypatch, release):
    monkeypatch.setenv("PER_PULSE_BOUND_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        messages = json.loads(request.content)["messages"]
        text = json.dumps(messages)
        replied = any(m.get("role") == "assistant" for m in messages)
        has_tool = any(m.get("role") == "tool" for m in messages)
        if "TICK_HANGS" in text and replied:
            release.wait(HANG)  # the background step's provider stalls
        if "SLOW_FOREGROUND" in text:
            time.sleep(SLOW_FOREGROUND)
        if "WRITE_APPROVAL" in text and not has_tool:
            delta = {"tool_calls": [{"index": 0, "id": "bound-file", "type": "function", "function": {
                "name": "mutate_file", "arguments": json.dumps(
                    {"path": "approval.txt", "expected_digest": None, "content": "Approved.\n"})}}]}
            finish = "tool_calls"
        else:
            delta = {"content": "Foreground done." if "SLOW_FOREGROUND" in text else "Ready."}
            finish = "stop"
        events = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        data = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "bound-fixture",
                           credential_env="PER_PULSE_BOUND_TEST_KEY"),
            transport=httpx.MockTransport(handler))

    return factory


def _page(client, instruction):
    response = client.post("/api/pages", json={"title": instruction, "instruction": instruction})
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def _until(predicate, label, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, label
        time.sleep(0.05)


def test_a_stuck_background_step_is_bounded_while_your_own_turns_are_not(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "BACKGROUND_TURN_TIMEOUT_SECONDS", BACKGROUND_BOUND)
    release = threading.Event()
    app = create_app(tmp_path, model_factory=_factory(monkeypatch, release),
                     allow_workspace_actions=True, auto_apply_edits=False)
    try:
        with TestClient(app, base_url="http://localhost") as client:
            pages = client.app.state.pages
            assert pages.runtime.config.harness_turn_timeout_sec is None
            assert pages.runtime.config.harness_background_turn_timeout_sec == BACKGROUND_BOUND
            execution = PageExecution(pages.runtime)

            def replies(page_id):
                return [m["content"] for m in client.get(f"/api/pages/{page_id}").json()["messages"]
                        if m["role"] == "assistant"]

            background = _page(client, "TICK_HANGS")
            _until(lambda: replies(background) == ["Ready."], "first reply")
            started = client.post(f"/api/pages/{background}/tick", json={"count": 1})
            assert started.status_code == 201, started.text

            # A person's own turn waiting for approval stays open past the bound.
            approval = _page(client, "WRITE_APPROVAL")
            _until(lambda: bool(execution.snapshot(approval)["pendingActions"]), "approval pending")

            # A newer foreground Work that takes longer than the bound finishes.
            began = time.monotonic()
            foreground = _page(client, "SLOW_FOREGROUND")
            _until(lambda: replies(foreground) == ["Foreground done."], "foreground reply")
            assert time.monotonic() - began >= SLOW_FOREGROUND > BACKGROUND_BOUND

            # The stuck background step is ended at its bound, not left running.
            _until(lambda: client.get(f"/api/pages/{background}").json()["tick"]["status"] != "running",
                   "background step ended", timeout=HANG + 5)
            tick = client.get(f"/api/pages/{background}").json()["tick"]
            assert tick["status"] == "stopped" and tick["reason"] != "operator"

            pending, = execution.snapshot(approval)["pendingActions"]
            assert pending["canResolve"], "a person's own approval must stay open"

            # The page sees the terminal turn before the coordinator harvests
            # its completed worker. Keep the app running until that ordinary
            # settlement is recorded, rather than racing the shutdown path.
            def timeout_settled():
                observed = [json.loads(line) for line in
                            (tmp_path / "metrics.jsonl").read_text().splitlines()]
                timed_out = {event["event_id"] for event in observed
                             if event.get("type") == "pulse_failed"
                             and event.get("code") == "world_turn_timeout"}
                return any(event.get("type") == "center_reservation_settled"
                           and event.get("event") in timed_out for event in observed)

            _until(timeout_settled, "background timeout coordinator settlement")
    finally:
        release.set()

    events = [json.loads(line) for line in (tmp_path / "metrics.jsonl").read_text().splitlines()]
    timeouts = [e for e in events if e.get("type") == "pulse_failed" and e.get("code") == "world_turn_timeout"]
    assert len(timeouts) == 1, timeouts
    settled = Counter(e["reservation"] for e in events if e.get("type") == "center_reservation_settled")
    assert settled and all(count == 1 for count in settled.values()), settled
