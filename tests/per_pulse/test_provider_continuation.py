"""A budget hold resumes retained work without repeating a confirmed effect.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

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


@pytest.mark.parametrize("revise_purpose", [False, True])
def test_budget_exhausted_after_file_write_survives_restart_and_continues_without_rewriting(
        tmp_path, monkeypatch, revise_purpose):
    monkeypatch.setenv("PER_PULSE_CONTINUE_KEY", "synthetic-credential")
    calls = []
    def factory(_scope):
        def handler(request):
            calls.append(request)
            messages = json.loads(request.content)["messages"]
            boundary = max(i for i, item in enumerate(messages) if item["role"] == "user")
            current = messages[boundary:]
            notices = [i for i, item in enumerate(messages)
                       if item["role"] == "system"
                       and "Internal provider continuation, not a user message." in item["content"]]
            if revise_purpose and notices and notices[-1] > boundary:
                assert messages[boundary]["content"] == "Change the purpose: produce a planning brief instead."
                if not any(item["role"] == "tool" for item in messages[notices[-1]:]):
                    delta = {"tool_calls": [{"index": 0, "id": "fixture-revised-write", "type": "function",
                        "function": {"name": "mutate_file", "arguments": json.dumps({
                            "path": "planning-brief.md", "expected_digest": None,
                            "content": "# Planning brief\nVerify the assumptions before release.\n"})}}]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"content": "Planning brief completed. [Synthetic fixture]"}, "stop"
            elif messages[boundary]["content"] == "Change the purpose: produce a planning brief instead.":
                delta, finish = {"content": "Revision accepted. [Synthetic fixture]"}, "stop"
            elif "WRITE_RESUME" in messages[boundary]["content"] and not any(item["role"] == "tool" for item in current):
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
        if revise_purpose:
            response = client.post(f"/api/pages/{work}/messages",
                json={"content": "Change the purpose: produce a planning brief instead."})
            assert response.status_code == 202, response.text
            until(lambda: client.get(f"/api/pages/{work}").json(),
                  lambda view: any("Revision accepted." in item.get("content", "")
                                   for item in view["messages"] if item["role"] == "assistant")
                  and view["page"]["status"] != "working")
            assert runtime.causal_ledger.get_event(continuation).status.value == "queued"
            assert target.stat().st_mtime_ns == mtime
        before = len(calls)
    with TestClient(app(), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        assert runtime.causal_ledger.get_event(continuation).status.value == "queued"
        assert len(calls) == before
        limit = 3 if revise_purpose else 2
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": limit}).status_code == 200
        until(lambda: runtime.causal_ledger.get_event(continuation), lambda event: event.status.value == "settled")
        assert target.read_text() == "Keep this exact effect.\n"
        assert target.stat().st_mtime_ns == mtime
        assert len(calls) == before + (2 if revise_purpose else 1)
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == limit
        sent = json.loads(calls[before].content)["messages"]
        assert any(row["role"] == "system" and "Internal provider continuation" in row["content"]
                   for row in sent)
        assert not any(row["role"] == "user" and "previous turn reached the autonomous" in row["content"]
                       for row in sent)
        assert client.get(f"/api/pages/{work}").json()["artifacts"]
        if revise_purpose:
            brief = tmp_path / "workspace" / "planning-brief.md"
            assert brief.read_text() == "# Planning brief\nVerify the assumptions before release.\n"
            latest = next(row for row in reversed(sent) if row["role"] == "user")
            assert latest["content"] == "Change the purpose: produce a planning brief instead."
