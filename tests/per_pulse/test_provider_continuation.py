"""A budget hold resumes retained work without repeating a confirmed effect.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from test_weekly_host import _enqueue_self, _settled


def until(read, ready, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = read()
        if ready(value):
            return value
        time.sleep(.03)
    raise AssertionError(f"The native continuation did not settle: {value}")


def test_budget_exhausted_after_file_write_survives_restart_and_continues_without_rewriting(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_CONTINUE_KEY", "synthetic-credential")
    calls = []
    def factory(_scope):
        def handler(request):
            calls.append(request)
            messages = json.loads(request.content)["messages"]
            boundary = max(i for i, item in enumerate(messages) if item["role"] == "user")
            current = messages[boundary:]
            if "WRITE_RESUME" in messages[boundary]["content"] and not any(item["role"] == "tool" for item in current):
                delta = {"tool_calls": [{"index": 0, "id": "fixture-write", "type": "function", "function": {
                    "name": "mutate_file", "arguments": json.dumps({"path": "retained.txt", "expected_digest": None,
                                                                  "content": "Keep this exact effect.\n"})}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"content": "The retained work is complete. [Synthetic fixture]"}, "stop"
            frames = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(item) + "\n\n" for item in frames) + "data: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "continuation-fixture",
            credential_env="PER_PULSE_CONTINUE_KEY", max_retries=0), transport=httpx.MockTransport(handler))
    settings = PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0)
    def app():
        return create_app(tmp_path, pulse=settings, posture=False, model_factory=factory,
                          allow_workspace_actions=True, auto_apply_edits=True)
    with TestClient(app(), base_url="http://localhost") as client:
        work = client.post("/api/pages", json={"title": "Retained work", "instruction": "Initial context."}).json()["page"]["id"]
        _settled(client, work)
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 1}).status_code == 200
        runtime = client.app.state.pages.runtime
        center = runtime.world.get_task_front(work).center_id
        root = _enqueue_self(runtime, "WRITE_RESUME", "fixture-write-resume", center_id=center)
        target = tmp_path / "workspace" / "retained.txt"
        until(lambda: runtime.causal_ledger.get_event(root), lambda event: event.status.value == "uncertain")
        assert target.read_text() == "Keep this exact effect.\n"
        mtime = target.stat().st_mtime_ns
        queued = until(lambda: runtime.causal_ledger.list_events(world_id=runtime.world_id, status="queued", limit=100),
                       lambda rows: any(event.metadata.get("context_pulse") == "provider_continuation" for event in rows), timeout=5)
        continuation = next(event.id for event in queued if event.metadata.get("context_pulse") == "provider_continuation")
        held = client.get(f"/api/pages/{work}").json()["execution"]
        assert held["providerWaiting"] is True
        assert held["recoveryRequired"] is False and held["canCancel"] is False
        # Another foreground context remains usable while this one waits.
        assert client.post("/api/pages/home/messages", json={"content": "A direct foreground question."}).status_code == 202
        _settled(client, "home")
        before = len(calls)
    with TestClient(app(), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        assert runtime.causal_ledger.get_event(continuation).status.value == "queued"
        assert len(calls) == before
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 2}).status_code == 200
        until(lambda: runtime.causal_ledger.get_event(continuation), lambda event: event.status.value == "settled")
        assert target.read_text() == "Keep this exact effect.\n"
        assert target.stat().st_mtime_ns == mtime
        assert len(calls) == before + 1
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == 2
        assert client.get(f"/api/pages/{work}").json()["artifacts"]
