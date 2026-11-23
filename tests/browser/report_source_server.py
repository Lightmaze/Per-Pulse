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
FILE_TEXT = "# Report deliverable\n\nThis is the completed Work file.\n"
RESULT = "The original detailed source result. [Explicit synthetic HTTP fixture]"
WRITE_TASK = "Write report-delivery.md in this Work; report it on a later turn. [Authored fixture request]"
REPORT_TASK = "Report the previously completed Work file to Home. [Authored fixture request]"
SOURCE_TITLE = "Native report source Work"
DERIVED_TASK = "Derive a file from Native report source Work. [Authored fixture request]"
DERIVED_TEXT = "# Derived deliverable\n\nBased on the verified source Work file.\n"
requests = []
file_actions = {}
lock = threading.Lock()


def factory(scope):
    def respond(request):
        body = json.loads(request.content)
        with lock:
            requests.append({"context": scope.context_id, "body": body})
        messages = body["messages"]
        current = max(i for i, row in enumerate(messages) if row["role"] == "user")
        task = messages[current]["content"]
        results = [json.loads(row["content"]) for row in messages[current + 1:] if row["role"] == "tool"]
        if task == DERIVED_TASK and results and results[-1]["status"] != "succeeded":
            # Synthetic data only: expose the failed native outcome in CI
            # rather than allowing a swallowed fixture assertion to time out.
            print("Derived Work tool failed:", json.dumps(results[-1]), flush=True)
        if scope.context_id.startswith("work:") and task == WRITE_TASK and not results:
            delta = {"tool_calls": [{"index": 0, "id": "source-browser-mutate_file",
                "type": "function", "function": {"name": "mutate_file", "arguments": json.dumps(
                    {"path": "report-delivery.md", "expected_digest": None, "content": FILE_TEXT})}}]}
            reason = "tool_calls"
        elif scope.context_id.startswith("work:") and task == REPORT_TASK and not results:
            # The model may know the finished file path but no longer retain
            # the original opaque Kernel action ID from its earlier turn.
            delta = {"tool_calls": [{"index": 0, "id": "source-browser-work-report",
                "type": "function", "function": {"name": "work", "arguments": json.dumps(
                    {"op": "report", "kind": "finding", "note": NOTE,
                     "artifact_path": "report-delivery.md"})}}]}
            reason = "tool_calls"
        elif scope.context_id.startswith("work:") and task == DERIVED_TASK and len(results) < 5:
            assert all(row["status"] == "succeeded" for row in results), results
            if len(results) == 0:
                # A Kernel context carries an ActivityCenter ID, not the
                # public Work (TaskFront) ID. Discover the Work through the
                # same native tool that a later model can use.
                name, arguments = "work", {"op": "search", "query": SOURCE_TITLE}
            else:
                source, = (item for item in results[0]["output"]["works"]
                           if item["title"] == SOURCE_TITLE)
                original_work_id = source["id"]
                if len(results) == 1:
                    name, arguments = "work", {
                        "op": "inspect", "work_id": original_work_id, "include_files": True,
                    }
                else:
                    originals = results[1]["output"]["completed_files"]
                    selected, = (item for item in originals if item["path"] == "report-delivery.md")
                    assert selected["source_work_id"] == original_work_id
                    if len(results) == 2:
                        name, arguments = "read_file", {
                            "path": selected["path"], "expected_digest": selected["sha256"],
                        }
                    elif len(results) == 3:
                        assert FILE_TEXT.strip() in results[2]["output"]["content"]
                        name, arguments = "mutate_file", {
                            "path": "derived-delivery.md", "expected_digest": None,
                            "content": DERIVED_TEXT,
                        }
                    else:
                        name, arguments = "work", {
                            "op": "report", "kind": "result", "note": "Completed the derived file.",
                            "artifact_path": "derived-delivery.md",
                            "input_file_work_id": original_work_id,
                            "input_file_artifact_id": selected["artifact_id"],
                        }
            delta = {"tool_calls": [{"index": 0, "id": f"derived-browser-{len(results)}",
                "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments),
                }}]}
            reason = "tool_calls"
        else:
            assert all(row["status"] == "succeeded" for row in results), results
            if scope.context_id.startswith("work:") and task == WRITE_TASK:
                assert len(results) == 1
                with lock:
                    file_actions[scope.context_id] = {
                        "id": results[0]["output"]["action_request_id"], "turnId": scope.turn_id}
            elif scope.context_id.startswith("work:") and task == REPORT_TASK:
                assert len(results) == 1
            elif scope.context_id.startswith("work:") and task == DERIVED_TASK:
                assert len(results) == 5
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
                     posture=True, pulse=PulseSettings(enabled=False),
                     allow_workspace_actions=True, auto_apply_edits=True)

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
                            "origin": origin, "status": record["status"],
                            "reportedByPath": record["arguments"].get("artifact_path"),
                            "suppliedArtifactId": "artifact_id" in record["arguments"]})
        with lock:
            return {"reports": reports, "requests": len(requests),
                    "fileActions": list(file_actions.values())}

    route = app.router.routes.pop()
    index = next(i for i, value in enumerate(app.router.routes)
                 if getattr(value, "path", None) == "/{route:path}")
    app.router.routes.insert(index, route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
