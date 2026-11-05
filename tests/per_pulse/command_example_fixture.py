"""Explicit model choices for retained command passages; command pipes are real.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import httpx

from material_fixture import MaterialFixture


class CommandExampleFixture(MaterialFixture):
    def __init__(self, setenv):
        super().__init__(setenv)
        self.outcomes = []
        self.quiet = None
        self.before_action = []
        self.fail_after_prepare = False
        self.current_scope = None

    def factory(self, scope):
        self.current_scope = scope
        return super().factory(scope)

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        if "WORK_MATERIAL_JSON" in json.dumps(messages):
            packet = json.loads(messages[-1]["content"].split("WORK_MATERIAL_JSON\n", 1)[1])
            if "candidate_response" not in packet:
                self.before_action.append(packet)
            return self.response("PASS")
        if "UNTRUSTED_REVIEW_DATA_JSON" in json.dumps(messages):
            return self.response('{"mode":"none"}')
        current = max(i for i, item in enumerate(messages) if item["role"] == "user")
        instruction = messages[current]["content"]
        if "COMMAND_EXAMPLE_FIXTURE:" in instruction:
            command = json.loads(instruction.rsplit("COMMAND_EXAMPLE_FIXTURE:", 1)[1])
        elif instruction == "" and self.quiet is not None:
            command = self.quiet
        else:
            return self.response("Ready. [Explicit synthetic command example fixture]")
        results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
        if not results:
            if command["op"] == "same_batch":
                # Deliberately forge the host ID to test an unseen read in the
                # same model response. Normal capture learns it from output.
                receipt = self.current_scope.digest + ":same-batch-read"
                calls = [
                    {"index": 0, "id": "same-batch-read", "type": "function", "function": {
                        "name": "read_command_output", "arguments": json.dumps(command["read"])}},
                    {"index": 1, "id": "same-batch-keep", "type": "function", "function": {
                        "name": "posture", "arguments": json.dumps({"op": "remember_command",
                            "receipt_id": receipt, "title": "Unseen parallel passage",
                            "excerpt": "STDERR_FINAL_DIAGNOSIS"})}},
                ]
                payload = {"choices": [{"index": 0, "delta": {"tool_calls": calls}, "finish_reason": None}]}
                done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                    content="data: " + json.dumps(payload) + "\n\ndata: " + json.dumps(done) + "\n\ndata: [DONE]\n\n")
            if command["op"] == "run":
                return self.response(tool="run_command", arguments={"argv": ["explicit-pipe-fixture"]})
            tool = "read_command_output" if command["op"] == "read" else "posture"
            return self.response(tool=tool, arguments={key: value for key, value in command.items()
                                                      if key != "retain" and (tool != "read_command_output" or key != "op")})
        if len(results) == 1 and "retain" in command and results[0]["status"] == "succeeded":
            return self.response(tool="posture", sequence=1, arguments={
                "op": "remember_command", "receipt_id": results[0]["output"]["receipt_id"], **command["retain"]})
        self.outcomes.append(results)
        if self.fail_after_prepare:
            raise RuntimeError("Explicit model failure after example preparation")
        return self.response("The command passage is available for later work. [Explicit synthetic fixture]")
