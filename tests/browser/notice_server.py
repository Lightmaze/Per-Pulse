"""Explicit synthetic HTTP choices for native local Notice browser acceptance.

The real World, Kernel, reading/history tools and SQLite perform every effect.
No external model provider is contacted.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import traceback

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


PRIVATE = "LOCAL_NOTICE_OWNER_PRIVATE_7A2"
NOTE = PRIVATE + ": Preserve the literal [REDACTED] marker and its original neighborhood. " + (
    "Keep this detail attributable to its own Work. " * 25)
KIND = "pulse.local-notices.v1"
LABEL = " [Explicit synthetic HTTP fixture]"


def streamed(text=None, *, tool=None, arguments=None, sequence=0):
    delta = {"content": text} if tool is None else {"tool_calls": [{
        "index": 0, "id": f"notice-fixture-{sequence}", "type": "function",
        "function": {"name": tool, "arguments": json.dumps(arguments)},
    }]}
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {},
                      "finish_reason": "stop" if tool is None else "tool_calls"}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
    ]
    wire = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=wire + "data: [DONE]\n\n")


class NoticeFixture:
    def __init__(self, directory):
        self.directory = directory
        self.manifest = directory / "notice-fixture.json"
        self.saved = json.loads(self.manifest.read_text()) if self.manifest.exists() else {}
        self.requests = []
        self.checks = []
        self.failures = []
        os.environ["PER_PULSE_NOTICE_FIXTURE_KEY"] = "explicit-synthetic-credential"

    def save(self, **values):
        self.saved.update(values)
        self.manifest.write_text(json.dumps(self.saved))

    @staticmethod
    def envelope(messages):
        found = []
        for message in messages:
            try:
                value = json.loads(message.get("content", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and value.get("kind") == KIND:
                assert message["role"] == "assistant", "Notice data gains no system authority"
                assert "tool_calls" not in message, "Notice data is not a pending tool call"
                found.append(value)
        assert len(found) <= 1, "a request has only the current Notice projection"
        return found[0] if found else {"notices": [], "total_pending": 0}

    def pending(self, messages, expected):
        memory = self.envelope(messages)
        actual = [notice["id"] for notice in memory["notices"]]
        assert (self.saved.get("notice_id") in actual) is expected, (expected, memory)
        assert memory["total_pending"] == int(expected), memory
        if expected:
            assert PRIVATE in memory["notices"][0]["content"]
        return memory

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
        assert "notice" in [tool["function"]["name"] for tool in body.get("tools", [])]
        known_calls = set()
        for message in messages:
            known_calls.update(call["id"] for call in message.get("tool_calls", []))
            if message["role"] == "tool":
                assert message["tool_call_id"] in known_calls, "HTTP tool result retains its actual call identity"
        current = max(i for i, message in enumerate(messages) if message["role"] == "user")
        task = str(messages[current]["content"])
        results = [json.loads(message["content"]) for message in messages[current + 1:]
                   if message["role"] == "tool"]
        sequence = len(results)

        def call(tool, arguments):
            return streamed(tool=tool, arguments=arguments, sequence=sequence)

        def success(index=-1):
            result = results[index]
            assert result["status"] == "succeeded", result
            return result["output"]

        if "REGISTER_LOCAL_NOTICE" in task:
            if not results:
                self.pending(messages, False)
                return call("notice", {"op": "record", "content": NOTE})
            registered = success(0)
            assert registered["effect"] == "recorded", registered
            notice = registered["notice"]
            assert notice["content"] == NOTE and notice["status"] == "pending"
            self.save(notice_id=notice["id"], revision=notice["revision"], source=notice["source"])
            self.pending(messages, True)
            if sequence == 1:
                self.checks.append("record visible in immediately following HTTP model round")
                return call("reading", {"op": "open", "path": "large-notice-source.txt", "max_chars": 8000})
            passages = [success(index) for index in range(1, sequence)]
            assert all(passage["kind"] == "pulse.reading-passage.v1" for passage in passages)
            last = passages[-1]
            if last["next"] is not None:
                return call("reading", {"op": "continue", "receipt_id": last["receipt_id"], "max_chars": 8000})
            assert sum(len(passage["content"]) for passage in passages) >= 24000
            self.checks.append("Notice survives all actual large reading continuations")
            return streamed("Registered the local notice and read the large source." + LABEL)

        if "CHECK_AFTER_WINDOW" in task or "CHECK_ACTIVE_RESTART" in task:
            self.pending(messages, True)
            assert any("Earlier messages in this conversation are left out" in str(message.get("content"))
                       for message in messages), "the configured native context window really clipped history"
            historical_results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
            assert not any(result.get("output", {}).get("effect") == "recorded"
                           for result in historical_results), "the original record receipt left the model window"
            if not results:
                self.checks.append("active summary remains after native context clipping")
                return call("notice", {"op": "list"})
            listed = success(0)["notices"]
            assert len(listed) == 1 and listed[0]["id"] == self.saved["notice_id"]
            if sequence == 1:
                return call("notice", {"op": "read", "id": self.saved["notice_id"], "max_chars": 2048})
            read = success(1)
            assert read["notice"]["content"] == NOTE
            assert read["notice"]["status"] == "pending"
            assert read["notice"]["source_outcome"] == {
                "world_turn": "settled", "kernel_turn": "succeeded",
                "kernel_action": "succeeded", "result_delivered": True,
            }, read["notice"]["source_outcome"]
            passage = read["source_passage"]
            assert passage["available"] is True and passage["kind"] == "kernel_registration_neighborhood"
            assert PRIVATE in passage["content"], passage
            assert passage["source"]["message_id"] == self.saved["source"]["registration_message_id"]
            preceding = next(neighbor for neighbor in read["neighbors"] if neighbor["relative_message"] == -1)
            assert preceding["source"]["role"] == "user", preceding
            if sequence == 2:
                return call("read_history", {"message_id": preceding["source"]["message_id"], "max_chars": 4096})
            original = success(2)
            assert "REGISTER_LOCAL_NOTICE" in original["content"]
            assert original["source"]["message_id"] == preceding["source"]["message_id"]
            self.checks.append("Notice registration neighborhood agrees with native immutable user history")
            self.save(last_read=read)
            return streamed(("The active notice survived restart." if "CHECK_ACTIVE_RESTART" in task else
                             "The notice survived context clipping; its original registration is readable.") + LABEL)

        if "PROBE_SIBLING_NOTICE" in task:
            assert PRIVATE not in json.dumps(messages), "sibling Work cannot receive the private Notice"
            self.pending(messages, False)
            if not results:
                return call("notice", {"op": "list", "status": "all"})
            assert success(0)["notices"] == []
            if sequence == 1:
                return call("notice", {"op": "read", "id": self.saved["notice_id"]})
            assert results[1]["status"] == "failed", results[1]
            assert PRIVATE not in json.dumps(results[1])
            if sequence == 2:
                return call("notice", {"op": "close", "id": self.saved["notice_id"],
                                       "expected_revision": self.saved["revision"]})
            assert results[2]["status"] == "failed", results[2]
            assert PRIVATE not in json.dumps(results[2])
            self.checks.append("sibling Work list/read/close cannot inspect or alter the Notice")
            return streamed("Sibling Work cannot list, read, or close the private notice." + LABEL)

        if "CLOSE_LOCAL_NOTICE" in task:
            if not results:
                self.pending(messages, True)
                return call("notice", {"op": "close", "id": self.saved["notice_id"],
                                       "expected_revision": self.saved["revision"] + 1})
            assert results[0]["status"] == "failed", "a stale revision must not close a Notice"
            if sequence == 1:
                self.pending(messages, True)
                return call("notice", {"op": "close", "id": self.saved["notice_id"],
                                       "expected_revision": self.saved["revision"]})
            closed = success(1)
            assert closed["effect"] == "closed" and closed["notice"]["status"] == "closed"
            self.pending(messages, False)
            if sequence == 2:
                assert closed["notice"]["revision"] > self.saved["revision"]
                previous = self.saved["revision"]
                self.save(revision=closed["notice"]["revision"])
                return call("notice", {"op": "close", "id": self.saved["notice_id"],
                                       "expected_revision": previous})
            assert results[2]["status"] == "failed", "an old revision cannot close an already closed Notice"
            self.checks.append("revision conflict and stale re-close rejected; confirmed close removes next-round reminder")
            return streamed("Closed the local notice; its history is retained." + LABEL)

        if "CHECK_CLOSED_NOTICE" in task:
            self.pending(messages, False)
            if not results:
                return call("notice", {"op": "list"})
            assert success(0)["notices"] == [], "closed Notices are not pending"
            if sequence == 1:
                return call("notice", {"op": "list", "status": "all"})
            closed = success(1)["notices"]
            assert len(closed) == 1 and closed[0]["status"] == "closed"
            if sequence == 2:
                return call("notice", {"op": "read", "id": self.saved["notice_id"], "max_chars": 2048})
            read = success(2)
            assert read["notice"]["status"] == "closed" and read["notice"]["content"] == NOTE
            assert read["notice"]["source"] == self.saved["source"]
            assert read["source_passage"]["available"] is True
            self.checks.append("closed history and original source persist without active reminder")
            return streamed("Closed history and source remain readable after restart." + LABEL)

        return streamed("Ready for this local Work." + LABEL)

    def factory(self, _scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "explicit-native-notice-fixture",
            credential_env="PER_PULSE_NOTICE_FIXTURE_KEY",
        ), transport=httpx.MockTransport(self.handler))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    workspace = args.data_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "large-notice-source.txt").write_text(
        "\n".join(f"Authored large source line {index:04d}: inspect the actual passage, retaining source identity."
                  for index in range(360)) + "\n")
    # This browser journey verifies multi-passage Notice use and later history
    # clipping, not the independent 1024-token single-tool-result paging case.
    # Keep one active 24k-source reading turn within the model's window; the
    # browser creates subsequent real turns to exercise historical clipping.
    os.environ["P_PULSE_CONTEXT_TOKENS"] = "16384"
    fixture = NoticeFixture(args.data_dir)
    # Keep the production default enabled=False, with a short observer interval
    # so an accidental activation becomes visible during the idle check.
    app = create_app(args.data_dir, model_factory=fixture.factory, posture=False,
                     pulse=PulseSettings(check_interval_seconds=0.05))

    @app.get("/__notice/evidence")
    def evidence():
        return {"requests": len(fixture.requests), "checks": fixture.checks,
                "failures": fixture.failures, **fixture.saved}

    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
