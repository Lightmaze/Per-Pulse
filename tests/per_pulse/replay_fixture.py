"""Explicit synthetic provider decisions; the native replay machinery stays real.

Shared by the Python and Chromium journeys. No model learning is measured.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import threading
import httpx
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


ORIGINAL = "The first design assumed every visitor already had an account."
RECONSIDERED = "A tentative alternative: let a visitor inspect one example before registration."


class ReplayFixture:
    """Actual tool requests with a controlled provider, not a mocked runtime."""

    def __init__(self, setenv, *, hold=False, forbidden_tool=False):
        setenv("PER_PULSE_REPLAY_FIXTURE_KEY", "explicit-synthetic-credential")
        self.requests = []
        self.selected = []
        self.entered, self.release = threading.Event(), threading.Event()
        if not hold:
            self.release.set()
        self.forbidden_tool = forbidden_tool

    @staticmethod
    def response(text=None, *, tool=None, arguments=None):
        delta = {"content": text} if tool is None else {"tool_calls": [{
            "index": 0, "id": "fixture-" + tool + "-" + arguments.get("op", "call"), "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)},
        }]}
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop" if tool is None else "tool_calls"}]},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        if "WORK_MATERIAL_JSON" in json.dumps(messages):
            return self.response("PASS")
        current = max(i for i, message in enumerate(messages) if message["role"] == "user")
        task = str(messages[current]["content"])
        results = [json.loads(m["content"]) for m in messages[current + 1:] if m["role"] == "tool"]
        if "RECONSIDER_SAVED_DESIGN" in task:
            if not results:
                return self.response(tool="search_history", arguments={"query": ORIGINAL})
            assert results[-1]["status"] == "succeeded", results[-1]
            if len(results) == 1:
                self.selected = [m["source"] for m in results[-1]["output"]["matches"]
                                 if m["source"]["role"] == "assistant"][:1]
                assert len(self.selected) == 1
                return self.response(tool="replay", arguments={"op": "enter", "message_ids": [self.selected[0]["message_id"]]})
            assert {tool["function"]["name"] for tool in body["tools"]} == {"replay"}
            if len(results) == 2:
                self.entered.set()
                assert self.release.wait(30), "The test must release its provider fixture."
                if self.forbidden_tool:
                    return self.response(tool="mutate_file", arguments={"path": "forbidden.txt", "content": "Must not be written.", "expected_digest": None})
                return self.response(tool="replay", arguments={"op": "read", "message_id": self.selected[0]["message_id"]})
            assert results[-1]["output"]["content"] == ORIGINAL
            return self.response(RECONSIDERED)
        if "TRY_ORDINARY_ACTION" in task:
            if not results:
                assert "mutate_file" in {tool["function"]["name"] for tool in body["tools"]}
                return self.response(tool="mutate_file", arguments={"path": "practice.txt", "content": "The next ordinary turn can work.\n", "expected_digest": None})
            assert results[-1]["status"] == "succeeded"
            return self.response("The ordinary practice file was written after approval.")
        return self.response(ORIGINAL)

    def factory(self, _scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "explicit-replay-fixture",
            credential_env="PER_PULSE_REPLAY_FIXTURE_KEY",
        ), transport=httpx.MockTransport(self.handler))


