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
from pulse_system.core.engram.manager import _SUCCESSION_PROMPT


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
    summary_allowed = threading.Event()
    os.environ["PER_PULSE_CRASH_FIXTURE_KEY"] = "synthetic-local-only"

    def record(event):
        with journal_lock, journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def respond(request):
        payload = json.loads(request.content)
        last_user = next((m for m in reversed(payload["messages"])
                          if m.get("role") == "user"), {})
        is_summary = summary_allowed.is_set() and _SUCCESSION_PROMPT in json.dumps(last_user, ensure_ascii=False)
        record({"kind": "summary_request" if is_summary else "request", "mode": args.mode})
        if is_summary:
            delta = {"content": "Retain the original Work and its unconfirmed file effect. [Synthetic summary fixture]"}
            finish = "stop"
        elif args.mode == "effect":
            delta = {"tool_calls": [{"index": 0, "id": "fixture-crash-write", "type": "function",
                "function": {"name": "mutate_file", "arguments": json.dumps({
                    "path": "interrupted.txt", "expected_digest": None,
                    "content": "Written once before the process died.\n",
                })}}]}
            finish = "tool_calls"
        else:
            # Deliberately respond only to a NEW human instruction. The test
            # independently asserts that restarting never calls this fixture.
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

    def factory(scope):
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

    @app.post("/api/__crash/succession/{page_id}")
    def commit_native_succession(page_id: str):
        # A test-only trigger for the existing native summary/commit path.
        # It is never mounted by the product application. Only the explicitly
        # requested native summary may bypass the new-human-input fixture check.
        assert args.mode == "reply"
        pages = app.state.pages
        runtime = pages.runtime
        source = next(message["id"] for message in reversed(pages.view(page_id)["messages"])
                      if message["role"] == "assistant" and message["status"] == "settled")
        previous = runtime.continuity_engram_id
        summary_allowed.set()
        try:
            prepared = runtime._mgr.prepare_succession(
                previous, parent_event_id=source, runtime_fence=runtime._current_runtime_fence(),
            )
            successor = runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence())
        finally:
            summary_allowed.clear()
        return {"previous": previous, "successor": successor.new_id, "pageId": page_id,
                "lineage": runtime.causal_ledger.committed_history_lineage(runtime.world_id, successor.new_id)}

    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
