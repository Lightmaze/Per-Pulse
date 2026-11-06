"""One native World controls provider sends and restores the weekly policy.

Only the HTTP reply is synthetic. The page, Kernel, causal ledger, adapter,
World SQLite, API and restart path are real; no learning claims are made.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def _factory(monkeypatch, calls):
    monkeypatch.setenv("PER_PULSE_WEEK_HOST_KEY", "synthetic-credential")

    def handler(request):
        calls.append(request)
        frames = [
            {"choices": [{"index": 0, "delta": {"content": "Fixture result."},
                          "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        payload = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=payload + "data: [DONE]\n\n",
        )

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig(
                "https://fixture.invalid/chat/completions", "weekly-host-test",
                credential_env="PER_PULSE_WEEK_HOST_KEY",
            ),
            transport=httpx.MockTransport(handler),
        )
    return factory


def _settled(client, page_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        view = client.get(f"/api/pages/{page_id}").json()
        if any(m["role"] == "assistant" for m in view["messages"]) and view["page"]["status"] != "working":
            return view
        time.sleep(.05)
    raise AssertionError("The real Kernel did not settle the fixture reply")


def test_host_binds_weekly_gate_but_keeps_direct_user_work_outside_pool(tmp_path, monkeypatch):
    calls = []
    app = create_app(tmp_path, model_factory=_factory(monkeypatch, calls))
    with TestClient(app, base_url="http://localhost") as client:
        status = client.get("/api/pulse/weekly-usage")
        assert status.status_code == 200
        assert status.json()["usage"]["enabled"] is False
        world = client.get("/api/bootstrap").json()["workspaceId"]
        enabled = client.patch(
            "/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 2},
            headers={"x-per-pulse-world": world},
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["usage"]["remaining"] == 2
        page = client.post(
            "/api/pages", json={"title": "Manual task", "instruction": "Write a sentence."}
        )
        assert page.status_code == 201, page.text
        _settled(client, page.json()["page"]["id"])
        assert calls
        status = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert status["consumed"] == 0
        assert status["remaining"] == 2
        assert status["world_id"] == world

    with TestClient(create_app(tmp_path, model_factory=_factory(monkeypatch, [])),
                    base_url="http://localhost") as client:
        restored = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert restored["enabled"] is True
        assert restored["weekly_limit"] == 2
        assert restored["remaining"] == 2
        assert client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": False, "weeklyLimit": 2},
        ).status_code == 200
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["enabled"] is False


def test_weekly_api_rejects_unconfigured_spending_and_invalid_world(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        status = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert not status["enabled"]
        enabled = client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": True, "weeklyLimit": 10},
        )
        assert enabled.status_code == 409
        assert enabled.json()["error"] == "provider_unconfigured"
        mismatch = client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": False, "weeklyLimit": 0},
            headers={"x-per-pulse-world": "another-world"},
        )
        assert mismatch.status_code == 409
        assert status["world_id"] != "another-world"
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["enabled"] is False


def test_example_cannot_enable_remote_provider_budget(tmp_path):
    with TestClient(create_app(tmp_path, example=True), base_url="http://localhost") as client:
        reply = client.patch(
            "/api/pulse/weekly-usage",
            json={"enabled": True, "weeklyLimit": 1},
        )
        assert reply.status_code == 409
        assert reply.json()["error"] == "example_mode"
