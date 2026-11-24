"""Keys and passwords in your content reach your model as they are.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Current agent tools pass message and file text to the model unchanged. Per
Pulse used to refuse such a message without a reply, fail a turn that read a
config file holding a key, and refuse to edit such a file. The HTTP model
transport is scripted; World/Kernel persistence and file effects are real.
"""
from __future__ import annotations

import hashlib
import json
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

KEY = "sk-live-0123456789abcdef0123"
CONFIG = f"DATABASE_URL=postgres://localhost/app\nAPI_KEY={KEY}\n"


def _factory(monkeypatch, requests, tool=None):
    monkeypatch.setenv("PER_PULSE_KEYS_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        messages = json.loads(request.content)["messages"]
        requests.append(messages)
        if tool is not None and not any(m.get("role") == "tool" for m in messages):
            name, arguments = tool
            delta = {"tool_calls": [{"index": 0, "id": "keys-tool", "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments)}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": "Done."}, "stop"
        events = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        data = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "keys-fixture",
                           credential_env="PER_PULSE_KEYS_TEST_KEY"),
            transport=httpx.MockTransport(handler))

    return factory


def _reply(client, page_id):
    execution = PageExecution(client.app.state.pages.runtime)
    deadline = time.monotonic() + 15
    while True:
        messages = client.get(f"/api/pages/{page_id}").json()["messages"]
        replies = [m["content"] for m in messages if m["role"] == "assistant"]
        if replies:
            return replies[-1]
        assert execution.snapshot(page_id)["status"] != "error", "the turn failed"
        assert time.monotonic() < deadline, "no reply"
        time.sleep(0.05)


def _page(client, instruction):
    return client.post("/api/pages", json={"title": "Keys", "instruction": instruction}).json()["page"]["id"]


def test_a_key_in_your_message_reaches_the_model(tmp_path, monkeypatch):
    requests = []
    with TestClient(create_app(tmp_path, model_factory=_factory(monkeypatch, requests)),
                    base_url="http://localhost") as client:
        page_id = _page(client, f"Use this key for the deploy: {KEY}")
        assert _reply(client, page_id) == "Done."
    assert KEY in json.dumps(requests[-1])


def test_the_model_reads_a_config_file_that_holds_a_key(tmp_path, monkeypatch):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "config.env").write_text(CONFIG, encoding="utf-8", newline="")
    requests = []
    factory = _factory(monkeypatch, requests, tool=("read_file", {"path": "config.env"}))
    with TestClient(create_app(tmp_path, model_factory=factory, allow_workspace_actions=True),
                    base_url="http://localhost") as client:
        page_id = _page(client, "Check config.env")
        assert _reply(client, page_id) == "Done."
    tool_result = next(m for m in requests[-1] if m.get("role") == "tool")
    assert KEY in tool_result["content"]


def test_the_model_edits_a_file_that_holds_a_key(tmp_path, monkeypatch):
    (tmp_path / "workspace").mkdir()
    config = tmp_path / "workspace" / "config.env"
    config.write_text(CONFIG, encoding="utf-8", newline="")
    edit = {"path": "config.env", "expected_digest": hashlib.sha256(CONFIG.encode()).hexdigest(),
            "patch": {"edits": [{"old": "postgres://localhost/app", "new": "postgres://db/app"}]}}
    factory = _factory(monkeypatch, [], tool=("mutate_file", edit))
    with TestClient(create_app(tmp_path, model_factory=factory, allow_workspace_actions=True,
                               auto_apply_edits=True), base_url="http://localhost") as client:
        page_id = _page(client, "Point the app at the db host.")
        assert _reply(client, page_id) == "Done."
        assert config.read_text(encoding="utf-8") == CONFIG.replace("postgres://localhost/app", "postgres://db/app")
        item, = client.get(f"/api/pages/{page_id}/artifacts").json()["artifacts"]
        assert client.post(f"/api/pages/{page_id}/artifacts/{item['id']}/undo", json={}).status_code == 200
        assert config.read_text(encoding="utf-8") == CONFIG
