"""Explicit synthetic model decisions for the native autonomous user flow.

Only HTTP replies are scripted. Work adoption, files, reports and accounting
use the production World and Kernel. Copyright 2026 Lightmaze Contributors.
Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json
import threading
import traceback

import httpx

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.core.pulse.autonomy import FIRST_DEPARTURE_ORIENTATION, PROVIDER_CONTINUATION_ORIENTATION

PRACTICE_TITLE = "A small practice"
PRACTICE_TEXT = "A persisted result from explicitly synthetic autonomous practice.\n"
PRACTICE_INSTRUCTION = "AUTONOMOUS_PRACTICE_FIXTURE: create practice.txt and report the actual result."
REVISION_TASK = "Change the purpose: produce a planning brief instead."
RETAINED_TEXT = "Keep this exact effect.\n"
PLANNING_TEXT = "# Planning brief\nVerify the assumptions before release.\n"
SOURCE_TITLE = "Planning source Work"
SOURCE_TASK = "Prepare the release evidence and the planning hypothesis for later use."
SOURCE_REPLY = "The release evidence and planning hypothesis are distinct source files. [Synthetic fixture]"
SOURCE_NOTE = "For a planning brief, choose planning-hypothesis.md, not release-evidence.md. Keep the hypothesis unverified."
SOURCE_ADVICE = "Read planning-hypothesis.md from Planning source Work instead of release-evidence.md."


def response(*, content=None, tool=None, arguments=None, call_id=None):
    delta = ({"tool_calls": [{"index": 0, "id": call_id or "fixture-" + tool + "-" + arguments.get("op", "call"),
              "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]}
             if tool else {"content": content})
    frames = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 6}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join("data: " + json.dumps(item) + "\n\n" for item in frames) + "data: [DONE]\n\n")


class AutonomousPracticeFixture:
    def __init__(self, setenv, *, purpose_revision=False, learned_revision=False, long_boundary=None):
        self.purpose_revision = purpose_revision
        self.learned_revision = learned_revision
        self.long_boundary = long_boundary
        setenv("PER_PULSE_AUTONOMOUS_FIXTURE_KEY", "explicit-synthetic-test-credential")
        self.requests = []
        self.lock = threading.Lock()

    def factory(self, scope):
        def handler(request):
            body = json.loads(request.content)
            messages = body["messages"]
            material = json.dumps(messages)
            with self.lock:
                self.requests.append({"context": scope.context_id, "material": material})
            if "WORK_MATERIAL_JSON" in material:
                if self.learned_revision:
                    packet = next((row["content"].split("WORK_MATERIAL_JSON\n", 1)[1]
                                   for row in messages if "WORK_MATERIAL_JSON\n" in row["content"]), None)
                    if packet is not None:
                        work = json.loads(packet)
                        visible = work["current_work"]["messages"]
                        latest = next((row["content"] for row in reversed(visible)
                                       if row["role"] == "user"), "")
                        resumed = any(row["role"] == "system" and
                                      "Internal provider continuation, not a user message." in row["content"]
                                      for row in visible)
                        if (latest == REVISION_TASK and resumed and "candidate_response" not in work
                                and SOURCE_NOTE in json.dumps(work.get("posture_experiences", {}))):
                            return response(content=SOURCE_ADVICE)
                return response(content="PASS")
            if "UNTRUSTED_REVIEW_DATA_JSON" in material:
                return response(content='{"mode":"none"}')
            # A budget continuation is a system input, not a new user turn.
            # Start its fixture decision at that notice, not the earlier departure
            # or an already completed tool in the preceding turn.
            user_boundary = max((i for i, message in enumerate(messages)
                                 if message["role"] == "user"), default=-1)
            continuation_boundary = max((i for i, message in enumerate(messages)
                                         if message["role"] == "system"
                                         and "Internal provider continuation, not a user message."
                                         in message["content"]), default=-1)
            if continuation_boundary > user_boundary:
                boundary = continuation_boundary
            else:
                boundary = user_boundary
            current = messages[max(0, boundary):]
            orientation = messages[boundary]["content"] if boundary >= 0 else ""
            tools = [message for message in current if message["role"] == "tool"]
            # The first departure and the first Work can each reach the
            # fourth round without a budget interruption. Put the actual
            # handoff at that boundary, not just another read-only tool.
            if (self.long_boundary == "create" and not scope.context_id.startswith("work:")
                    and (FIRST_DEPARTURE_ORIENTATION in orientation or
                         (continuation_boundary > user_boundary
                          and FIRST_DEPARTURE_ORIENTATION in material))):
                results = [json.loads(row["content"]) for row in messages if row["role"] == "tool"]
                assert all(row["status"] == "succeeded" for row in results), results
                if len(results) == 0:
                    return response(tool="work", arguments={"op": "list"})
                if len(results) == 1:
                    return response(tool="work", arguments={"op": "search", "query": PRACTICE_TITLE})
                if len(results) == 2:
                    return response(tool="work", arguments={"op": "find", "query": "practice"})
                if len(results) == 3:
                    return response(tool="work", arguments={"op": "create", "title": PRACTICE_TITLE,
                                                             "instruction": PRACTICE_INSTRUCTION})
                assert len(results) == 4
                return response(content="I prepared the practice Work. [Synthetic fixture]")
            if self.long_boundary == "report" and scope.context_id.startswith("work:"):
                results = [json.loads(row["content"]) for row in messages if row["role"] == "tool"]
                assert all(row["status"] == "succeeded" for row in results), results
                if len(results) == 0:
                    return response(tool="mutate_file", arguments={
                        "path": "practice.txt", "expected_digest": None, "content": PRACTICE_TEXT})
                if len(results) == 1:
                    return response(tool="read_file", arguments={
                        "path": "practice.txt", "expected_digest": hashlib.sha256(PRACTICE_TEXT.encode()).hexdigest()})
                assert results[1]["output"]["content"] == PRACTICE_TEXT
                if len(results) == 2:
                    return response(tool="work", arguments={"op": "list"})
                if len(results) == 3:
                    return response(tool="work", arguments={"op": "report", "kind": "result",
                        "note": "The verified practice file is ready.", "artifact_path": "practice.txt"})
                assert len(results) == 4
                return response(content="The verified practice file is ready. [Synthetic fixture]")
            if self.purpose_revision:
                if scope.context_id.startswith("work:"):
                    if self.learned_revision and orientation == SOURCE_TASK:
                        if not tools:
                            return response(tool="mutate_file", call_id="source-release", arguments={
                                "path": "release-evidence.md", "expected_digest": None,
                                "content": "Installation is confirmed.\n"})
                        if len(tools) == 1:
                            return response(tool="mutate_file", call_id="source-planning", arguments={
                                "path": "planning-hypothesis.md", "expected_digest": None,
                                "content": "Verify the assumptions before release.\n"})
                        assert len(tools) == 2
                        assert all(json.loads(row["content"])["status"] == "succeeded" for row in tools)
                        return response(content=SOURCE_REPLY)
                    if continuation_boundary > user_boundary:
                        assert user_boundary >= 0 and messages[user_boundary]["content"] == REVISION_TASK
                        if self.learned_revision:
                            # A new bounded activation keeps the same revised purpose.
                            # Use the actual receipts since that user revision, including
                            # the preceding slice, rather than repeat its file effects.
                            results = [json.loads(row["content"]) for row in messages[user_boundary:]
                                       if row["role"] == "tool"]
                            assert all(row["status"] == "succeeded" for row in results), results
                            if not results:
                                return response(tool="work", arguments={"op": "search", "query": SOURCE_TITLE})
                            original, = (row for row in results[0]["output"]["works"]
                                         if row["title"] == SOURCE_TITLE)
                            if len(results) == 1:
                                return response(tool="work", arguments={"op": "inspect", "work_id": original["id"],
                                                                       "include_files": True})
                            originals = results[1]["output"]["completed_files"]
                            if len(results) == 2:
                                # Only the admitted contribution can choose the input at this decision.
                                admitted = any(row["role"] == "system"
                                               and "POSTURE_WORK_CONTRIBUTION" in row["content"]
                                               and SOURCE_ADVICE in row["content"] for row in messages)
                                choice = "planning-hypothesis.md" if admitted else "release-evidence.md"
                                selected, = (row for row in originals if row["path"] == choice)
                                return response(tool="read_file", arguments={"path": selected["path"],
                                                                           "expected_digest": selected["sha256"]})
                            # Carry the actual read receipt forward, not a newly evaluated preference.
                            # An advisory message may be omitted from a subsequent request.
                            read_digest = results[2]["output"]["digest"]
                            selected, = (row for row in originals if row["sha256"] == read_digest)
                            assert selected["source_work_id"] == original["id"]
                            if len(results) == 3:
                                return response(tool="mutate_file", arguments={
                                    "path": "planning-brief.md", "expected_digest": None,
                                    "content": "# Planning brief\n" + results[2]["output"]["content"]})
                            if len(results) == 4:
                                return response(tool="work", arguments={"op": "report", "kind": "result",
                                    "note": "The revised planning brief is ready.", "artifact_path": "planning-brief.md",
                                    "input_file_work_id": original["id"], "input_file_artifact_id": selected["artifact_id"]})
                            assert len(results) == 5
                            return response(content="Planning brief completed. [Synthetic fixture]")
                        if not tools:
                            return response(tool="mutate_file", arguments={
                                "path": "planning-brief.md", "expected_digest": None, "content": PLANNING_TEXT})
                        if len(tools) == 1:
                            return response(tool="work", arguments={"op": "report", "kind": "result",
                                "note": "The revised planning brief is ready.", "artifact_path": "planning-brief.md"})
                        return response(content="Planning brief completed. [Synthetic fixture]")
                    if orientation == REVISION_TASK:
                        return response(content="Revision accepted. [Synthetic fixture]")
                    if "WRITE_RESUME" in orientation and not tools:
                        return response(tool="mutate_file", arguments={
                            "path": "retained.txt", "expected_digest": None, "content": RETAINED_TEXT})
                return response(content="Ready to continue. [Synthetic fixture]")
            if scope.context_id.startswith("work:"):
                if not tools:
                    return response(tool="mutate_file", arguments={
                        "path": "practice.txt", "expected_digest": None, "content": PRACTICE_TEXT})
                if len(tools) == 1:
                    return response(tool="work", arguments={"op": "report", "kind": "result",
                        "note": "The practice is saved in practice.txt. [Synthetic model fixture]",
                        **({"artifact_path": "practice.txt"} if self.long_boundary else {})})
                return response(content="The practice is saved in practice.txt. [Synthetic model fixture]")
            if FIRST_DEPARTURE_ORIENTATION in orientation and not tools:
                return response(tool="work", arguments={"op": "create", "title": PRACTICE_TITLE,
                                                         "instruction": PRACTICE_INSTRUCTION})
            if (orientation == "" or continuation_boundary > user_boundary
                    or PROVIDER_CONTINUATION_ORIENTATION in orientation):
                if not tools:
                    return response(tool="work", arguments={"op": "list"})
                if len(tools) == 1:
                    return response(tool="work", arguments={"op": "create", "title": PRACTICE_TITLE,
                                                             "instruction": PRACTICE_INSTRUCTION})
            if tools:
                return response(content="I opened a focused practice Work. [Synthetic model fixture]")
            return response(content="The practice result is ready to continue. [Synthetic model fixture]")

        def diagnosed_handler(request):
            try:
                return handler(request)
            except Exception:
                # Synthetic traffic only. Keep the first actual fixture error in CI
                # even if the transport adapter converts it to a stopped turn.
                traceback.print_exc()
                raise

        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "synthetic-autonomous-practice",
            credential_env="PER_PULSE_AUTONOMOUS_FIXTURE_KEY", max_retries=0,
        ), transport=httpx.MockTransport(diagnosed_handler))
