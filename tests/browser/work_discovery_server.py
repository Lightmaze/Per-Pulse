"""Native Work creation fixture; only HTTP model choices and a reply barrier are synthetic.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
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

TITLE = "A discovered source check"
entered, release = threading.Event(), threading.Event()
requests = []
lock = threading.Lock()


def response(text=None, arguments=None):
    delta = {"content": text} if arguments is None else {"tool_calls": [{
        "index": 0, "id": "browser-discover-work", "type": "function",
        "function": {"name": "work", "arguments": json.dumps(arguments)},
    }]}
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {},
                      "finish_reason": "stop" if arguments is None else "tool_calls"}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 6}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join("data: " + json.dumps(item) + "\n\n" for item in chunks)
                                  + "data: [DONE]\n\n")


def handler(request, scope):
    body = json.loads(request.content)
    with lock:
        requests.append(body)
    messages = body["messages"]
    current = max(i for i, item in enumerate(messages) if item["role"] == "user")
    prompt = str(messages[current]["content"])
    results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
    if scope.context_id.startswith("work:"):
        return response("The focused check finished. [Explicit synthetic fixture]")
    if "HOLD_DISCOVERY_REPLY" in prompt:
        entered.set()
        if not release.wait(30):
            raise RuntimeError("the browser did not release its explicit reply barrier")
        return response("The held Home reply finished. [Explicit synthetic fixture]")
    if "CREATE_WORK_DISCOVERY" in prompt:
        if not results:
            return response(arguments={"op": "create", "title": TITLE,
                                       "instruction": "Check this source in its own Work. [Explicit synthetic fixture]"})
        if results[-1]["status"] != "succeeded":
            return response("The native create failed. [Explicit synthetic fixture]")
        return response("I opened the focused Work. [Explicit synthetic fixture]")
    return response("The Home turn finished. [Explicit synthetic fixture]")


def factory(scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "synthetic-work-discovery-fixture",
        credential_env="PER_PULSE_WORK_DISCOVERY_KEY",
    ), transport=httpx.MockTransport(lambda request: handler(request, scope)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.environ["PER_PULSE_WORK_DISCOVERY_KEY"] = "explicit-synthetic-browser-credential"
    app = create_app(args.data_dir, posture=False, pulse=PulseSettings(enabled=False),
                     allow_workspace_actions=True, model_factory=None if args.offline else factory)

    @app.get("/__work-discovery/status")
    def status():
        runtime = app.state.pages.runtime
        native = runtime.storage.native_work_requests(subject=runtime.continuity_engram_id, limit=32)
        with lock:
            return {"requests": len(requests), "entered": entered.is_set(),
                    "creates": [{"workId": item["work_id"], "status": item["status"]}
                                for item in native if item["arguments"]["op"] == "create"]}

    @app.post("/__work-discovery/release")
    def release_reply():
        release.set()
        return {"released": True}

    routes = app.router.routes[-2:]
    del app.router.routes[-2:]
    index = next(i for i, value in enumerate(app.router.routes)
                 if getattr(value, "path", None) == "/{route:path}")
    app.router.routes[index:index] = routes
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
