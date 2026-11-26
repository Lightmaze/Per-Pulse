"""Deliver a sourced file while a different Work is in a live Pulse turn.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The model transport is explicit synthetic HTTP; the live service, World,
Kernel, files, source delivery, budgets and restart are the product.
"""
from __future__ import annotations

import json
import os
import threading
import time

from fastapi.testclient import TestClient
import httpx

from material_fixture import MaterialFixture
from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from test_pulse_integration import client_for, eventually, page, view


def test_long_foreground_delivery_during_an_active_background_pulse(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_ACTIVE_WORK_FIXTURE_KEY", "explicit-synthetic-credential")
    blocked = threading.Event()
    release = threading.Event()
    second_blocked = threading.Event()
    second_release = threading.Event()
    lock = threading.Lock()
    background_requests = 0
    reading_context = None
    observed_requests = []
    original = {}
    target_outcomes = []
    follow_on_outcomes = []
    follow_on_source = {}
    count = int(os.environ.get("PER_PULSE_ACTIVE_WORK_TURNS", "12"))
    assert count >= 2

    def factory(scope):
        def respond(request):
            nonlocal background_requests, reading_context
            messages = json.loads(request.content)["messages"]
            users = [i for i, item in enumerate(messages) if item["role"] == "user"]
            current = users[-1] if users else None
            task = str(messages[current]["content"]) if current is not None else ""
            # A content-free reentry need not repeat the original user task
            # as its final prompt. Route the synthetic transport by the
            # native context that actually made the initial reading request.
            with lock:
                observed_requests.append((scope.context_id, scope.turn_id))
                if "ACTIVE_BACKGROUND_READING" in task and reading_context is None:
                    reading_context = scope.context_id
                reading = reading_context is not None and scope.context_id == reading_context
                if reading:
                    background_requests += 1
                    ordinal = background_requests
            if reading:
                if ordinal == 2:
                    blocked.set()
                    if not release.wait(timeout=300):
                        return httpx.Response(504, text="Synthetic background timeout")
                if ordinal == 3:
                    second_blocked.set()
                    if not second_release.wait(timeout=300):
                        return httpx.Response(504, text="Synthetic second background timeout")
                return MaterialFixture.response(f"BACKGROUND_DONE_{ordinal}")
            if current is None:
                return MaterialFixture.response("Home report received.")
            results = [json.loads(item["content"]) for item in messages[current + 1:]
                       if item["role"] == "tool"]
            if "ACTIVE_SECOND_DELIVER" in task:
                source_text = results[1]["output"]["content"] if len(results) >= 2 else ""
                plan = [
                    ("work", {"op": "inspect", "work_id": follow_on_source["work"], "include_files": True}),
                    ("read_file", {"path": "active-delivered.md",
                                   "expected_digest": follow_on_source["sha256"]}),
                    ("mutate_file", {"path": "active-follow-on.md", "expected_digest": None,
                                     "content": "# Follow-on\n" + source_text}),
                    ("work", {"op": "report", "kind": "result", "note": "Completed follow-on delivery.",
                              "artifact_path": "active-follow-on.md",
                              "input_file_work_id": follow_on_source["work"],
                              "input_file_artifact_id": follow_on_source["id"]}),
                ]
                if len(results) == len(plan):
                    follow_on_outcomes.extend(results)
                    return MaterialFixture.response("SECOND_TARGET_DONE")
            elif "ACTIVE_TARGET_DELIVER" in task:
                plan = [
                    ("work", {"op": "inspect", "work_id": original["work"], "include_files": True}),
                    ("read_file", {"path": "active-approved.md", "expected_digest": original["sha256"]}),
                    ("mutate_file", {"path": "active-delivered.md", "expected_digest": None,
                                     "content": "# Delivered\nVerified evidence from the approved version.\n"}),
                    ("work", {"op": "report", "kind": "result", "note": "Completed sourced delivery.",
                              "artifact_path": "active-delivered.md", "input_file_work_id": original["work"],
                              "input_file_artifact_id": original["id"]}),
                ]
                if len(results) >= 2:
                    assert "Approved evidence" in results[1]["output"]["content"]
                if len(results) == len(plan):
                    target_outcomes.extend(results)
                    return MaterialFixture.response("TARGET_DONE")
            elif "ACTIVE_SOURCE_CREATE" in task:
                plan = [
                    ("mutate_file", {"path": "active-draft.md", "expected_digest": None,
                                     "content": "Unverified draft.\n"}),
                    ("mutate_file", {"path": "active-approved.md", "expected_digest": None,
                                     "content": "Approved evidence.\n"}),
                ]
                if len(results) == len(plan):
                    return MaterialFixture.response("SOURCE_DONE")
            else:
                return MaterialFixture.response("FOREGROUND_DONE: " + task)
            name, arguments = plan[len(results)]
            return MaterialFixture.response(tool=name, arguments=arguments, sequence=len(results))

        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "active-work-fixture",
                           credential_env="PER_PULSE_ACTIVE_WORK_FIXTURE_KEY", max_retries=0),
            transport=httpx.MockTransport(respond),
        )

    settings = PulseSettings(
        enabled=True, spontaneous_rate=1, check_interval_seconds=0.02,
        refractory_seconds=0.05, max_background_pulses_per_hour=2,
        max_propagation_depth=0,
    )
    options = dict(pulse=settings, model_factory=factory, posture=False,
                   allow_workspace_actions=True, auto_apply_edits=True)

    def reply(client, work, text):
        return eventually(
            lambda: view(client, work),
            lambda state: state["page"]["status"] == "idle" and any(
                message["role"] == "assistant" and text in message["content"]
                for message in state["messages"]),
            timeout=12,
        )

    def send(client, work, content):
        response = client.post(f"/api/pages/{work}/messages", json={"content": content})
        assert response.status_code == 202, response.text
        return reply(client, work, "FOREGROUND_DONE: " + content)

    def pulse_state(client):
        response = client.get("/api/pulse")
        assert response.status_code == 200, response.text
        return response.json()

    def weekly(client):
        return client.get("/api/pulse/weekly-usage").json()["usage"]

    def pause(client, paused):
        response = client.patch("/api/pulse", json={"paused": paused})
        assert response.status_code == 200, response.text

    def hold(client, work):
        response = client.patch(f"/api/pulse/contexts/{work}", json={"paused": True})
        assert response.status_code == 200, response.text

    def prepare_work(client, title, source, message):
        created = client.post("/api/pages", json={
            "title": title, "instruction": "Prepare this Work for a sourced deliverable.",
            "sourcePageId": source, "sourceMessageId": message,
        })
        assert created.status_code == 201, created.text
        work = created.json()["page"]["id"]
        reply(client, work, "FOREGROUND_DONE: Prepare this Work")
        hold(client, work)
        return work

    with client_for(tmp_path, **options) as client:
        runtime = client.app.state.pages.runtime
        subject = runtime.continuity_engram_id
        world = runtime.world_id
        pause(client, True)
        hold(client, "home")
        created = client.post("/api/pages", json={
            "title": "Evidence versions", "instruction": "ACTIVE_SOURCE_CREATE",
        })
        assert created.status_code == 201, created.text
        source = created.json()["page"]["id"]
        source_message = created.json()["initialMessageId"]
        reply(client, source, "SOURCE_DONE")
        artifacts = client.get(f"/api/pages/{source}/artifacts").json()["artifacts"]
        assert {item["path"] for item in artifacts} == {"active-draft.md", "active-approved.md"}
        approved = next(item for item in artifacts if item["path"] == "active-approved.md")
        original.update(work=source, id=approved["id"], sha256=approved["sha256"])
        background = page(client, "Separate reading Work", "ACTIVE_BACKGROUND_READING")
        reply(client, background, "BACKGROUND_DONE_1")
        assert background_requests == 1 and reading_context is not None
        hold(client, source)
        # Hold only background activity in these Works; foreground input
        # remains available. Prepare before resuming to avoid admission races.
        destination = prepare_work(client, "Sourced deliverable", source, source_message)
        # Let only the reading context enter background admission. Its
        # provider remains blocked throughout the foreground file delivery.
        time.sleep(0.10)
        pause(client, False)
        try:
            assert blocked.wait(timeout=12), (
                f"Reading did not enter its background transport; requests={observed_requests!r}, "
                f"activity={pulse_state(client)!r}"
            )
            assert pulse_state(client)["budget"]["used"] == 1
            admitted = runtime.causal_ledger.list_events(kind="spontaneous", limit=20)
            assert len(admitted) == 1
            assert admitted[0].center_id == runtime.world.get_task_front(background).center_id
            assert admitted[0].status.value == "running"
            started = time.perf_counter()
            for index in range(count):
                send(client, source, f"Review revision {index + 1}.")
            sent = client.post(f"/api/pages/{destination}/messages",
                               json={"content": "ACTIVE_TARGET_DELIVER"})
            assert sent.status_code == 202, sent.text
            reply(client, destination, "TARGET_DONE")
            assert [item["status"] for item in target_outcomes] == ["succeeded"] * 4
            assert {item["path"] for item in target_outcomes[0]["output"]["completed_files"]} == {
                "active-draft.md", "active-approved.md",
            }
            output, = client.get(f"/api/pages/{destination}/artifacts").json()["artifacts"]
            home = eventually(lambda: view(client, "home"), lambda state: any(
                (item.get("reportedArtifact") or {}).get("artifactId") == output["id"]
                for item in state["messages"] if item.get("reportedArtifact")), timeout=12)
            report = next(item for item in home["messages"] if item.get("reportedArtifact"))
            assert report["inputArtifact"]["artifactId"] == approved["id"]
            assert report["sourcePageId"] == destination
            destination_view = view(client, destination)
            assert destination_view["page"]["sourceMessageId"] == source_message
            destination_message = next(item["id"] for item in destination_view["messages"]
                                       if item["role"] == "assistant" and item["content"] == "TARGET_DONE")
            follow_on_source.update(work=destination, id=output["id"], sha256=output["sha256"])
            response = client.get(f"/api/pages/{destination}/artifacts/{output['id']}/download")
            assert response.status_code == 200
            assert response.content == b"# Delivered\nVerified evidence from the approved version.\n"
            assert not release.is_set()
            assert background_requests == 2
            assert runtime.causal_ledger.get_event(admitted[0].id).status.value == "running"
            assert len(runtime.storage.list_engrams()) == 1
            assert runtime.world.get_task_front(destination).focal_engram_id == subject
            print(f"Active Pulse: {count} foreground follow-ups and sourced file delivered "
                  f"in {time.perf_counter() - started:.3f}s before background release.", flush=True)
            pause(client, True)
        finally:
            release.set()
        reply(client, background, "BACKGROUND_DONE_2")
        impulses = runtime.causal_ledger.list_events(kind="spontaneous", limit=20)
        assert len(impulses) == 1 and impulses[0].status.value == "settled"
        assert impulses[0].center_id == runtime.world.get_task_front(background).center_id
        impulse_id = impulses[0].id
        saved_usage = weekly(client)
        assert saved_usage["enabled"] and saved_usage["consumed"] >= 1

    # Do not use client_for here: it would re-enable the weekly allowance
    # and hide a failure to restore the saved user policy.
    with TestClient(create_app(tmp_path, **options), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        assert (runtime.world_id, runtime.continuity_engram_id) == (world, subject)
        state = pulse_state(client)
        assert state["paused"] and state["budget"]["remaining"] == 1
        assert weekly(client)["consumed"] == saved_usage["consumed"]
        assert weekly(client)["enabled"] == saved_usage["enabled"]
        assert weekly(client)["weekly_limit"] == saved_usage["weekly_limit"]
        assert runtime.causal_ledger.get_event(impulse_id).status.value == "settled"
        assert client.get(f"/api/pages/{destination}/artifacts/{output['id']}/download").content == (
            b"# Delivered\nVerified evidence from the approved version.\n")
        assert client.get(f"/api/pages/{source}/artifacts/{approved['id']}/download").content == b"Approved evidence.\n"
        send(client, destination, "Continue after cold restart while paused.")
        third = prepare_work(client, "Follow-on from prior delivery", destination, destination_message)
        assert background_requests == 2
        assert {item.id for item in runtime.causal_ledger.list_events(kind="spontaneous", limit=20)} == {impulse_id}
        pause(client, False)
        try:
            assert second_blocked.wait(timeout=12), (
                f"Reading did not reenter after restart; requests={observed_requests!r}, "
                f"activity={pulse_state(client)!r}"
            )
            events = runtime.causal_ledger.list_events(kind="spontaneous", limit=20)
            assert len(events) == 2
            second = next(item for item in events if item.id != impulse_id)
            assert second.status.value == "running"
            assert second.center_id == runtime.world.get_task_front(background).center_id
            parent = runtime.causal_ledger.get_event(second.parent_event_id)
            assert parent.kind.value == "assistant_result"
            assert parent.parent_event_id == impulse_id
            assert parent.engram_id == second.engram_id == subject
            assert pulse_state(client)["budget"]["remaining"] == 0
            started = time.perf_counter()
            for index in range(min(count, 12)):
                send(client, third, f"Continue follow-on revision {index + 1}.")
            sent = client.post(f"/api/pages/{third}/messages", json={"content": "ACTIVE_SECOND_DELIVER"})
            assert sent.status_code == 202, sent.text
            reply(client, third, "SECOND_TARGET_DONE")
            assert [item["status"] for item in follow_on_outcomes] == ["succeeded"] * 4
            assert any(item["path"] == "active-delivered.md"
                       for item in follow_on_outcomes[0]["output"]["completed_files"])
            follow_on, = client.get(f"/api/pages/{third}/artifacts").json()["artifacts"]
            home = eventually(lambda: view(client, "home"), lambda state: any(
                (item.get("reportedArtifact") or {}).get("artifactId") == follow_on["id"]
                for item in state["messages"] if item.get("reportedArtifact")), timeout=12)
            report = next(item for item in home["messages"]
                          if (item.get("reportedArtifact") or {}).get("artifactId") == follow_on["id"])
            assert report["inputArtifact"]["artifactId"] == output["id"]
            assert report["sourcePageId"] == third
            assert view(client, third)["page"]["sourceMessageId"] == destination_message
            assert client.get(f"/api/pages/{third}/artifacts/{follow_on['id']}/download").content == (
                b"# Follow-on\n# Delivered\nVerified evidence from the approved version.\n")
            assert not second_release.is_set()
            assert background_requests == 3
            assert runtime.causal_ledger.get_event(second.id).status.value == "running"
            assert len(runtime.storage.list_engrams()) == 1
            assert runtime.world.get_task_front(third).focal_engram_id == subject
            print(f"Second Pulse: follow-on file delivered in {time.perf_counter() - started:.3f}s "
                  "before background release, after cold restart.", flush=True)
            pause(client, True)
        finally:
            second_release.set()
        reply(client, background, "BACKGROUND_DONE_3")
        eventually(lambda: runtime.causal_ledger.get_event(second.id),
                   lambda row: row.status.value == "settled")
        final_usage = weekly(client)
        assert final_usage["consumed"] >= saved_usage["consumed"]

    with TestClient(create_app(tmp_path, **options), base_url="http://localhost") as client:
        runtime = client.app.state.pages.runtime
        assert (runtime.world_id, runtime.continuity_engram_id) == (world, subject)
        assert pulse_state(client)["paused"]
        assert pulse_state(client)["budget"]["remaining"] == 0
        assert weekly(client)["consumed"] == final_usage["consumed"]
        assert weekly(client)["weekly_limit"] == saved_usage["weekly_limit"]
        pause(client, False)
        send(client, third, "Continue with exhausted background allowance.")
        time.sleep(0.15)
        assert background_requests == 3
        assert pulse_state(client)["budget"]["remaining"] == 0
        events = runtime.causal_ledger.list_events(kind="spontaneous", limit=20)
        assert {item.id for item in events} == {impulse_id, second.id}
        assert all(item.status.value == "settled" for item in events)
        assert client.get(f"/api/pages/{third}/artifacts/{follow_on['id']}/download").content == (
            b"# Follow-on\n# Delivered\nVerified evidence from the approved version.\n")
        undone = client.post(f"/api/pages/{third}/artifacts/{follow_on['id']}/undo", json={})
        assert undone.status_code == 200, undone.text
        home = view(client, "home")
        report = next(item for item in home["messages"]
                      if (item.get("reportedArtifact") or {}).get("artifactId") == follow_on["id"])
        assert report["reportedArtifact"]["undone"] is True
        assert report["inputArtifact"]["undone"] is False
        assert client.get(f"/api/pages/{destination}/artifacts/{output['id']}/download").content == (
            b"# Delivered\nVerified evidence from the approved version.\n")
        assert client.get(f"/api/pages/{source}/artifacts/{approved['id']}/download").content == b"Approved evidence.\n"
