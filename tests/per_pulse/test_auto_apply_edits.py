"""File changes inside the workspace apply at once and can be undone.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted; file effects, checkpoints, World/Kernel
persistence and digests are real. No external model is contacted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.contracts import ToolDescriptor
from pulse_harness.core.turn_runner import TurnRunner


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _factory(monkeypatch, plans):
    monkeypatch.setenv("PER_PULSE_EDIT_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        messages = json.loads(request.content)["messages"]
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
        current = messages[last_user:]
        prompt = json.dumps(current)
        selected = next((value for marker, value in plans.items() if marker in prompt), None)
        if selected is not None and not any(m.get("role") == "tool" for m in current):
            delta = {"tool_calls": [{"index": 0, "id": "synthetic-edit", "type": "function", "function": {
                "name": "mutate_file", "arguments": json.dumps(selected),
            }}]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": "Synthetic assistant: done."}, "stop"
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "edit-fixture",
                           credential_env="PER_PULSE_EDIT_TEST_KEY"),
            transport=httpx.MockTransport(handler),
        )

    return factory


def _client(path: Path, **kwargs):
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def _page(client, instruction):
    response = client.post("/api/pages", json={"title": instruction, "instruction": instruction})
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def _settle(client, page_id, timeout=8):
    """Wait for the page's turn to finish; it must never ask for approval."""
    execution = PageExecution(client.app.state.pages.runtime)
    deadline = time.monotonic() + timeout
    view = None
    while time.monotonic() < deadline:
        view = execution.snapshot(page_id)
        assert view["pendingActions"] == [], "a file change inside the workspace asked for approval"
        if view["turnId"] and view["status"] in {"idle", "error"}:
            return view
        time.sleep(0.025)
    pytest.fail(f"Page did not finish: {view}")


def _undo(client, page_id, artifact_id):
    return client.post(f"/api/pages/{page_id}/artifacts/{artifact_id}/undo", json={})


def test_a_file_change_applies_without_approval_and_undo_puts_it_back(tmp_path, monkeypatch):
    original, revised = "Original notes.\n", "Revised notes.\n"
    (tmp_path / "workspace").mkdir()
    notes = tmp_path / "workspace" / "notes.md"
    notes.write_text(original, encoding="utf-8")
    factory = _factory(monkeypatch, {"REVISE_NOTES": {
        "path": "notes.md", "expected_digest": _sha(original), "content": revised,
    }})
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True,
                 auto_apply_edits=True) as client:
        assert client.get("/api/bootstrap").json()["provider"]["autoApplyEdits"] is True
        page_id = _page(client, "REVISE_NOTES")
        assert _settle(client, page_id)["status"] == "idle"
        assert notes.read_text(encoding="utf-8") == revised
        item, = client.get(f"/api/pages/{page_id}/artifacts").json()["artifacts"]
        assert item["path"] == "notes.md" and item["undone"] is False

        undone = _undo(client, page_id, item["id"])
        assert undone.status_code == 200, undone.text
        assert undone.json()["artifact"]["undone"] is True
        assert notes.read_text(encoding="utf-8") == original
        listed, = undone.json()["artifacts"]["artifacts"]
        assert listed["id"] == item["id"] and listed["undone"] is True
        assert client.get(f"/api/pages/{page_id}").json()["artifacts"][0]["undone"] is True

        again = _undo(client, page_id, item["id"])
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "artifact_already_undone"
        assert notes.read_text(encoding="utf-8") == original

        other = _page(client, "No file request.")
        assert _undo(client, other, item["id"]).status_code == 404


def test_undo_leaves_a_later_edit_alone_and_removes_a_new_file(tmp_path, monkeypatch):
    created = "A new draft.\n"
    factory = _factory(monkeypatch, {"WRITE_DRAFT": {
        "path": "draft.md", "expected_digest": None, "content": created,
    }})
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True,
                 auto_apply_edits=True) as client:
        page_id = _page(client, "WRITE_DRAFT")
        _settle(client, page_id)
        draft = tmp_path / "workspace" / "draft.md"
        assert draft.read_text(encoding="utf-8") == created
        item, = client.get(f"/api/pages/{page_id}/artifacts").json()["artifacts"]

        draft.write_text("Edited by hand.\n", encoding="utf-8")
        refused = _undo(client, page_id, item["id"])
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "artifact_version_changed"
        assert draft.read_text(encoding="utf-8") == "Edited by hand.\n"

        draft.write_text(created, encoding="utf-8")
        assert _undo(client, page_id, item["id"]).status_code == 200
        assert not draft.exists()


def test_asking_before_edits_keeps_the_approval(tmp_path, monkeypatch):
    factory = _factory(monkeypatch, {"WRITE_DRAFT": {
        "path": "draft.md", "expected_digest": None, "content": "Draft.\n",
    }})
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True,
                 auto_apply_edits=False) as client:
        assert client.get("/api/bootstrap").json()["provider"]["autoApplyEdits"] is False
        page_id = _page(client, "WRITE_DRAFT")
        execution = PageExecution(client.app.state.pages.runtime)
        deadline = time.monotonic() + 8
        while not execution.snapshot(page_id)["pendingActions"]:
            assert time.monotonic() < deadline, "the change did not ask for approval"
            time.sleep(0.025)
        assert not (tmp_path / "workspace" / "draft.md").exists()


def test_undo_is_refused_when_file_changes_are_off(tmp_path):
    with _client(tmp_path, model_factory=None, allow_workspace_actions=False) as client:
        page_id = _page(client, "A page.")
        refused = _undo(client, page_id, "missing-action")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "workspace_actions_disabled"


def test_commands_still_ask_when_file_changes_apply_at_once():
    policy = SimpleNamespace(auto_apply_capabilities=frozenset({"workspace.write"}))
    edit = ToolDescriptor(name="mutate_file", description="", mutable=True, requires_approval=True,
                          capability="workspace.write", source="test")
    command = ToolDescriptor(name="run_command", description="", mutable=True, requires_approval=True,
                             capability="workspace.command", source="test")
    assert TurnRunner._requires_approval(policy, edit) is False
    assert TurnRunner._requires_approval(policy, command) is True
    default = SimpleNamespace(auto_apply_capabilities=frozenset())
    assert TurnRunner._requires_approval(default, edit) is True
