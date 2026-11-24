"""A mistaken tool call is answered with why, so the model can correct it.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Explicit HTTP fixtures stand in for the model; no external model is used.
"""
import json

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from test_pages import client_for, send, wait_for


def converse(tmp_path, monkeypatch, function):
    """The model makes one tool call, then replies to whatever result it gets."""
    monkeypatch.setenv("CORRECTION_FIXTURE_KEY", "synthetic-credential")
    seen = []

    def respond(delta, finish):
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body["messages"][-1]["role"] == "tool":
            return respond({"content": "I corrected the call. [Synthetic HTTP fixture]"}, "stop")
        task = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")
        if "Use a tool." not in str(task):
            return respond({"content": "Ready. [Synthetic HTTP fixture]"}, "stop")
        return respond({"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": function}]},
                       "tool_calls")

    def factory(_):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat", "correction-fixture",
            credential_env="CORRECTION_FIXTURE_KEY"), transport=httpx.MockTransport(handler))

    with client_for(tmp_path, model_factory=factory) as client:
        send(client, "home", "Use a tool.")
        view = wait_for(client, "home", lambda v: v["messages"][-1]["role"] == "assistant", timeout=15)
    assert view["page"]["status"] != "error", view["page"]
    assert view["messages"][-1]["content"].startswith("I corrected the call.")
    [answer] = [body for body in seen if body["messages"][-1]["role"] == "tool"]
    call = next(m for m in answer["messages"] if m.get("tool_calls"))["tool_calls"][0]
    return call, json.loads(answer["messages"][-1]["content"])


def test_an_unknown_tool_name_is_answered_with_the_available_tools(tmp_path, monkeypatch):
    call, result = converse(tmp_path, monkeypatch, {"name": "open_file", "arguments": json.dumps({"path": "notes.md"})})
    assert call["function"]["name"] == "open_file"
    assert result["status"] == "failed" and result["error_code"] == "unknown_tool"
    assert result["output"]["error"].startswith("No tool named 'open_file' is available in this turn.")
    assert "read_file" in result["output"]["error"]


@pytest.mark.parametrize("arguments, problem", [
    ('{"path": "notes.md"', "Expecting"),        # cut off
    ('["notes.md"]', "got a JSON array"),         # not an object
    ('{"path": "a", "path": "b"}', "duplicate JSON key"),
])
def test_unreadable_arguments_are_answered_instead_of_failing_the_turn(tmp_path, monkeypatch, arguments, problem):
    call, result = converse(tmp_path, monkeypatch, {"name": "read_file", "arguments": arguments})
    # The call is replayed with empty arguments, never with text the service cannot parse.
    assert call["function"] == {"name": "read_file", "arguments": "{}"}
    assert result["status"] == "failed" and result["error_code"] == "tool_arguments_invalid"
    assert result["output"]["error"].startswith("The arguments for read_file could not be read as one JSON object")
    assert problem in result["output"]["error"]


def test_a_failed_tool_call_says_why(tmp_path, monkeypatch):
    _, result = converse(tmp_path, monkeypatch, {"name": "read_file", "arguments": json.dumps({"path": "notes.md", "offset": -1})})
    assert result == {"status": "failed", "error_code": "tool_arguments_invalid",
                      "output": {"error": "read_file range is invalid"}}


def test_empty_argument_text_is_an_empty_object(tmp_path, monkeypatch):
    _, result = converse(tmp_path, monkeypatch, {"name": "read_file", "arguments": ""})
    # read_file needs a path: the tool itself answers, not the argument reader.
    assert result["error_code"] == "tool_arguments_invalid"
    assert result["output"]["error"] == "read_file arguments are not canonical"
