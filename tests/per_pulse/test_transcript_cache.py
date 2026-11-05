"""A long page does not reread its whole transcript on every model round.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted (a read_file step, then a reply);
World, Kernel, SQLite and the workspace are real.
"""
from __future__ import annotations

import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.session_store import HarnessStore


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_TRANSCRIPT_KEY", "synthetic-test-credential")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.md").write_text("Notes.\n", encoding="utf-8")

    def handler(request):
        messages = json.loads(request.content)["messages"]
        last_user = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        if not any(m.get("role") == "tool" for m in messages[last_user:]):
            delta = {"tool_calls": [{"index": 0, "id": f"read-{len(messages)}", "type": "function",
                                     "function": {"name": "read_file", "arguments": json.dumps({"path": "notes.md"})}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": "Synthetic fixture reply."}, "stop"
        events = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        body = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "transcript-fixture",
                           credential_env="PER_PULSE_TRANSCRIPT_KEY"),
            transport=httpx.MockTransport(handler))

    return TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost")


def _replies(client, page_id, count, timeout=15):
    deadline = time.monotonic() + timeout
    while sum(m["role"] == "assistant" for m in client.get(f"/api/pages/{page_id}").json()["messages"]) < count:
        assert time.monotonic() < deadline, "the reply never came"
        time.sleep(0.05)


def test_rounds_read_only_new_transcript_rows_and_start_over_after_an_outside_change(tmp_path, monkeypatch):
    reads: list[int] = []
    original = HarnessStore.transcript_snapshot

    def recording(self, scope, *, after_transcript_seq=0):
        if not scope.turn_id:
            reads.append(after_transcript_seq)
        return original(self, scope, after_transcript_seq=after_transcript_seq)

    monkeypatch.setattr(HarnessStore, "transcript_snapshot", recording)
    with _client(tmp_path, monkeypatch) as client:
        page_id = client.post("/api/pages", json={"title": "Notes", "instruction": "Read the notes."}).json()["page"]["id"]
        _replies(client, page_id, 1)
        for count in (2, 3):
            assert client.post(f"/api/pages/{page_id}/messages", json={"content": "Again.", "references": []}).status_code == 202
            _replies(client, page_id, count)
        # Session reads after the first start from the rows already held.
        assert reads[0] == 0 and reads[1:] and all(after > 0 for after in reads[1:])

        # Another connection commits: the next read starts over from the beginning.
        # Change a value and put it back: the content is the same, but the
        # store cannot know that without reading again.
        outside = sqlite3.connect(tmp_path / "kernel.db", isolation_level=None)
        session_id, updated_at = outside.execute("SELECT session_id, updated_at FROM sessions LIMIT 1").fetchone()
        outside.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", ("changed", session_id))
        outside.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (updated_at, session_id))
        outside.close()
        reads.clear()
        assert client.post(f"/api/pages/{page_id}/messages", json={"content": "Once more.", "references": []}).status_code == 202
        _replies(client, page_id, 4)
        assert 0 in reads[:2]
        replies = [m["content"] for m in client.get(f"/api/pages/{page_id}").json()["messages"] if m["role"] == "assistant"]
        assert replies == ["Synthetic fixture reply."] * 4
