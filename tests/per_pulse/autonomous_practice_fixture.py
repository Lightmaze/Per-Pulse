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
REVISION_TASK = "Change the purpose: produce a planning brief instead."
RETAINED_TEXT = "Keep this exact effect.\n"
PLANNING_TEXT = "# Planning brief\nVerify the assumptions before release.\n"


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
    def __init__(self, setenv, *, purpose_revision=False):
        self.purpose_revision = purpose_revision
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
            # A budget continuation is a system input, not a new user turn.
            # Start its fixture decision at that notice, not the earlier departure
            # or an already completed tool in the preceding turn.
            user_boundary = max((i for i, message in enumerate(messages)
                                 if message["role"] == "user"), default=-1)
            continuation_boundary = max((i for i, message in enumerate(messages)
                                         if message["role"] == "system"
                                         and "Internal provider continuation, not a user message."
                                         in message["content"]), default=-1)
            if continuation_boundary > user_boundary:
                boundary = continuation_boundary
            else:
                boundary = user_boundary
            current = messages[max(0, boundary):]
            orientation = messages[boundary]["content"] if boundary >= 0 else ""
            tools = [message for message in current if message["role"] == "tool"]
            if self.purpose_revision:
                if scope.context_id.startswith("work:"):
                    if continuation_boundary > user_boundary:
                        assert user_boundary >= 0 and messages[user_boundary]["content"] == REVISION_TASK
                        if not tools:
                            return response(tool="mutate_file", arguments={
                                "path": "planning-brief.md", "expected_digest": None, "content": PLANNING_TEXT})
                        if len(tools) == 1:
                            return response(tool="work", arguments={"op": "report", "kind": "result",
                                "note": "The revised planning brief is ready.", "artifact_path": "planning-brief.md"})
                        return response(content="Planning brief completed. [Synthetic fixture]")
                    if orientation == REVISION_TASK:
                        return response(content="Revision accepted. [Synthetic fixture]")
                    if "WRITE_RESUME" in orientation and not tools:
                        return response(tool="mutate_file", arguments={
                            "path": "retained.txt", "expected_digest": None, "content": RETAINED_TEXT})
                return response(content="Ready to continue. [Synthetic fixture]")
            if scope.context_id.startswith("work:"):
                if not tools:
                    return response(tool="mutate_file", arguments={
                        "path": "practice.txt", "expected_digest": None, "content": PRACTICE_TEXT})
                if len(tools) == 1:
                    return response(tool="work", arguments={"op": "report", "kind": "result",
                        "note": "The practice is saved in practice.txt. [Synthetic model fixture]"})
                return response(content="The practice is saved in practice.txt. [Synthetic model fixture]")
            if FIRST_DEPARTURE_ORIENTATION in orientation and not tools:
                return response(tool="work", arguments={"op": "create", "title": PRACTICE_TITLE,
                                                         "instruction": PRACTICE_INSTRUCTION})
            if (orientation == "" or continuation_boundary > user_boundary
                    or PROVIDER_CONTINUATION_ORIENTATION in orientation):
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
