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


# Both actual settled answers share a preamble longer than the discovery
# excerpt. The distinguishing operating condition is in the later passage.
DECISION_PREAMBLE = "Calibration record retained. " * 6
DECISION_ANSWERS = {
    "offline": DECISION_PREAMBLE + "Offline battery operation: use stored readings. 离线运行时使用已保存的读数。",
    "mains": DECISION_PREAMBLE + "Mains battery operation: use outlet checks. 市电运行时检查插座。",
}
DECISION_TASKS = {
    "请为电池离线运行编写决策简报。": ("offline", "offline-zh", "离线运行"),
    "请为电池市电运行编写决策简报。": ("mains", "mains-zh", "市电运行"),
    "请为花园灌溉编写决策简报。": ("rain", "rain-zh", None),
}

CORRECTION_NOTE = "For offline battery work, verify the timestamp of stored readings before use."
CORRECTION_ADVICE = "Check the timestamp of stored readings before relying on the historical answer."
OUTCOME_NOTE = "For offline battery work, verify the timestamp of stored readings before use. If it is missing, obtain a fresh reading."
OUTCOME_ADVICE = "If the stored reading has no timestamp, obtain a fresh reading before relying on it."
CORRECTION_TASKS = {
    "Prepare a Markdown decision brief for 电池 under offline constraints in decision-revised.md.": "revised",
    "Prepare a Markdown decision brief for 电池 under offline constraints in decision-outcome.md.": "outcome",
    "Prepare a Markdown decision brief for 电池 under offline constraints in decision-withdrawn.md.": "withdrawn",
}


def handler(request):
    messages = json.loads(request.content)["messages"]
    tool_arguments = None
    tool_name = "attention"
    material = json.dumps(messages)
    if "WORK_MATERIAL_JSON" in material:
        content = "PASS"
        packet = next((json.loads(row["content"].split("WORK_MATERIAL_JSON\n", 1)[1])
                       for row in messages if isinstance(row.get("content"), str)
                       and "WORK_MATERIAL_JSON\n" in row["content"]), None)
        if packet and "candidate_response" not in packet:
            task = next((row["content"] for row in reversed(packet["current_work"]["messages"])
                         if row["role"] == "user"), "")
            if task in CORRECTION_TASKS:
                matching = [episode for episode in packet.get("posture_experiences", {}).get("episodes", [])
                            if episode.get("title") == "Offline battery reading procedure"]
                if CORRECTION_TASKS[task] == "revised":
                    assert len(matching) == 1, matching
                    lesson = matching[0]
                    assert lesson["revision"] == 2 and lesson["note"] == CORRECTION_NOTE, lesson
                    assert lesson["excerpt"] == DECISION_ANSWERS["offline"], lesson
                    assert not lesson["encounter_available"] and "encounter" not in lesson, lesson
                    assert lesson["feedback"] == [{"verdict": "revised", "revision": 1}], lesson
                    content = CORRECTION_ADVICE
                elif CORRECTION_TASKS[task] == "outcome":
                    assert len(matching) == 1, matching
                    lesson = matching[0]
                    assert lesson["revision"] == 5 and lesson["note"] == OUTCOME_NOTE, lesson
                    assert lesson["excerpt"] == DECISION_ANSWERS["offline"], lesson
                    assert not lesson["encounter_available"] and "encounter" not in lesson, lesson
                    assert lesson["feedback"] == [{"verdict": "revised", "revision": 1},
                                                  {"verdict": "not_useful", "revision": 2},
                                                  {"verdict": "revised", "revision": 3},
                                                  {"verdict": "harmful", "revision": 4, "report_scope": "result_only",
                                                   "result_offered_revisions": [2]}], lesson
                    content = OUTCOME_ADVICE
                else:
                    assert not matching, matching
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
        elif isinstance(task, str) and task.startswith("Prepare a Markdown status brief for "):
            topic = task.removeprefix("Prepare a Markdown status brief for ").removesuffix(".")
            results = [json.loads(row["content"]) for row in messages[last_user + 1:] if row["role"] == "tool"]
            assert all(row["status"] == "succeeded" for row in results), results
            candidates = []
            for message in messages[last_user + 1:]:
                try:
                    packet = json.loads(message.get("content", ""))
                except (TypeError, ValueError):
                    continue
                if isinstance(packet, dict) and packet.get("kind") == "pulse.related-work-results.v1":
                    assert message["role"] == "assistant"
                    candidates.extend(packet["candidates"])
            related = topic in {"receiving propagation", "propagation while receiving"}
            path = "status-" + topic.replace(" ", "-") + ".md"
            if not results:
                if related:
                    assert len(candidates) == 1 and candidates[0]["work_title"] == "Receiving propagation", candidates
                    tool_name = "work"
                    tool_arguments = {"op": "find", "pulse_results_only": True, "query": candidates[0]["query"]}
                else:
                    assert candidates == [], candidates
                    tool_name = "mutate_file"
                    tool_arguments = {"path": path, "expected_digest": None,
                                      "content": "# Status brief\n\nVerify the current state before drawing conclusions.\n"}
            elif related:
                assert not candidates, "The discovery cue belongs only to the first decision."
                hits = results[0]["output"]["matches"]
                assert len(hits) == 1 and hits[0]["work_title"] == "Receiving propagation", hits
                hit = hits[0]
                if len(results) == 1:
                    tool_name = "mutate_file"
                    tool_arguments = {"path": path, "expected_digest": None,
                                      "content": "# Status brief\n\n" + hit["source_excerpt"] + "\n"}
                elif len(results) == 2:
                    tool_name = "work"
                    tool_arguments = {"op": "report", "kind": "result", "note": "Prepared the status brief.",
                                      "artifact_path": path,
                                      **{key: hit[key] for key in ("source_work_id", "source_event_id", "source_excerpt")}}
                else:
                    assert len(results) == 3
            elif len(results) == 1:
                assert not candidates
                tool_name = "work"
                tool_arguments = {"op": "report", "kind": "result", "note": "Prepared a fresh status brief.",
                                  "artifact_path": path}
            else:
                assert len(results) == 2
        elif isinstance(task, str) and (task.startswith("Prepare a Markdown decision brief for ")
                                       or task in DECISION_TASKS):
            if task in DECISION_TASKS:
                mode, filename, condition = DECISION_TASKS[task]
            else:
                mode = task.split(" under ", 1)[1].split()[0]
                filename, condition = mode, mode.capitalize()
            if task in CORRECTION_TASKS:
                filename = CORRECTION_TASKS[task]
            expected = DECISION_ANSWERS.get(mode)
            path = "decision-" + filename + ".md"
            results = [json.loads(row["content"]) for row in messages[last_user + 1:] if row["role"] == "tool"]
            assert all(row["status"] == "succeeded" for row in results), results
            candidates = []
            for message in messages[last_user + 1:]:
                try:
                    packet = json.loads(message.get("content", ""))
                except (TypeError, ValueError):
                    continue
                if isinstance(packet, dict) and packet.get("kind") == "pulse.related-work-results.v1":
                    assert message["role"] == "assistant"
                    candidates.extend(packet["candidates"])
            if not results:
                if expected:
                    assert candidates and condition in candidates[0]["query"], candidates
                    assert candidates[0]["query"] in expected, candidates
                    assert candidates[0]["query"] not in DECISION_ANSWERS[
                        "mains" if mode == "offline" else "offline"], candidates
                    assert candidates[0]["work_title"] == "电池", candidates
                    if mode == "offline":
                        assert len(candidates) == 2, candidates
                        assert candidates[0]["source_event_id"] != candidates[1]["source_event_id"], candidates
                    tool_name = "work"
                    tool_arguments = {"op": "find", "pulse_results_only": True, "query": candidates[0]["query"]}
                else:
                    assert not candidates, candidates
                    tool_name = "mutate_file"
                    tool_arguments = {"path": path, "expected_digest": None,
                                      "content": "# Decision brief\n\nCheck the current evidence.\n"}
            elif expected:
                assert not candidates
                hits = results[0]["output"]["matches"]
                assert len(hits) == 1 and hits[0]["source_excerpt"] == expected, hits
                hit = hits[0]
                if len(results) == 1:
                    tool_name = "mutate_file"
                    text = "# Decision brief\n\n" + hit["source_excerpt"] + "\n"
                    if task in CORRECTION_TASKS:
                        advice = OUTCOME_ADVICE if filename == "outcome" else CORRECTION_ADVICE
                        contributed = any(isinstance(row.get("content"), str)
                                          and "POSTURE_WORK_CONTRIBUTION" in row["content"]
                                          and advice in row["content"] for row in messages)
                        assert contributed == (filename in {"revised", "outcome"}), messages
                        if contributed:
                            text += "\n" + advice + "\n"
                    tool_arguments = {"path": path, "expected_digest": None, "content": text}
                elif len(results) == 2:
                    tool_name = "work"
                    tool_arguments = {"op": "report", "kind": "result", "note": "Prepared the decision brief.",
                                      "artifact_path": path,
                                      **{key: hit[key] for key in ("source_work_id", "source_event_id", "source_excerpt")}}
                else:
                    assert len(results) == 3
            elif len(results) == 1:
                tool_name = "work"
                tool_arguments = {"op": "report", "kind": "result", "note": "Prepared a fresh decision brief.",
                                  "artifact_path": path}
            else:
                assert len(results) == 2
        elif not has_result and "RETAIN_ATTENTION_GOAL" in prompt:
            tool_arguments = {
                "op": "hold",
                "note": "Check that a visitor can actually try the experience.",
                "drive": 0.72,
            }
        elif not has_result and "RELEASE_LOCAL_THOUGHT" in prompt:
            tool_arguments = {"op": "release"}
        content = "Synthetic fixture response. The local turn completed."
        # The receiver's latest explicit operating mode supplies a different
        # settled answer on each real propagation. No stored event is forged.
        for message in reversed(messages):
            if message["role"] != "user":
                continue
            if message.get("content") == "Set the test operating mode to offline.":
                content = DECISION_ANSWERS["offline"]
                break
            if message.get("content") == "Set the test operating mode to mains.":
                content = DECISION_ANSWERS["mains"]
                break
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
    app = create_app(args.data_dir, pulse=PulseSettings(
        enabled=not args.no_pulse, spontaneous_rate=1 if args.spontaneous else 0,
        max_background_pulses_per_hour=1 if args.spontaneous else 2,
        check_interval_seconds=0.01, refractory_seconds=.05 if args.spontaneous else 0,
        max_background_provider_tokens_per_hour=128,
        background_provider_token_reservation=64,
    ), posture=True if args.posture else None, model_factory=factory,
        **({"allow_workspace_actions": True, "auto_apply_edits": True} if args.deliverables else {}))

    if args.deliverables and args.no_pulse:
        @app.post("/__attention/age-result/{result_id}")
        def age_result(result_id: str):
            # Authored terminal history fixtures in the actual native ledger,
            # not 520 model turns or a modified HTTP page projection. These
            # rows have no claimable turn roots. The original result, Kernel
            # computation, file receipt and experience remain untouched.
            ledger = app.state.pages.runtime.causal_ledger
            result = ledger.get_event(result_id)
            assert result is not None and result.kind.value == "assistant_result"
            assert result.status.value == "settled" and result.center_id is not None
            parent = ledger.get_event(result.parent_event_id)
            assert parent is not None
            for index in range(520):
                ledger.record_child(
                    result.id, kind="stimulus", domain=parent.domain, source="user",
                    content=f"Later field note {index:04d}. [Authored history fixture]",
                    metadata={"authored_history_fixture": True},
                )
            return {"inserted": 520}

        app.router.routes.insert(0, app.router.routes.pop())

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
