"""Explicit local transport fixture for the browser flow, never a live model.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Run only through posture_flow.mjs; all data goes to its temporary directory.
"""
from __future__ import annotations

import argparse
import json
import os

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PageService
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def response(text=None, arguments=None):
    delta = {"content": text} if arguments is None else {"tool_calls": [{
        "index": 0, "id": "browser-" + arguments["op"], "type": "function",
        "function": {"name": "posture", "arguments": json.dumps(arguments)},
    }]}
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop" if arguments is None else "tool_calls"}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                                  + "data: [DONE]\n\n")


def handler(request):
    body = json.loads(request.content)
    messages = body["messages"]
    if "WORK_MATERIAL_JSON" in json.dumps(messages):
        return response("PASS")
    current = max(index for index, message in enumerate(messages) if message["role"] == "user")
    if "Let me try the interaction before asking for a profile." not in str(messages[current]["content"]):
        return response("The first screen is a twelve-field intake form. [Explicit browser test fixture]")
    results = [message for message in messages[current + 1:] if message["role"] == "tool"]
    if not results:
        return response(arguments={"op": "list"})
    outcome = json.loads(results[-1]["content"])
    if outcome["status"] != "succeeded":
        return response("The browser fixture encountered a failed tool action.")
    if len(results) > 1:
        return response("The revised flow begins with one playable interaction. [Explicit browser test fixture]")
    sources = outcome["output"]["recent_sources"]
    original = next(item for item in reversed(sources) if item["kind"] == "assistant_result")
    feedback = next(item for item in reversed(sources) if item["source"] == "user")
    return response(arguments={
        "op": "remember", "source_message_id": original["message_id"],
        "feedback_message_id": feedback["message_id"],
        "title": "Direct interaction before optional setup",
        "lesson": "Deliver an interactive attempt when immediate experience is the purpose.",
        "conditions": {"all": ["An open-ended consumer experience is requested", {"any": [
            "The central value can be tried now", "A reversible sample is possible",
        ]}]},
        "counterexamples": ["A standard registration task needs a concise form."],
        "shift": {"understanding": "Identify the first usable interaction.",
                  "candidates": "Consider a playable first slice.",
                  "delivery": "Verify the visitor can actually try it."},
    })


def factory(_scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "explicit-browser-test-fixture",
        credential_env="PER_PULSE_BROWSER_TEST_KEY",
    ), transport=httpx.MockTransport(handler))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--seed-later-messages", type=int, default=0)
    args = parser.parse_args()
    os.environ["PER_PULSE_BROWSER_TEST_KEY"] = "synthetic-browser-fixture"
    if args.seed_later_messages:
        if not 1 <= args.seed_later_messages <= 600:
            parser.error("fixture seed count must be between 1 and 600")
        pages = PageService(args.data_dir, posture=True, model_factory=None)
        try:
            for index in range(args.seed_later_messages):
                pages.runtime.inject(pages.runtime.continuity_engram_id,
                                     f"Later queued browser fixture message {index}.", source="user")
        finally:
            pages.runtime.close()
    else:
        uvicorn.run(create_app(args.data_dir, posture=True, model_factory=None if args.offline else factory),
                    host="127.0.0.1", port=args.port, log_level="warning")
