"""Explicit synthetic HTTP choices for saved tool-result browser acceptance.

World, Kernel, read_file, history tools, SQLite and file bytes are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import traceback

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider
from pulse_harness.core.context_window import OMITTED_NOTE


OLD = "ARCHIVED_TOOL_ONLY_C82"
NEW = "CURRENT_FILE_ONLY_D97"
FILENAME = "observations.txt"
LABEL = " [Explicit synthetic HTTP fixture]"
ORIGINAL = OLD + "\n" + "\n".join(
    f"Archived field passage {index:02d}: 山川湖海，原始记录保留当时文字，不以当前文件替换。"
    for index in range(24)) + "\n"


def streamed(text=None, *, tool=None, arguments=None, sequence=0):
    delta = {"content": text} if tool is None else {"tool_calls": [{
        "index": 0, "id": f"tool-history-fixture-{sequence}", "type": "function",
        "function": {"name": tool, "arguments": json.dumps(arguments)},
    }]}
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {},
                      "finish_reason": "stop" if tool is None else "tool_calls"}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(
        "data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")


class ToolHistoryFixture:
    def __init__(self, directory):
        self.directory = directory
        self.manifest = directory / "tool-history-fixture.json"
        self.saved = json.loads(self.manifest.read_text()) if self.manifest.exists() else {}
        self.requests, self.checks, self.failures = [], [], []
        os.environ["PER_PULSE_TOOL_HISTORY_FIXTURE_KEY"] = "explicit-synthetic-credential"

    def save(self, **values):
        self.saved.update(values)
        self.manifest.write_text(json.dumps(self.saved))

    def original_row(self):
        with sqlite3.connect(self.directory / "kernel.db") as db:
            db.row_factory = sqlite3.Row
            if "original_row" in self.saved:
                row = db.execute("SELECT * FROM transcript_messages WHERE message_id = ?",
                                 (self.saved["original_row"]["message_id"],)).fetchone()
            else:
                row = db.execute("SELECT * FROM transcript_messages WHERE role = 'tool' "
                                 "AND instr(content, ?) > 0 ORDER BY transcript_seq LIMIT 1", (OLD,)).fetchone()
            return dict(row) if row is not None else None

    def evidence(self):
        with sqlite3.connect(self.directory / "kernel.db") as db:
            reads = db.execute("SELECT COUNT(*) FROM actions WHERE tool_name = 'read_file'").fetchone()[0]
        original = self.original_row()
        with sqlite3.connect(self.directory / "world.db") as db:
            turn = db.execute("SELECT event_id FROM harness_turns WHERE id = ?",
                              (original["turn_id"],)).fetchone() if original else None
        return {"requests": len(self.requests), "checks": self.checks, "failures": self.failures,
                "read_file_actions": reads, "original_event_id": turn[0] if turn else None,
                "original_unchanged": original == self.saved.get("original_row"),
                **self.saved}

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        try:
            return self.handle(body)
        except Exception:
            failure = traceback.format_exc()
            self.failures.append(failure)
            print(failure, flush=True)
            return streamed("Synthetic fixture assertion failed." + LABEL)

    def handle(self, body):
        messages = body["messages"]
        known_calls = {}
        tool_messages = []
        for message in messages:
            for call in message.get("tool_calls", []):
                known_calls[call["id"]] = call["function"]["name"]
            if message["role"] == "tool":
                assert message["tool_call_id"] in known_calls, "tool output retains its actual call identity"
                tool_messages.append((message, known_calls[message["tool_call_id"]]))
        current = max(i for i, message in enumerate(messages) if message["role"] == "user")
        task = str(messages[current]["content"])
        results = [json.loads(message["content"]) for message in messages[current + 1:]
                   if message["role"] == "tool"]
        sequence = len(results)

        def call(tool, arguments):
            return streamed(tool=tool, arguments=arguments, sequence=sequence)

        def success(index):
            assert results[index]["status"] == "succeeded", results[index]
            return results[index]["output"]

        if "CAPTURE_TOOL_HISTORY" in task:
            if not results:
                return call("read_file", {"path": FILENAME})
            output = success(0)
            assert output["content"] == ORIGINAL and not output["truncated"], output
            row = self.original_row()
            assert row
            stored = json.loads(row["content"])
            assert {key: stored.get(key) for key in ("status", "output", "error_code")} == results[0]
            assert self.evidence()["read_file_actions"] == 1
            assert self.saved["read_file_invocations"] == 1
            self.save(original_row=row)
            self.checks.append("real read_file stores its original result in the immutable transcript")
            # The private marker deliberately never enters this assistant summary.
            return streamed("Read the file and kept the original tool receipt." + LABEL)

        if "HISTORY_WINDOW_FILLER" in task:
            assert all(OLD not in message.get("content", "") for message in messages
                       if message["role"] in {"user", "assistant"}), "no discussion summary may leak the old marker"
            return streamed("Continuation received. " + "Keep the current discussion readable. " * 24 + LABEL)

        if "RECALL_TOOL_HISTORY" in task:
            if not results:
                assert OMITTED_NOTE in [message.get("content") for message in messages], "native window really clipped history"
                assert all(OLD not in message.get("content", "") for message in messages), "old marker is unavailable before recall"
                assert all(name != "read_file" for _message, name in tool_messages), "the original read_file exchange left the actual request"
                assert self.original_row() == self.saved["original_row"], "clipping preserves the saved original row"
                assert NEW in (self.directory / "workspace" / FILENAME).read_text(), "physical file already changed"
                self.checks.append("before recall the actual HTTP window has neither original read result nor a marker-bearing summary")
                return call("search_history", {"query": '"workspace_provenance"'})
            discussion = success(0)
            assert discussion["matches"] == [] and discussion["coverage"]["roles"] == ["user", "assistant"]
            if sequence == 1:
                return call("search_history", {"query": OLD, "role": "tool", "limit": 1})
            found = success(1)
            assert found["coverage"]["roles"] == ["tool"] and found["next_cursor"] is None
            assert len(found["matches"]) == 1, "prior history-read copies do not multiply the original result"
            source = found["matches"][0]["source"]
            row, original = self.saved["original_row"], json.loads(self.saved["original_row"]["content"])
            for key in ("session_id", "engram_id", "message_id", "event_id", "turn_id", "transcript_seq", "content_digest"):
                assert source[key] == row[key], (key, source, row)
            assert source["role"] == "tool" and source["tool_name"] == "read_file"
            assert source["action_request_id"] == original["action_request_id"]
            assert source["tool_call_id"] == original["tool_call_id"]
            assert source["action_status"] == "succeeded" and source["result_delivered"] is True
            if sequence == 2:
                return call("read_history", {"message_id": source["message_id"], "role": "tool", "max_chars": 512})
            passages = [success(index) for index in range(2, sequence)]
            offset = 0
            for passage in passages:
                assert passage["source"] == source and passage["offset"] == offset
                assert len(passage["content"]) <= 512
                offset += len(passage["content"])
            last = passages[-1]
            if last["next_offset"] is not None:
                assert last["next_offset"] == offset
                return call("read_history", {"message_id": source["message_id"], "role": "tool",
                                             "offset": last["next_offset"], "max_chars": 512})
            recovered = "".join(passage["content"] for passage in passages)
            assert recovered == row["content"] and offset == last["total_chars"]
            assert len(passages) >= 3 and "山川湖海" in recovered, "Unicode text spans actual character-offset pages"
            content = json.loads(recovered)["output"]["content"]
            assert content == ORIGINAL and NEW not in content
            assert self.evidence()["read_file_actions"] == 1, "history did not re-execute the original tool"
            assert self.saved["read_file_invocations"] == 1, "the actual workspace read method still ran only once"
            self.save(last_source=source, recovered_content=content, passage_count=len(passages))
            self.checks.append("role=tool search and character-offset reads recover the exact old JSON with canonical source and no repeated file action")
            # The rendered finding is derived from actual tool bytes, not a canned expected answer.
            return streamed("Recovered archived file content: " + content.splitlines()[0] + "." + LABEL)

        if "PROBE_OTHER_WORK" in task:
            if not results:
                assert OLD not in json.dumps(messages), "another Work receives no original tool text"
                return call("search_history", {"query": OLD, "role": "tool"})
            assert success(0)["matches"] == []
            if sequence == 1:
                return call("read_history", {"message_id": self.saved["original_row"]["message_id"], "role": "tool"})
            assert results[1]["status"] == "failed" and OLD not in json.dumps(results[1])
            self.checks.append("another Work can neither search nor directly read the original tool message")
            return streamed("This independent Work cannot read the original tool result." + LABEL)

        return streamed("Ready for the independent Work." + LABEL)

    def factory(self, _scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "tool-history-fixture",
            credential_env="PER_PULSE_TOOL_HISTORY_FIXTURE_KEY"), transport=httpx.MockTransport(self.handler))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    workspace = args.data_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    if not (workspace / FILENAME).exists():
        (workspace / FILENAME).write_text(ORIGINAL, encoding="utf-8")
    os.environ["P_PULSE_CONTEXT_TOKENS"] = "1024"
    fixture = ToolHistoryFixture(args.data_dir)
    real_read = StructuredWorkspaceToolProvider._read

    def counted_read(provider, *arguments, **keywords):
        # Count the real method across process restarts without substituting its result.
        fixture.save(read_file_invocations=fixture.saved.get("read_file_invocations", 0) + 1)
        return real_read(provider, *arguments, **keywords)

    StructuredWorkspaceToolProvider._read = counted_read
    app = create_app(args.data_dir, model_factory=fixture.factory, posture=False,
                     pulse=PulseSettings(enabled=False))

    @app.get("/__tool_history/evidence")
    def evidence():
        return fixture.evidence()

    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
