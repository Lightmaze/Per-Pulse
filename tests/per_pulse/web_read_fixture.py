"""Synthetic HTTP decisions and website bytes; native effects stay real.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json

import httpx

from pulse_harness.adapters import web_read
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.core.pulse.autonomy import FIRST_DEPARTURE_ORIENTATION

URL = "https://reading.example/field-notes"
TITLE = "Reading expedition"
ORIGINAL = "Original field observation: café 山川 🌍."
ANSWER = "The sourced reading is saved in expedition.txt."


def response(content=None, tool=None, arguments=None):
    deltas = ([{"tool_calls": [{"index": 0, "id": "fixture-" + tool,
        "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]}]
        if tool else [{"content": character} for character in content])
    frames = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
    frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}],
                   "usage": {"prompt_tokens": 80, "completion_tokens": 20}})
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(item) + "\n\n" for item in frames) + "data: [DONE]\n\n")


class Body(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content
    async def __aiter__(self):
        for offset in range(0, len(self.content), 17):
            yield self.content[offset:offset + 17]


class WebFixture:
    def __init__(self, monkeypatch):
        monkeypatch.setenv("PER_PULSE_WEB_FIXTURE_KEY", "explicit-synthetic-credential")
        self.requests, self.web_requests = [], []
        self.body = '<title>Field observations</title><script>not evidence</script><p>' + ORIGINAL + '</p>'
        monkeypatch.setattr(web_read, "_resolve", lambda host, token, deadline: ("8.8.8.8",))
        def website(request):
            self.web_requests.append(str(request.url))
            assert request.headers["host"] == "reading.example"
            assert request.extensions["sni_hostname"] == "reading.example"
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                                  stream=Body(self.body.encode()))
        monkeypatch.setattr(web_read, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(website), trust_env=False))

    def factory(self, scope):
        def model(request):
            body = json.loads(request.content)
            self.requests.append((scope, body))
            messages = body["messages"]
            material = json.dumps(messages)
            if "UNTRUSTED_REVIEW_DATA_JSON" in material:
                return response('{"mode":"none"}')
            if "WORK_MATERIAL_JSON" in material:
                return response("PASS")
            boundary = max(i for i, row in enumerate(messages) if row["role"] == "user")
            current = messages[boundary:]
            results = [json.loads(row["content"]) for row in current if row["role"] == "tool"]
            task = current[0]["content"]
            if task.startswith("Read an unsupported webpage"):
                if not results:
                    return response(tool="read_web", arguments={"url": "http://reading.example/plain"})
                assert results[-1]["error_code"] == "web_url_invalid"
                return response("This URL uses HTTP. Try a public HTTPS text page.")
            if task.startswith("Independent conversation"):
                return response("This independent Work has no web observation.")
            if task.startswith("Recall the original webpage"):
                if not results:
                    return response(tool="search_history", arguments={"query": "Original field observation", "role": "tool"})
                if len(results) == 1:
                    match = results[0]["output"]["matches"][0]
                    return response(tool="read_history", arguments={"message_id": match["source"]["message_id"], "role": "tool"})
                assert ORIGINAL in results[-1]["output"]["content"]
                return response("Recovered the original saved webpage observation.")
            if scope.context_id.startswith("work:"):
                if not results:
                    return response(tool="read_web", arguments={"url": URL})
                if len(results) == 1:
                    observation = results[0]["output"]
                    assert observation["source"]["scope"] == "extracted_text"
                    assert ORIGINAL in observation["content"]
                    return response(tool="mutate_file", arguments={"path": "expedition.txt", "expected_digest": None,
                        "content": observation["content"] + "\nSource: " + observation["source"]["url"] + "\n"})
                if len(results) == 2:
                    return response(tool="work", arguments={"op": "report", "kind": "result", "note": ANSWER})
                return response(ANSWER)
            if FIRST_DEPARTURE_ORIENTATION in task and not results:
                return response(tool="work", arguments={"op": "create", "title": TITLE,
                    "instruction": "Read " + URL + " and save a sourced field observation."})
            return response("The reading Work is ready to continue.")
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "web-fixture",
            credential_env="PER_PULSE_WEB_FIXTURE_KEY", max_retries=0), transport=httpx.MockTransport(model))
