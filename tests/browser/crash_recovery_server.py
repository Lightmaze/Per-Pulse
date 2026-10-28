"""Native service fixture stopped after a real file write, before its receipt.

Only the model decisions and the post-effect barrier are synthetic. Approval,
workspace mutation, World/Kernel persistence and process death are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import threading

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mode", choices=("effect", "reply"), required=True)
    args = parser.parse_args()
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    journal = args.evidence_dir / "events.jsonl"
    journal_lock = threading.Lock()
    os.environ["PER_PULSE_CRASH_FIXTURE_KEY"] = "synthetic-local-only"

    def record(event):
        with journal_lock, journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def respond(request):
        payload = json.loads(request.content)
        record({"kind": "request", "mode": args.mode})
        if args.mode == "effect":
            delta = {"tool_calls": [{"index": 0, "id": "fixture-crash-write", "type": "function",
                "function": {"name": "mutate_file", "arguments": json.dumps({
                    "path": "interrupted.txt", "expected_digest": None,
                    "content": "Written once before the process died.\n",
                })}}]}
            finish = "tool_calls"
        else:
            # Deliberately respond only to a NEW human instruction. The test
            # independently asserts that restarting never calls this fixture.
            last_user = next((m for m in reversed(payload["messages"])
                              if m.get("role") == "user"), {})
            assert "CONTINUE_AFTER_REVIEW" in json.dumps(last_user), "Old work was replayed"
            delta = {"content": "Continued the same Work after review. [Synthetic crash fixture]"}
            finish = "stop"
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
             "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}},
        ]
        wire = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=wire + "data: [DONE]\n\n")

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "crash-fixture",
                           credential_env="PER_PULSE_CRASH_FIXTURE_KEY"),
            transport=httpx.MockTransport(respond),
        )

    original_replace = StructuredWorkspaceToolProvider._replace

    def replace_then_block(self, plan):
        original_replace(self, plan)
        if args.mode == "effect":
            record({"kind": "effect", "path": plan.relative_path})
            # The native atomic file replacement already happened. Stop before
            # terminal_operation persists a receipt (which also settles the
            # action), not after execute_for_scope returns. The parent kills this
            # OS process while the action's durable status remains RUNNING.
            threading.Event().wait()

    StructuredWorkspaceToolProvider._replace = replace_then_block
    app = create_app(
        args.data_dir, model_factory=factory, allow_workspace_actions=True,
        pulse=PulseSettings(enabled=True, spontaneous_rate=0, max_background_pulses_per_hour=0),
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
