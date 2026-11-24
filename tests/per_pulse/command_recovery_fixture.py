"""Subprocess fixture for actual Docker recovery; all model choices are synthetic.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import httpx
from fastapi.testclient import TestClient

from per_pulse.app import create_app
from per_pulse import service as page_service
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.025)
    raise TimeoutError("Native fixture state did not arrive")


def main():
    root, mode = Path(sys.argv[1]), sys.argv[2]
    world = root / "world"
    os.environ["PER_PULSE_COMMAND_RECOVERY_FIXTURE_KEY"] = "explicit-local-test-double"
    if mode == "owner":
        os.environ["P_PULSE_COMMAND_IMAGE"] = os.environ["PER_PULSE_DOCKER_IMAGE"]
    else:
        os.environ.pop("P_PULSE_COMMAND_IMAGE", None)
    # Exercise the supported lease configuration and real wall clock. Nothing
    # replaces the keeper, SQL lease, publication guard, or process ownership.
    page_service.RuntimeServiceConfig = partial(page_service.RuntimeServiceConfig,
        runtime_lease_ttl_sec=5, runtime_lease_renew_interval_sec=1)
    command = (
        "from pathlib import Path; import time; "
        "Path('container-started').write_text(str(time.time())); time.sleep(20); "
        "Path('shared.txt').write_text('old container late write\\n'); "
        "Path('late-write').write_text('this must not happen after recovery')"
    )
    tool = "run_command" if mode == "owner" else "mutate_file"
    arguments = {"argv": ["python3", "-c", command], "timeout_seconds": 30} if mode == "owner" else {
        "path": "shared.txt", "expected_digest": hashlib.sha256(b"original\n").hexdigest(),
        "content": "successor approved write\n",
    }

    def factory(_scope):
        def respond(request):
            messages = json.loads(request.content)["messages"]
            latest = max(i for i, m in enumerate(messages) if m["role"] == "user")
            if any(m["role"] == "tool" for m in messages[latest + 1:]):
                delta, reason = {"content": "Synthetic model observed the native action receipt."}, "stop"
            else:
                delta = {"tool_calls": [{"index": 0, "id": "fixture-" + mode, "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(arguments)}}]}
                reason = "tool_calls"
            chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}]
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=
                "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions",
            "docker-recovery-test-double", credential_env="PER_PULSE_COMMAND_RECOVERY_FIXTURE_KEY"),
            transport=httpx.MockTransport(respond))

    with TestClient(create_app(world, model_factory=factory, posture=False, allow_workspace_actions=True,
                              pulse=page_service.PulseSettings(enabled=False)), base_url="http://localhost") as client:
        pages = client.app.state.pages
        if mode == "owner":
            created = client.post("/api/pages", json={"title": "Native Docker recovery", "instruction": "Run the exact synthetic command."})
            assert created.status_code == 201, created.text
            page = created.json()["page"]["id"]
        else:
            original = json.loads((root / "owner-ready.json").read_text())
            page = original["page"]
            recovered = pages.execution.snapshot(page)
            assert recovered["uncertainActions"][0]["actionId"] == original["action"]
            assert recovered["historicalUncertainty"]
            stopped = pages.execution.cancel(page, original["turn"])
            assert stopped["accepted"]
            sent = client.post(f"/api/pages/{page}/messages", json={"content": "Write the exact successor result in this original Work."})
            assert sent.status_code == 202, sent.text

        def pending():
            snapshot = pages.execution.snapshot(page)
            return snapshot if snapshot["pendingActions"] and snapshot["pendingActions"][0]["canResolve"] else None
        snapshot = wait_for(pending)
        action = snapshot["pendingActions"][0]
        (root / (mode + "-ready.json")).write_text(json.dumps({"pid": os.getpid(), "page": page,
            "action": action["actionId"], "turn": snapshot["turnId"], "world": pages.runtime.world_id}))
        receipt = pages.execution.resolve_approval(page, action["actionId"], "allow_once")
        assert receipt["actionStatus"] == "succeeded", receipt
        wait_for(lambda: any(m["role"] == "assistant" for m in pages.view(page)["messages"]))
        (root / (mode + "-result.json")).write_text(json.dumps({"receipt": receipt,
            "shared": (world / "workspace/shared.txt").read_text(), "page": page,
            "command_recovery": pages.runtime.harness.command_recovery}))


if __name__ == "__main__":
    main()
