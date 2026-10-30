"""Browser fixture for one native learning -> approved artifact -> sourced Home journey.

This is an explicit scripted HTTP model transport, NOT a live provider.
World, Kernel, posture tool, SQLite, approval, files and page routing are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx
import uvicorn

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

LESSON = "Let visitors try the central experience before optional setup."
CORRECTION = (
    "The intake form hides the product. Let me try its central interaction "
    "before optional profile questions."
)
requests = []


def streamed(*, text=None, tool=None, arguments=None):
    if tool:
        delta = {"tool_calls": [{"index": 0, "id": "journey-" + (arguments["op"] if tool == "posture" else tool),
                 "type": "function", "function": {
                     "name": tool, "arguments": json.dumps(arguments)}}]}
    else:
        delta = {"content": text}
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {},
                      "finish_reason": "tool_calls" if tool else "stop"}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 6}},
    ]
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def handler(request):
    body = json.loads(request.content)
    messages = body["messages"]
    material = json.dumps(messages, ensure_ascii=False)
    requests.append({"material": material, "tools": [t.get("function", {}).get("name")
                       for t in body.get("tools", [])]})
    if "WORK_MATERIAL_JSON" in material:
        # An explicitly requested form is the practice-first counterexample.
        # Its shared lesson reaches the fork, which should decline the change.
        if "BUILD_FIXED_REGISTRATION_ARTIFACT" in material:
            return streamed(text="PASS")
        # Native Posture computes a scoped semantic contribution first. The
        # later main turn sees that contribution, not the private old episode.
        if LESSON in material and '"candidate_response"' not in material:
            return streamed(text="For the visitor's first experience: " + LESSON)
        return streamed(text="PASS")

    current_index = max(i for i, msg in enumerate(messages) if msg["role"] == "user")
    current = messages[current_index:]
    task = json.dumps(current, ensure_ascii=False)
    tool_results = [m for m in current if m["role"] == "tool"]

    if CORRECTION in task:
        if not tool_results:
            return streamed(tool="posture", arguments={"op": "list"})
        if len(tool_results) == 1:
            data = json.loads(tool_results[-1]["content"])
            if data["status"] != "succeeded":
                return streamed(text="Correction lookup failed. [Synthetic browser fixture]")
            sources = data["output"]["recent_sources"]
            original = next(x for x in reversed(sources) if x["kind"] == "assistant_result")
            feedback = next(x for x in reversed(sources) if x["source"] == "user")
            return streamed(tool="posture", arguments={
                "op": "remember",
                "source_message_id": original["message_id"],
                "feedback_message_id": feedback["message_id"],
                "title": "First usable interaction",
                "lesson": LESSON,
                "conditions": {"all": [
                    "A visitor asks to explore a practice product",
                    "The central interaction can be tried immediately"]},
                "counterexamples": ["The user explicitly needs a fixed registration form."],
                "shift": {
                    "understanding": "Find the earliest usable interaction.",
                    "candidates": "Choose a playable slice over optional intake.",
                    "delivery": "Verify it can be tried immediately."},
            })
        return streamed(text="I will offer an interaction before optional setup. [Synthetic browser fixture]")

    if "BUILD_FIXED_REGISTRATION_ARTIFACT" in task:
        if not tool_results:
            return streamed(tool="mutate_file", arguments={
                "path": "registration.txt", "expected_digest": None,
                "content": "Required registration fields.\n"})
        return streamed(text="The required registration file is complete. [Synthetic browser fixture]")

    if "BUILD_PRACTICE_ARTIFACT" in task:
        if not tool_results:
            learned = LESSON in material
            return streamed(tool="mutate_file", arguments={
                "path": "practice.txt" if learned else "intake.txt",
                "expected_digest": None,
                "content": ("A playable first interaction.\n" if learned
                            else "Twelve-field intake form.\n")})
        return streamed(text="The file task finished. [Synthetic browser fixture]")

    if "DESIGN_FIRST_VIEW" in task:
        return streamed(text="The first screen is a twelve-field intake form. [Synthetic browser fixture]")
    return streamed(text="The continuing subject received the task. [Synthetic browser fixture]")


def model_factory(_scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "synthetic-browser-journey",
        credential_env="PER_PULSE_JOURNEY_FIXTURE_KEY",
    ), transport=httpx.MockTransport(handler))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    os.environ["PER_PULSE_JOURNEY_FIXTURE_KEY"] = "not-a-provider-credential"
    state = Path(args.data_dir)
    app = create_app(state, posture=True, allow_workspace_actions=True, model_factory=model_factory)

    @app.get("/__journey/status")
    def status():
        tasks = [r["material"] for r in requests
                 if "BUILD_PRACTICE_ARTIFACT" in r["material"]
                 and "WORK_MATERIAL_JSON" not in r["material"]
                 and '"role": "tool"' not in r["material"]]
        fixed_reviews = [r["material"] for r in requests
                         if "BUILD_FIXED_REGISTRATION_ARTIFACT" in r["material"]
                         and "WORK_MATERIAL_JSON" in r["material"]]
        fixed_main = [r["material"] for r in requests
                      if "BUILD_FIXED_REGISTRATION_ARTIFACT" in r["material"]
                      and "WORK_MATERIAL_JSON" not in r["material"]
                      and '"role": "tool"' not in r["material"]]
        return {"files": sorted(p.name for p in (state / "workspace").glob("*.txt")),
                "tasks": [{"hasLesson": LESSON in t, "leaksCorrection": CORRECTION in t,
                           "leaksOriginal": "The first screen is a twelve-field intake form." in t}
                          for t in tasks],
                "fixedReviews": [{"hasLesson": LESSON in t,
                                  "leaksCorrection": CORRECTION in t,
                                  "leaksOriginal": "The first screen is a twelve-field intake form." in t}
                                 for t in fixed_reviews],
                "fixedMain": [{"hasLesson": LESSON in t} for t in fixed_main],
                "fixtureTrace": [{"tools": r["tools"], "hasCorrection": CORRECTION in r["material"],
                                  "hasLesson": LESSON in r["material"],
                                  "end": r["material"][-700:]}
                                 for r in requests[-16:]]}

    # Ensure the introspection fixture route precedes the React catch-all.
    route = app.router.routes.pop()
    if getattr(route, "path", None) == "/__journey/status":
        insert_at = next(i for i, r in enumerate(app.router.routes)
                         if getattr(r, "path", None) == "/{route:path}")
        app.router.routes.insert(insert_at, route)
    else:
        app.router.routes.append(route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
