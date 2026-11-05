"""Explicit synthetic HTTP choices over real Home/Work execution and persistence.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No external provider is contacted; the fixture never creates or sends Work itself.
"""
from __future__ import annotations

import argparse
import json
import os
import threading

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

TITLE = "A focused source check"
INITIAL = "WORK_INITIAL_BROWSER: Inspect the supplied question in this focused Work."
CONTINUED = "WORK_CONTINUED_BROWSER: Check one more case in the same Work."
FIRST_NOTE = "The initial source check is available. [Explicit synthetic fixture]"
LATER_NOTE = "The same Work checked the second case. [Explicit synthetic fixture]"
requests = []
lock = threading.Lock()


def response(text=None, arguments=None, *, sequence=0):
    delta = {"content": text} if arguments is None else {"tool_calls": [{
        "index": 0, "id": "browser-work-" + str(sequence), "type": "function",
        "function": {"name": "work", "arguments": json.dumps(arguments)},
    }]}
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {},
                      "finish_reason": "stop" if arguments is None else "tool_calls"}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                                  + "data: [DONE]\n\n")


def handler(request, scope):
    body = json.loads(request.content)
    with lock:
        requests.append({"context": scope.context_id, "body": body})
    messages = body["messages"]
    current = max(index for index, message in enumerate(messages) if message["role"] == "user")
    prompt = str(messages[current]["content"])
    results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
    if results and results[-1]["status"] != "succeeded":
        return response("The internal Work action failed. [Explicit synthetic fixture]")
    if scope.context_id.startswith("work:"):
        continued = "WORK_CONTINUED_BROWSER" in prompt
        if not results:
            return response(arguments={"op": "report", "kind": "finding",
                                       "note": LATER_NOTE if continued else FIRST_NOTE})
        return response("The second scoped check is complete. [Explicit synthetic fixture]" if continued
                        else "The first scoped check is complete. [Explicit synthetic fixture]")
    if "Selected update from Work " in prompt:
        return response("I received the selected Work finding. [Explicit synthetic fixture]")
    if "CREATE_WORK_BROWSER" in prompt:
        if not results:
            return response(arguments={"op": "create", "title": TITLE, "instruction": INITIAL})
        return response("I opened A focused source check. [Explicit synthetic fixture]")
    if "CONTINUE_WORK_BROWSER" in prompt:
        if not results:
            return response(arguments={"op": "list"})
        if len(results) == 1:
            owned = next(item for item in results[-1]["output"]["works"] if item["title"] == TITLE)
            return response(arguments={"op": "send", "work_id": owned["id"],
                                       "instruction": CONTINUED}, sequence=1)
        return response("I continued the existing Work. [Explicit synthetic fixture]")
    return response("The Home turn completed. [Explicit synthetic fixture]")


def factory(scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "synthetic-work-organization-fixture",
        credential_env="PER_PULSE_WORK_ORGANIZATION_KEY",
    ), transport=httpx.MockTransport(lambda request: handler(request, scope)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--no-workspace-actions", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.environ["PER_PULSE_WORK_ORGANIZATION_KEY"] = "explicit-synthetic-browser-credential"
    app = create_app(args.data_dir, posture=False, pulse=PulseSettings(enabled=False),
                     allow_workspace_actions=not args.no_workspace_actions,
                     model_factory=None if args.offline else factory)

    @app.get("/__work-organization/status")
    def status():
        runtime = app.state.pages.runtime
        subject = runtime.continuity_engram_id
        native = []
        for record in runtime.storage.native_work_requests(subject=subject, limit=32):
            turn = runtime.causal_ledger.get_turn_for_world(record["source_turn_id"], runtime.world_id)
            event = runtime.causal_ledger.get_event(record["event_id"]) if record["event_id"] else None
            report = (runtime._native_work.report_origin(
                event.id, record["request_id"], subject, event.content,
                require_settled_source=True,
            ) if event is not None and record["arguments"]["op"] == "report" else None)
            native.append({"op": record["arguments"]["op"], "workId": record["work_id"],
                           "status": record["status"], "eventId": record["event_id"],
                           "sourceTurnId": record["source_turn_id"],
                           "sourceContext": record["source_scope"]["context_id"],
                           "sourceResultMessageId": turn.result_event_id if turn else None,
                           "reportOrigin": report})
        with lock:
            return {"nativeRequests": native, "requests": list(requests)}

    route = app.router.routes.pop()
    index = next(i for i, value in enumerate(app.router.routes)
                 if getattr(value, "path", None) == "/{route:path}")
    app.router.routes.insert(index, route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
