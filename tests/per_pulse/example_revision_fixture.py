"""Explicit HTTP choices for revising an interpretation during quiet work.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json

from reading_example_fixture import ReadingExampleFixture


class ExampleRevisionFixture(ReadingExampleFixture):
    def __init__(self, setenv):
        super().__init__(setenv)
        self.quiet = None
        self.before_action = []

    def handler(self, request):
        body = json.loads(request.content)
        messages = body["messages"]
        if "WORK_MATERIAL_JSON" in json.dumps(messages):
            self.requests.append(body)
            packet = json.loads(messages[-1]["content"].split("WORK_MATERIAL_JSON\n", 1)[1])
            if "candidate_response" not in packet:
                self.before_action.append(packet)
            return self.response("PASS")
        current = max(i for i, item in enumerate(messages) if item["role"] == "user")
        instruction = messages[current]["content"]
        # A successor's first quiet turn carries its context-local summary.
        # The fixture's next choice applies to that same native tick as well.
        if (self.quiet is not None and "READING_EXAMPLE_FIXTURE:" not in instruction
                and not instruction.startswith("APPLY_READING_FIXTURE:")):
            self.requests.append(body)
            results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
            if not results:
                return self.response(tool="posture", arguments=self.quiet)
            self.outcomes.append(results)
            self.prepared = results[-1]
            if self.fail_after_prepare:
                raise RuntimeError("Explicit fixture failure after quiet revision preparation")
            return self.response("I refined the interpretation while keeping the original passage. [Explicit synthetic fixture]")
        return super().handler(request)
