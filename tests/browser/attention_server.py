"""Browser-only synthetic attention transport over the real persistent World/Kernel.

No external provider is contacted. This fixture cannot prove model quality.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def handler(request):
    messages = json.loads(request.content)["messages"]
    tool_arguments = None
    tool_name = "attention"
    material = json.dumps(messages)
    if "WORK_MATERIAL_JSON" in material:
        content = "PASS"
    elif "UNTRUSTED_REVIEW_DATA_JSON" in material:
        content = '{"mode":"none"}'
    else:
        last_user = max(index for index, message in enumerate(messages)
                        if message["role"] == "user")
        prompt = json.dumps(messages[last_user]["content"])
        has_result = any(message["role"] == "tool" for message in messages[last_user + 1:])
        task = messages[last_user]["content"]
        delivery_request = "\n\nMy request for this page:\nWrite the adopted observation to a Markdown file and report it Home."
        if isinstance(task, str) and task.startswith("Selected source for this page") and task.endswith(delivery_request):
            packet = json.loads(task.split("PAGE_SOURCE_JSON\n", 1)[1].removesuffix(delivery_request))
            assert packet["excerpt"] == "The local turn completed."
            results = [json.loads(row["content"]) for row in messages[last_user + 1:] if row["role"] == "tool"]
            assert all(row["status"] == "succeeded" for row in results), results
            if not results:
                tool_name = "mutate_file"
                tool_arguments = {"path": "pulse-observation.md", "expected_digest": None,
                                  "content": "# Adopted observation\n\n" + packet["excerpt"] + "\n"}
            elif len(results) == 1:
                tool_name = "work"
                tool_arguments = {"op": "report", "kind": "result", "note": "Completed the adopted observation.",
                                  "artifact_path": "pulse-observation.md"}
            else:
                assert len(results) == 2
        elif "Prepare a Markdown brief from the earlier completed background observation." in prompt:
            results = [json.loads(row["content"]) for row in messages[last_user + 1:] if row["role"] == "tool"]
            assert all(row["status"] == "succeeded" for row in results), results
            if not results:
                tool_name = "work"
                tool_arguments = {"op": "find", "query": "The local turn completed.",
                                  "pulse_results_only": True}
            else:
                hits = results[0]["output"]["matches"]
                assert len(hits) == 1 and hits[0]["role"] == "assistant", hits
                hit = hits[0]
                assert hit["pulse_event_id"] != hit["source_event_id"]
                if len(results) == 1:
                    tool_name = "mutate_file"
                    tool_arguments = {"path": "background-brief.md", "expected_digest": None,
                                      "content": "# Background brief\n\n" + hit["source_excerpt"] + "\n"}
                elif len(results) == 2:
                    tool_name = "work"
                    tool_arguments = {"op": "report", "kind": "result", "note": "Used the completed background observation.",
                                      "artifact_path": "background-brief.md",
                                      **{key: hit[key] for key in ("source_work_id", "source_event_id", "source_excerpt")}}
                else:
                    assert len(results) == 3
        elif not has_result and "RETAIN_ATTENTION_GOAL" in prompt:
            tool_arguments = {
                "op": "hold",
                "note": "Check that a visitor can actually try the experience.",
                "drive": 0.72,
            }
        elif not has_result and "RELEASE_LOCAL_THOUGHT" in prompt:
            tool_arguments = {"op": "release"}
        content = "Synthetic fixture response. The local turn completed."
    if tool_arguments is not None:
        # One turn can call the same tool more than once (find, then report).
        # Provider call IDs must be unique within that turn, including later rounds.
        round_index = sum(row["role"] == "tool" for row in messages[last_user + 1:])
        delta = {"tool_calls": [{
            "index": 0, "id": f"fixture-{round_index}-{tool_name}", "type": "function",
            "function": {"name": tool_name, "arguments": json.dumps(tool_arguments)},
        }]}
        reason = "tool_calls"
    else:
        delta = {"content": content}
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
    parser.add_argument("--spontaneous", action="store_true",
                        help="Exercise one real contentless background input.")
    parser.add_argument("--no-pulse", action="store_true",
                        help="Reopen a persisted World without new autonomous activity.")
    parser.add_argument("--posture", action="store_true",
                        help="Enable the native experience journal for explicit browser capture.")
    parser.add_argument("--deliverables", action="store_true",
                        help="Allow native file actions in the isolated adoption journey.")
    args = parser.parse_args()
    if os.environ.get('PER_PULSE_INSTALLED_WHEEL') == '1':
        import per_pulse.app
        import pulse_system
        import pulse_harness
        prefix = Path(sys.prefix).resolve()
        assert sys.prefix != sys.base_prefix, 'Use an isolated wheel installation'
        for module in (per_pulse.app, pulse_system, pulse_harness):
            assert Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
        assert (Path(per_pulse.app.__file__).parent / 'static' / 'index.html').is_file()
    os.environ["PER_PULSE_ATTENTION_BROWSER_KEY"] = "explicit-synthetic-browser-credential"
    uvicorn.run(
        create_app(args.data_dir, pulse=PulseSettings(
            enabled=not args.no_pulse, spontaneous_rate=1 if args.spontaneous else 0,
            max_background_pulses_per_hour=1 if args.spontaneous else 2,
            check_interval_seconds=0.01, refractory_seconds=.05 if args.spontaneous else 0,
            max_background_provider_tokens_per_hour=128,
            background_provider_token_reservation=64,
        ), posture=True if args.posture else None, model_factory=factory,
            **({"allow_workspace_actions": True, "auto_apply_edits": True} if args.deliverables else {})),
        host="127.0.0.1", port=args.port, log_level="warning",
    )
