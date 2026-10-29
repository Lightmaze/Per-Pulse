"""Synthetic choices used to inspect real focused replay requests and effects.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json

import httpx

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.service.native_replay import KIND
from replay_fixture import ORIGINAL, RECONSIDERED, ReplayFixture

FOCUS = "Could a visitor inspect one example before registration?"
UNRELATED = "Unselected conversation: the archive shelf is copper."
PREAMBLE = "Unselected entry preamble: the old booking was cancelled."
REASONING = "Unselected pre-entry reasoning: the old meeting was on Monday."
ADVICE = "Unselected experience advice: the previous customer preferred telephone contact."


class FocusedReplayFixture(ReplayFixture):
    def __init__(self, setenv, **kwargs):
        super().__init__(setenv, **kwargs)
        self.focused_requests = []
        self.auxiliary_requests = []
        self.knowledge_requests = []

    @staticmethod
    def entry_response(arguments):
        delta = {"content": PREAMBLE, "reasoning_content": REASONING, "tool_calls": [{
            "index": 0, "id": "fixture-replay-enter", "type": "function",
            "function": {"name": "replay", "arguments": json.dumps(arguments)},
        }]}
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    def handler(self, request):
        body = json.loads(request.content)
        messages = body["messages"]
        if "UNTRUSTED_REVIEW_DATA_JSON" in json.dumps(messages):
            self.requests.append(body)
            self.knowledge_requests.append(body)
            return self.response('{"mode":"none"}')
        if "WORK_MATERIAL_JSON" in json.dumps(messages):
            self.requests.append(body)
            self.auxiliary_requests.append(body)
            return self.response(ADVICE if "RECONSIDER_SAVED_DESIGN" in json.dumps(messages) else "PASS")
        current = max(i for i, message in enumerate(messages) if message["role"] == "user")
        task = str(messages[current]["content"])
        results = [json.loads(m["content"]) for m in messages[current + 1:] if m["role"] == "tool"]
        entry = next((r for r in results if r.get("output", {}).get("kind") == KIND
                      and r["output"].get("focus") == FOCUS), None)
        if "RECONSIDER_SAVED_DESIGN" not in task and entry is None:
            return super().handler(request)
        self.requests.append(body)
        if not results:
            return self.response(tool="search_history", arguments={"query": ORIGINAL})
        assert results[-1]["status"] == "succeeded", results[-1]
        if entry is None:
            self.selected = [m["source"] for m in results[-1]["output"]["matches"]
                             if m["source"]["role"] == "assistant"][:1]
            assert len(self.selected) == 1
            return self.entry_response({"op": "enter", "message_ids": [self.selected[0]["message_id"]],
                                        "focus": FOCUS})
        self.focused_requests.append(body)
        assert {tool["function"]["name"] for tool in body["tools"]} == {"replay"}
        if results[-1] == entry:
            self.entered.set()
            assert self.release.wait(30), "The test must release its provider fixture."
            if self.forbidden_tool:
                return self.response(tool="mutate_file", arguments={"path": "forbidden.txt",
                    "content": "Must not be written.", "expected_digest": None})
            return self.response(tool="replay", arguments={"op": "read", "message_id": self.selected[0]["message_id"]})
        assert results[-1]["output"]["content"] == ORIGINAL
        return self.response(RECONSIDERED)

    def factory(self, _scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "explicit-focused-replay-fixture",
            credential_env="PER_PULSE_REPLAY_FIXTURE_KEY", reasoning_field="reasoning_content",
        ), transport=httpx.MockTransport(self.handler))
