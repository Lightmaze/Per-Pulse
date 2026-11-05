"""Explicit synthetic HTTP model choices for real native report navigation.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The fixture does not write reports: the native Work tool and Kernel do that.
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

NOTE = "A selected source finding. [Explicit synthetic HTTP fixture]"
RESULT = "The original detailed source result. [Explicit synthetic HTTP fixture]"
requests = []
lock = threading.Lock()


def factory(scope):
    def respond(request):
        body = json.loads(request.content)
        with lock:
            requests.append({"context": scope.context_id, "body": body})
        messages = body["messages"]
        current = max(i for i, row in enumerate(messages) if row["role"] == "user")
        results = [json.loads(row["content"]) for row in messages[current + 1:] if row["role"] == "tool"]
        if scope.context_id.startswith("work:") and not results:
            delta = {"tool_calls": [{"index": 0, "id": "source-browser-report", "type": "function",
                "function": {"name": "work", "arguments": json.dumps(
                    {"op": "report", "kind": "finding", "note": NOTE})}}]}
            reason = "tool_calls"
        else:
            assert all(row["status"] == "succeeded" for row in results), results
            delta = {"content": RESULT if scope.context_id.startswith("work:")
                     else "The Home conversation continues. [Explicit synthetic HTTP fixture]"}
            reason = "stop"
        payload = {"choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n")
    return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions",
        "synthetic-report-source-fixture", credential_env="PER_PULSE_REPORT_SOURCE_KEY"),
        transport=httpx.MockTransport(respond))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.environ["PER_PULSE_REPORT_SOURCE_KEY"] = "explicit-synthetic-browser-credential"
    app = create_app(args.data_dir, model_factory=None if args.offline else factory,
                     posture=False, pulse=PulseSettings(enabled=False), allow_workspace_actions=True)

    @app.get("/__report-source/status")
    def status():
        runtime = app.state.pages.runtime
        reports = []
        for record in runtime.storage.native_work_requests(limit=32):
            if record["arguments"]["op"] != "report" or record["event_id"] is None:
                continue
            event = runtime.causal_ledger.get_event(record["event_id"])
            origin = runtime.work_report_origin(event.id, record["request_id"],
                event.engram_id, event.content, require_settled_source=True)
            turn = runtime.causal_ledger.get_turn_for_world(record["source_turn_id"], runtime.world_id)
            reports.append({"eventId": event.id, "workId": record["work_id"],
                            "sourceResultMessageId": turn.result_event_id,
                            "origin": origin, "status": record["status"]})
        with lock:
            return {"reports": reports, "requests": len(requests)}

    route = app.router.routes.pop()
    index = next(i for i, value in enumerate(app.router.routes)
                 if getattr(value, "path", None) == "/{route:path}")
    app.router.routes.insert(index, route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
