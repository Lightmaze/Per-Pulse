"""Explicit synthetic choices; native reading, memory and file effects are real."""
from __future__ import annotations

import json
from material_fixture import MaterialFixture


class ReadingExampleFixture(MaterialFixture):
    def __init__(self, setenv):
        super().__init__(setenv)
        self.outcomes = []
        self.prepared = None
        self.fail_after_prepare = False

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        if "WORK_MATERIAL_JSON" in json.dumps(messages):
            return self.response("PASS")
        current = max(i for i, item in enumerate(messages) if item["role"] == "user")
        instruction = messages[current]["content"]
        if instruction.startswith("APPLY_READING_FIXTURE:"):
            command = json.loads(instruction.split(":", 1)[1])
            results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
            if not results:
                return self.response(tool="posture", arguments={"op": "get", "experience_id": command["experience_id"]})
            if results[0]["status"] != "succeeded":
                return self.response("The example is not available here. [Explicit synthetic fixture]")
            if len(results) == 1:
                example = results[0]["output"]["experience"]
                content = "# Source comparison\n\n" + example["excerpt"] + "\n\n" + example["note"] + "\n\n[Explicit synthetic fixture; native approved file.]\n"
                return self.response(tool="mutate_file", sequence=1, arguments={
                    "path": command["path"], "expected_digest": None, "content": content})
            return self.response("The source comparison is saved in " + command["path"] + ". [Explicit synthetic fixture]")
        if "READING_EXAMPLE_FIXTURE:" not in instruction:
            return self.response("Ready. [Explicit synthetic reading example fixture]")
        # A committed generation prepends its actual context-local summary.
        command = json.loads(instruction.rsplit("READING_EXAMPLE_FIXTURE:", 1)[1])
        results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
        if not results:
            tool = "posture" if command["op"] == "remember_reading" else "reading"
            return self.response(tool=tool, arguments={k: v for k, v in command.items() if k != "retain"})
        if len(results) == 1 and "retain" in command and results[0]["status"] == "succeeded":
            return self.response(tool="posture", sequence=1, arguments={
                "op": "remember_reading", "receipt_id": results[0]["output"]["receipt_id"], **command["retain"]})
        self.outcomes.append(results)
        self.prepared = results[-1]
        if self.fail_after_prepare:
            raise RuntimeError("Explicit fixture failure after preparation")
        return self.response("The selected source passage is retained locally. [Explicit synthetic fixture]")
