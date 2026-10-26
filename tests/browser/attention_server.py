"""Browser-only synthetic attention transport over the real persistent World/Kernel.

No external provider is contacted. This fixture cannot prove model quality.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import os

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def handler(request):
    messages = json.loads(request.content)["messages"]
    last_user = max(index for index, message in enumerate(messages)
                    if message["role"] == "user")
    prompt = json.dumps(messages[last_user]["content"])
    has_result = any(message["role"] == "tool" for message in messages[last_user + 1:])
    tool_arguments = None
    if not has_result and "RETAIN_ATTENTION_GOAL" in prompt:
        tool_arguments = {
            "op": "hold",
            "note": "Check that a visitor can actually try the experience.",
            "drive": 0.72,
        }
    elif not has_result and "RELEASE_LOCAL_THOUGHT" in prompt:
        tool_arguments = {"op": "release"}
    if tool_arguments is not None:
        delta = {"tool_calls": [{
            "index": 0, "id": "fixture-attention", "type": "function",
            "function": {"name": "attention", "arguments": json.dumps(tool_arguments)},
        }]}
        reason = "tool_calls"
    else:
        delta = {"content": "Synthetic fixture response. The local turn completed."}
        reason = "stop"
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
         "usage": {"prompt_tokens": 13, "completion_tokens": 9}},
    ]
    wire = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=wire + "data: [DONE]\n\n")


def factory(_scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "native-attention-browser-fixture",
        credential_env="PER_PULSE_ATTENTION_BROWSER_KEY",
    ), transport=httpx.MockTransport(handler))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    os.environ["PER_PULSE_ATTENTION_BROWSER_KEY"] = "explicit-synthetic-browser-credential"
    uvicorn.run(
        create_app(args.data_dir, pulse=PulseSettings(
            enabled=True, spontaneous_rate=0, max_background_pulses_per_hour=2,
            check_interval_seconds=0.01, refractory_seconds=0,
        ), model_factory=factory),
        host="127.0.0.1", port=args.port, log_level="warning",
    )
