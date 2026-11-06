"""Explicit synthetic model decisions for the native autonomous user flow.

Only HTTP replies are scripted. Work adoption, files, reports and accounting
use the production World and Kernel. Copyright 2026 Lightmaze Contributors.
Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import threading

import httpx

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.core.pulse.autonomy import FIRST_DEPARTURE_ORIENTATION, PROVIDER_CONTINUATION_ORIENTATION

PRACTICE_TITLE = "A small practice"
PRACTICE_TEXT = "A persisted result from explicitly synthetic autonomous practice.\n"
PRACTICE_INSTRUCTION = "AUTONOMOUS_PRACTICE_FIXTURE: create practice.txt and report the actual result."


def response(*, content=None, tool=None, arguments=None):
    delta = ({"tool_calls": [{"index": 0, "id": "fixture-" + tool + "-" + arguments.get("op", "call"),
              "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]}
             if tool else {"content": content})
    frames = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 6}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join("data: " + json.dumps(item) + "\n\n" for item in frames) + "data: [DONE]\n\n")


class AutonomousPracticeFixture:
    def __init__(self, setenv):
        setenv("PER_PULSE_AUTONOMOUS_FIXTURE_KEY", "explicit-synthetic-test-credential")
        self.requests = []
        self.lock = threading.Lock()

    def factory(self, scope):
        def handler(request):
            body = json.loads(request.content)
            messages = body["messages"]
            material = json.dumps(messages)
            with self.lock:
                self.requests.append({"context": scope.context_id, "material": material})
            if "WORK_MATERIAL_JSON" in material:
                return response(content="PASS")
            if "UNTRUSTED_REVIEW_DATA_JSON" in material:
                return response(content='{"mode":"none"}')
            boundary = max(i for i, message in enumerate(messages) if message["role"] == "user")
            current = messages[boundary:]
            tools = [message for message in current if message["role"] == "tool"]
            if scope.context_id.startswith("work:"):
                if not tools:
                    return response(tool="mutate_file", arguments={
                        "path": "practice.txt", "expected_digest": None, "content": PRACTICE_TEXT})
                if len(tools) == 1:
                    return response(tool="work", arguments={"op": "report", "kind": "result",
                        "note": "The practice is saved in practice.txt. [Synthetic model fixture]"})
                return response(content="The practice is saved in practice.txt. [Synthetic model fixture]")
            if FIRST_DEPARTURE_ORIENTATION in messages[boundary]["content"] and not tools:
                return response(tool="work", arguments={"op": "create", "title": PRACTICE_TITLE,
                                                         "instruction": PRACTICE_INSTRUCTION})
            if messages[boundary]["content"] == "" or PROVIDER_CONTINUATION_ORIENTATION in messages[boundary]["content"]:
                if not tools:
                    return response(tool="work", arguments={"op": "list"})
                if len(tools) == 1:
                    return response(tool="work", arguments={"op": "create", "title": PRACTICE_TITLE,
                                                             "instruction": PRACTICE_INSTRUCTION})
            if tools:
                return response(content="I opened a focused practice Work. [Synthetic model fixture]")
            return response(content="The practice result is ready to continue. [Synthetic model fixture]")

        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "synthetic-autonomous-practice",
            credential_env="PER_PULSE_AUTONOMOUS_FIXTURE_KEY", max_retries=0,
        ), transport=httpx.MockTransport(handler))
