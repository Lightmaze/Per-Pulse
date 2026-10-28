"""End-to-end native Work experience: correction -> later choice -> approved file -> sourced Home return.

The model transport is an explicit scripted fixture. Actual SQLite World/Kernel,
posture projections, action approvals, file writes, and page routing run normally.
This does not measure real-model learning or decision quality.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import time

import httpx
from fastapi.testclient import TestClient
import pytest

from per_pulse.app import create_app
from per_pulse.artifacts import PageArtifacts
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


LESSON = "Let visitors try the central experience before optional setup."
CORRECTION = "The intake form hides the product. Let me try its central interaction before optional profile questions."
EXPERIENCE = "A playable first interaction.\n"
INTAKE = "Twelve-field intake form.\n"


def fixture_factory(monkeypatch, requests):
    monkeypatch.setenv("PER_PULSE_FULL_JOURNEY_FIXTURE_KEY", "synthetic-only")

    def handler(request):
        body = json.loads(request.content)
        messages = body["messages"]
        requests.append(body)
        last_user = max(i for i, message in enumerate(messages) if message["role"] == "user")
        current = messages[last_user:]
        task = json.dumps(current, ensure_ascii=False)
        model_view = json.dumps(messages, ensure_ascii=False)
        if "WORK_MATERIAL_JSON" in model_view:
            # Native posture uses a separate no-tools semantic computation.
            # A saved experience is NOT spliced directly into the main turn:
            # this fixture must actually issue a contribution before the
            # next main action can change. Delivery review gets PASS.
            if LESSON in model_view and '"candidate_response"' not in model_view:
                response = "For the visitor's immediate experience: " + LESSON
            else:
                response = "PASS"
            delta, finish = {"content": response}, "stop"
        elif "BUILD_PRACTICE_ARTIFACT" in task and not any(m["role"] == "tool" for m in current):
            learned = LESSON in model_view
            arguments = {"path": "practice.txt" if learned else "intake.txt",
                         "expected_digest": None, "content": EXPERIENCE if learned else INTAKE}
            delta = {"tool_calls": [{"index": 0, "id": "fixture-artifact", "type": "function",
                                    "function": {"name": "mutate_file", "arguments": json.dumps(arguments)}}]}
            finish = "tool_calls"
        else:
            response = ("The first screen is a twelve-field intake form. [Explicit synthetic fixture]"
                        if "DESIGN_FIRST_VIEW" in task else
                        "I received the correction. [Explicit synthetic fixture]"
                        if CORRECTION in task else
                        "The selected file task finished. [Explicit synthetic fixture]")
            delta, finish = {"content": response}, "stop"
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "synthetic-journey",
                           credential_env="PER_PULSE_FULL_JOURNEY_FIXTURE_KEY"),
            transport=httpx.MockTransport(handler),
        )

    return factory


def create_work(client, title, instruction):
    response = client.post("/api/pages", json={"title": title, "instruction": instruction})
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def await_view(client, path, predicate, label):
    deadline = time.monotonic() + 12
    latest = None
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        latest = response.json()
        if predicate(latest):
            return latest
        time.sleep(0.025)
    pytest.fail(f"Timed out waiting for {label}: {latest}")


def test_original_correction_changes_later_fixture_action_and_returns_sourced_result(tmp_path, monkeypatch):
    requests = []
    provider = fixture_factory(monkeypatch, requests)
    with TestClient(create_app(tmp_path, posture=True, allow_workspace_actions=True,
                               model_factory=provider), base_url="http://localhost") as client:
        pages = client.app.state.pages
        execution = PageExecution(pages.runtime)
        artifacts = PageArtifacts(pages.runtime)
        home_before = client.get("/api/pages/home").json()["messages"]

        source_page = create_work(client, "Original product brief", "DESIGN_FIRST_VIEW")
        source_view = await_view(client, f"/api/pages/{source_page}",
                                 lambda v: any(m["role"] == "assistant" for m in v["messages"]), "initial outcome")
        source_result = next(m for m in source_view["messages"] if m["role"] == "assistant")
        assert "twelve-field intake" in source_result["content"]
        correction = client.post(f"/api/pages/{source_page}/messages", json={"content": CORRECTION})
        assert correction.status_code == 202, correction.text
        await_view(client, f"/api/pages/{source_page}",
                   lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 2, "correction settled")
        created = client.post(f"/api/pages/{source_page}/posture/experiences", json={
            "sourceMessageId": source_result["id"],
            "feedbackMessageId": correction.json()["messageId"],
            "title": "First usable interaction",
            "lesson": LESSON,
            "conditions": {"all": ["A visitor asks for an exploratory experience",
                                    "The central experience can be tried immediately"]},
            "counterexamples": ["The user explicitly needs a fixed registration form."],
            "shift": {"understanding": "Identify the first useful interaction.",
                      "candidates": "Prefer a playable slice to an optional intake form.",
                      "delivery": "Verify the visitor can try it."},
            "scope": "context",
        })
        assert created.status_code == 201, created.text
        record = created.json()["experience"]
        assert record["sourcePageId"] == source_page and record["revision"] == 1
        assert record["source"]["capture_kind"] == "operator_selected_correction"

        # Before sharing, an unrelated Work cannot access the source correction.
        baseline = create_work(client, "Practice delivery", "BUILD_PRACTICE_ARTIFACT")
        # A pending action may be visible before its owning turn finishes the
        # WAITING_APPROVAL transition. UI controls honor canResolve; this
        # native API journey must wait for the same actionable state rather
        # than racing the exact-turn approval guard.
        baseline_pending = await_view(client, f"/api/pages/{baseline}/execution",
                                       lambda x: any(a["canResolve"] for a in x["pendingActions"]),
                                       "baseline actionable approval")
        baseline_action = next(a for a in baseline_pending["pendingActions"] if a["canResolve"])
        assert baseline_action["turnId"] == baseline_pending["turnId"]
        assert baseline_action["arguments"]["path"] == "intake.txt"
        assert client.get(f"/api/pages/{baseline}/posture").json()["experiences"] == []
        assert not (tmp_path / "workspace" / "intake.txt").exists()
        denied = client.post(
            f"/api/pages/{baseline}/execution/actions/{baseline_action['actionId']}",
            json={"decision": "deny"})
        assert denied.status_code == 200, denied.text
        assert not (tmp_path / "workspace" / "intake.txt").exists()

        shared = client.patch(f"/api/pages/{source_page}/posture/experiences/{record['id']}", json={
            "expectedRevision": 1, "changes": {"scope": "subject"}})
        assert shared.status_code == 200, shared.text
        assert shared.json()["experience"]["revision"] == 2

        work = create_work(client, "Practice delivery", "BUILD_PRACTICE_ARTIFACT")
        exposed = client.get(f"/api/pages/{work}/posture").json()["experiences"]
        assert len(exposed) == 1 and exposed[0]["id"] == record["id"]
        assert not exposed[0]["canEdit"] and "encounter" not in exposed[0]
        approved_pending = await_view(client, f"/api/pages/{work}/execution",
                                      lambda x: any(a["canResolve"] for a in x["pendingActions"]),
                                      "learned-choice actionable approval")
        action = next(a for a in approved_pending["pendingActions"] if a["canResolve"])
        assert action["turnId"] == approved_pending["turnId"]
        assert action["arguments"]["path"] == "practice.txt"
        assert action["arguments"]["content"] == EXPERIENCE
        assert not (tmp_path / "workspace" / "practice.txt").exists()

        # Inspect actual provider inputs: shared abstraction enters the new Work,
        # while private original outcome and correction do not.
        task_requests = [x for x in requests if "BUILD_PRACTICE_ARTIFACT" in
                         json.dumps(x["messages"], ensure_ascii=False) and
                         not any(m["role"] == "tool" for m in x["messages"])]
        assert len(task_requests) >= 2
        assert LESSON not in json.dumps(task_requests[0]["messages"], ensure_ascii=False)
        material = json.dumps(task_requests[-1]["messages"], ensure_ascii=False)
        assert LESSON in material and CORRECTION not in material
        assert source_result["content"] not in material

        allowed = client.post(f"/api/pages/{work}/execution/actions/{action['actionId']}",
                              json={"decision": "allow_once"})
        assert allowed.status_code == 200, allowed.text
        await_view(client, f"/api/pages/{work}/execution",
                   lambda x: x["status"] == "idle", "approved execution settled")
        assert (tmp_path / "workspace" / "practice.txt").read_text() == EXPERIENCE
        file_result, = artifacts.snapshot(work)["artifacts"]
        assert file_result["actionId"] == action["actionId"]
        assert artifacts.read(work, action["actionId"])["content"] == EXPERIENCE
        assert artifacts.snapshot(baseline)["artifacts"] == []

        finished = await_view(client, f"/api/pages/{work}",
                              lambda v: any(m["role"] == "assistant" for m in v["messages"]),
                              "post-approval response")
        result_message = [m for m in finished["messages"] if m["role"] == "assistant"][-1]
        returned = client.post(f"/api/pages/{work}/return", json={
            "content": result_message["content"], "messageId": result_message["id"]})
        assert returned.status_code == 201, returned.text
        returned_message = returned.json()["view"]["messages"][-1]
        assert returned_message["sourcePageId"] == work
        assert returned_message["sourceMessageId"] == result_message["id"]
        assert len(returned.json()["view"]["messages"]) == len(home_before) + 1
        assert (client.get(f"/api/pages/{source_page}").json()["messages"][0]["id"]
                == source_view["messages"][0]["id"])

    # World, Kernel, posture journal, file receipts and Home return survive restart.
    with TestClient(create_app(tmp_path, posture=True, allow_workspace_actions=True,
                               model_factory=None), base_url="http://localhost") as client:
        assert client.get("/api/pages/home").json()["messages"][-1]["sourcePageId"] == work
        original = client.get(f"/api/pages/{work}", params={"sourceMessageId": result_message["id"]})
        assert original.status_code == 200
        assert any(m["id"] == result_message["id"] for m in original.json()["messages"])
        assert client.get(f"/api/pages/{work}/posture").json()["experiences"][0]["id"] == record["id"]
        assert PageArtifacts(client.app.state.pages.runtime).read(work, action["actionId"])["content"] == EXPERIENCE
        assert not (tmp_path / "workspace" / "intake.txt").exists()
