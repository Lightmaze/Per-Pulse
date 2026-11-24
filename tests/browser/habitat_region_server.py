"""Synthetic provider choices for a real World/Kernel regional Habitat browser trip.

Only model decisions are fixtures. Native permissions, files, causal source,
ManagedHabitat effect journal, Work histories and SQLite restarts are real.
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
from pulse_system.service.native_habitat import NativeHabitat

lock = threading.Lock()
observed = []
FIRST = "The first visit left a real note.\n"
SECOND = "The second Work added a distinct note.\n"


def response(*, name=None, arguments=None, content=None, index=0):
    if name is None:
        delta = {"content": content or "The regional observation is complete. [Synthetic model choice]"}
        reason = "stop"
    else:
        delta = {"tool_calls": [{"index": 0, "id": "habitat-browser-" + str(index),
            "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments)}}]}
        reason = "tool_calls"
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        + "data: [DONE]\n\n")


def handler(request, scope):
    body = json.loads(request.content)
    messages = body["messages"]
    start = max(i for i, row in enumerate(messages) if row["role"] == "user")
    prompt = str(messages[start]["content"])
    results = [json.loads(row["content"]) for row in messages[start + 1:]
               if row["role"] == "tool"]
    with lock:
        observed.append({"context": scope.context_id, "prompt": prompt,
                         "results": results})
    if "HABITAT_WORK_A_WRITE" in prompt:
        if not results:
            return response(name="habitat_write", arguments={
                "op": "write", "region": "garden", "file": "notes.txt",
                "content": FIRST})
    elif "HABITAT_WORK_B_CONTINUE" in prompt:
        if not results:
            return response(name="habitat_observe", arguments={
                "op": "read", "region": "garden", "file": "notes.txt"})
        if len(results) == 1 and results[0]["status"] == "succeeded":
            return response(name="habitat_write", arguments={
                "op": "append", "region": "garden", "file": "notes.txt",
                "content": SECOND}, index=1)
    elif "HABITAT_HOME_VERIFY" in prompt:
        if not results:
            return response(name="habitat_observe", arguments={
                "op": "read", "region": "garden", "file": "notes.txt"})
    return response()


def factory(scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions",
        "synthetic-regional-habitat-fixture",
        credential_env="PER_PULSE_HABITAT_TEST_KEY",
    ), transport=httpx.MockTransport(lambda request: handler(request, scope)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    os.environ["PER_PULSE_HABITAT_TEST_KEY"] = "explicit-synthetic-browser-credential"
    app = create_app(args.data_dir, model_factory=factory,
                     posture=False, allow_workspace_actions=True,
                     auto_apply_edits=False,
                     pulse=PulseSettings(enabled=False))

    @app.get("/__habitat/status")
    def evidence():
        runtime = app.state.pages.runtime
        path = runtime.habitat.root / "regions/garden/notes.txt"
        index = runtime.storage.load_component_state(
            NativeHabitat._index_key(runtime.world_id, "regions/garden/notes.txt"))
        with lock:
            return {"worldId": runtime.world_id,
                    "exists": path.exists(),
                    "content": path.read_text(encoding="utf-8") if path.exists() else None,
                    "writerIndex": index, "modelObservations": list(observed)}

    # This fixture route must precede the product's SPA fallback.
    route = app.router.routes.pop()
    index = next(i for i, item in enumerate(app.router.routes)
                 if getattr(item, "path", None) == "/{route:path}")
    app.router.routes.insert(index, route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
