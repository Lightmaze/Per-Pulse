"""Explicit synthetic transport for the concrete-example browser journey.

The real World, Kernel, journal, HTTP API and client remain in use. No live
model is contacted and these scripted choices do not measure learning.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import os

import httpx
import uvicorn

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

EXCERPT = "Saved documents remain available. Search is slow.\nNext update: 09:00 UTC."
ORIGINAL = "## Customer notice\n\n" + EXCERPT + "\n\nPRIVATE_SURROUNDING_ACCOUNT: a synthetic internal discussion."
REVISED_NOTE = "Check the current service state and update time before adapting this example."
REQUESTS = []


def response(text=None, arguments=None):
    delta = {"content": text} if arguments is None else {"tool_calls": [{
        "index": 0, "id": "example-" + arguments["op"], "type": "function",
        "function": {"name": "posture", "arguments": json.dumps(arguments)},
    }]}
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop" if arguments is None else "tool_calls"}]},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                                  + "data: [DONE]\n\n")


def handler(request):
    body = json.loads(request.content)
    REQUESTS.append(body)
    messages = body["messages"]
    if "WORK_MATERIAL_JSON" in json.dumps(messages):
        return response("PASS")
    current = max(index for index, message in enumerate(messages) if message["role"] == "user")
    task = str(messages[current]["content"])
    results = [message for message in messages[current + 1:] if message["role"] == "tool"]
    if "KEEP_ORIGINAL_EXAMPLE" in task or "REVISE_EXAMPLE_NOTE" in task:
        if not results:
            return response(arguments={"op": "list"})
        outcome = json.loads(results[-1]["content"])
        if outcome["status"] != "succeeded":
            return response("The explicit example fixture encountered a failed action.")
        if len(results) > 1:
            return response("The example request finished. [Explicit synthetic fixture]")
        sources = outcome["output"]["recent_sources"]
        if "REVISE_EXAMPLE_NOTE" in task:
            saved = next(item for item in outcome["output"]["experiences"] if item.get("representation") == "example")
            followup = next(item for item in reversed(sources) if item["source"] == "user")
            return response(arguments={
                "op": "revise", "experience_id": saved["id"], "expected_revision": saved["revision"],
                "feedback_message_id": followup["message_id"], "changes": {"note": REVISED_NOTE},
            })
        original = next(item for item in reversed(sources) if item["kind"] == "assistant_result")
        return response(arguments={
            "op": "remember_example", "source_message_id": original["message_id"],
            "title": "A concrete service notice", "excerpt": EXCERPT,
            "note": "A particular wording to compare with a future task, not a reusable rule.",
        })
    if "FIND_SHARED_EXAMPLE" in task:
        if not results:
            return response(arguments={"op": "find", "query": "Saved documents remain available"})
        outcome = json.loads(results[-1]["content"])
        examples = outcome.get("output", {}).get("experiences", [])
        return response("The exact example was available. [Explicit synthetic fixture]" if examples
                        else "No example was available. [Explicit synthetic fixture]")
    return response(ORIGINAL)


def factory(_scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "explicit-example-browser-fixture",
        credential_env="PER_PULSE_EXAMPLE_FIXTURE_KEY",
    ), transport=httpx.MockTransport(handler))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.environ["PER_PULSE_EXAMPLE_FIXTURE_KEY"] = "synthetic-fixture-only"
    app = create_app(args.data_dir, posture=True, model_factory=None if args.offline else factory)

    @app.get("/__examples/requests")
    def requests():
        return REQUESTS

    # Register this test-only diagnostic before the frontend catch-all mount.
    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
