"""Retain actual quiet-Pulse work through the existing experience journal.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Provider decisions are explicit HTTP fixtures; World, Kernel and reads are real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from material_fixture import MaterialFixture
from per_pulse.service import PageService, PulseSettings
from pulse_harness.core.errors import ScopeConflict
from pulse_system.agent.harness.base import HarnessError
from pulse_system.service.native_posture import capture_example


class ExampleFixture(MaterialFixture):
    def __init__(self, setenv):
        super().__init__(setenv)
        self.mode = "answer"
        self.answer = "A concrete result from the actual fixture turn."
        self.source = None
        self.outcomes = []
        self.experience_id = None
        self.before_action = []
        self.primary_requests = []
        self.finish = "succeed"
        self.cancel = None

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        material = json.dumps(messages)
        if "WORK_MATERIAL_JSON" in material:
            packet = json.loads(messages[-1]["content"].split("WORK_MATERIAL_JSON\n", 1)[1])
            if self.mode == "reuse" and "candidate_response" not in packet:
                self.before_action.append(packet)
                offered = next(item for item in packet["posture_experiences"]["episodes"]
                               if item["id"] == self.experience_id)
                return self.response(f"Use the retained example {offered['id']} revision {offered['revision']}: "
                                     + offered["excerpt"])
            return self.response("PASS")
        if "UNTRUSTED_REVIEW_DATA_JSON" in material:
            return self.response('{"mode":"none"}')
        current = max(i for i, item in enumerate(messages) if item["role"] == "user")
        results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
        if self.mode == "remember":
            if not results:
                return self.response(tool="posture", arguments={
                    "op": "remember_example", "source_message_id": self.source.id,
                    "title": "A retained local result", "excerpt": self.source.content,
                    "note": "Keep this exact example available for later work.",
                })
            self.outcomes.append(results[-1])
            if self.finish == "fail":
                return httpx.Response(400)
            if self.finish == "cancel":
                self.cancel()
            return self.response("The example selection is complete. [Explicit synthetic fixture]")
        if self.mode == "reuse":
            self.primary_requests.append(body)
            if not results:
                return self.response(tool="posture", arguments={"op": "get", "experience_id": self.experience_id})
            self.outcomes.append(results[-1])
            return self.response("The retained example is available for this continuation. [Explicit synthetic fixture]")
        return self.response(self.answer)


@pytest.fixture
def local(tmp_path, monkeypatch):
    fixture = ExampleFixture(monkeypatch.setenv)
    monkeypatch.setattr("pulse_system.core.pulse.context.random.random", lambda: 0.0)
    pages = open_pages(tmp_path, fixture)
    try:
        yield pages, fixture
    finally:
        pages.runtime.close()


def open_pages(path, fixture):
    return PageService(path, posture=True, model_factory=fixture.factory, pulse=PulseSettings(
        enabled=True, spontaneous_rate=1, refractory_seconds=0, check_interval_seconds=0.001,
        adaptive_connections=False, spectrum_enabled=False, max_background_pulses_per_hour=12,
    ))


def run_event(pages, event):
    runtime = pages.runtime
    context = runtime._native_kernel_context_for_center(event.center_id) or "home"
    result = runtime._mgr.pulse(runtime.continuity_engram_id, pulse_event=event,
                                execution_context=context, runtime_fence=runtime._current_runtime_fence())
    return runtime.causal_ledger.get_event(result.result_event_id)


def user_result(pages, *, page_id="home"):
    sent = pages.send(page_id, "Work on a concrete example for this context.")
    return run_event(pages, pages.runtime.causal_ledger.get_event(sent["messageId"]))


def next_quiet(pages, *, center_id=None):
    runtime = pages.runtime
    runtime.context_pulse.tick(now=datetime.now(timezone.utc) + timedelta(seconds=1))
    queued = runtime.causal_ledger.list_events(world_id=runtime.world_id, kind="spontaneous",
                                               status="queued", limit=100)
    event, = [item for item in queued if item.center_id == center_id]
    assert event.content is None and event.metadata["context_pulse"] == "reentry"
    return event


@pytest.mark.parametrize("autonomous_source", [False, True], ids=["earlier-user-result", "earlier-pulse-result"])
def test_quiet_pulse_can_retain_a_real_example_without_user_feedback(local, autonomous_source):
    pages, fixture = local
    source = user_result(pages)
    if autonomous_source:
        fixture.answer = "A further concrete result produced without new input text."
        source = run_event(pages, next_quiet(pages))
    fixture.mode, fixture.source = "remember", source
    trigger = next_quiet(pages)
    run_event(pages, trigger)
    assert fixture.outcomes[-1]["status"] == "succeeded", fixture.outcomes[-1]
    pages.runtime._native_posture.drain()
    record, = pages.runtime.harness.posture_memory.list_experiences(
        subject_id=pages.runtime.continuity_engram_id)
    assert record["excerpt"] == source.content
    assert record["source"]["source_message_id"] == source.id
    assert record["source"]["feedback_evidence_class"] == "not_attached"
    assert record["feedback"] == [] and "outcome" not in record["encounter"]
    assert record["source"]["learning_trigger"]["event_id"] == trigger.id
    assert record["source"]["learning_trigger"]["input_text"] is None
    if autonomous_source:
        assert record["encounter"]["situation"] == "Internal continuation without new input text."
        activation = record["source"]["situation_source"]
        assert activation["event_id"] == source.parent_event_id
        assert activation["kind"] == "spontaneous" and activation["source"] == "self"
        assert activation["context_pulse"] == "reentry" and activation["input_text"] is None
        turn = pages.runtime.causal_ledger.get_turn_for_world(activation["turn_id"], pages.runtime.world_id)
        assert turn.event_id == activation["event_id"] and turn.result_event_id == source.id
        assert turn.prompt_accepted is True


def test_operator_can_save_an_actual_autonomous_result(local):
    pages, fixture = local
    user_result(pages)
    fixture.answer = "An autonomous result selected by the operator."
    source = run_event(pages, next_quiet(pages))
    result = pages.learning.create_example("home", {
        "sourceMessageId": source.id, "title": "Keep the autonomous result", "excerpt": source.content,
    })
    record = result["experience"]
    assert record["excerpt"] == source.content
    assert record["sourceAnchor"] == {"resultMessageId": source.id}
    assert record["source"]["feedback_evidence_class"] == "not_attached"
    assert "outcome" not in record["encounter"]


def test_autonomous_example_survives_restart_and_enters_the_next_pulses_first_decision(local, tmp_path):
    pages, fixture = local
    user_result(pages)
    fixture.answer = "A concrete autonomous result that can be reused after restart."
    source = run_event(pages, next_quiet(pages))
    fixture.mode, fixture.source = "remember", source
    run_event(pages, next_quiet(pages))
    assert fixture.outcomes[-1]["status"] == "succeeded"
    pages.runtime._native_posture.drain()
    record, = pages.runtime.harness.posture_memory.list_experiences(subject_id=pages.runtime.continuity_engram_id)
    pages.runtime.close()

    fixture.mode, fixture.experience_id = "reuse", record["id"]
    reopened = open_pages(tmp_path, fixture)
    try:
        saved, = reopened.learning.view("home")["experiences"]
        assert saved["source"] == record["source"]
        assert saved["sourceAnchor"] == {"resultMessageId": source.id}
        assert saved["encounter"] == record["encounter"]
        run_event(reopened, next_quiet(reopened))
        first = fixture.before_action[0]
        assert first["native_model_round"] == 0
        offered, = first["posture_experiences"]["episodes"]
        assert (offered["id"], offered["revision"], offered["excerpt"]) == (
            record["id"], record["revision"], source.content)
        assert first["posture_experiences"]["offered_posture_revisions"] == [
            {"id": record["id"], "revision": record["revision"]}]
        first_primary = json.dumps(fixture.primary_requests[0]["messages"])
        assert "POSTURE_WORK_CONTRIBUTION" in first_primary
        assert record["id"] in first_primary
        assert fixture.outcomes[-1]["status"] == "succeeded"
        assert fixture.outcomes[-1]["output"]["experience"]["id"] == record["id"]
        assert reopened.runtime.harness.posture_memory.get_experience(record["id"])["feedback"] == []
    finally:
        reopened.runtime.close()


def test_autonomous_result_can_keep_a_later_actual_user_followup(local):
    pages, fixture = local
    user_result(pages)
    source = run_event(pages, next_quiet(pages))
    followup = pages.send("home", "The concrete result helped me identify the missing constraint.")
    event = pages.runtime.causal_ledger.get_event(followup["messageId"])
    run_event(pages, event)
    saved = pages.learning.create_example("home", {
        "sourceMessageId": source.id, "title": "An example with a real follow-up",
        "excerpt": source.content, "feedbackMessageId": event.id,
    })["experience"]
    assert saved["encounter"]["outcome"] == event.content
    assert saved["source"]["feedback_message_id"] == event.id
    assert saved["source"]["situation_source"]["event_id"] == source.parent_event_id
    assert saved["feedback"] == []


@pytest.mark.parametrize("finish", ["fail", "cancel"])
def test_an_unsuccessful_quiet_capture_turn_does_not_admit_an_example(local, finish):
    pages, fixture = local
    source = user_result(pages)
    fixture.mode, fixture.source, fixture.finish = "remember", source, finish
    trigger = next_quiet(pages)
    if finish == "cancel":
        def cancel():
            running = pages.runtime.causal_ledger.get_running_turn_for_context(
                pages.runtime.continuity_engram_id, "home")
            pages.runtime.harness.abort(pages.runtime.continuity_engram_id, turn_id=running.id)
        fixture.cancel = cancel
    with pytest.raises(HarnessError):
        run_event(pages, trigger)
    assert fixture.outcomes[-1]["status"] == "succeeded"  # The selection action was staged.
    pages.runtime._native_posture.drain()
    assert pages.runtime.harness.posture_memory.list_experiences(
        subject_id=pages.runtime.continuity_engram_id) == []
    request, = pages.runtime._native_posture._requests()
    assert request["status"] == "cancelled"


def test_quiet_capture_cannot_borrow_another_works_result(local):
    pages, fixture = local
    user_result(pages)
    created = pages.create_page("Separate Work", "Keep its source local.")
    source = run_event(pages, pages.runtime.causal_ledger.get_event(created["initialMessageId"]))
    fixture.mode, fixture.source = "remember", source
    run_event(pages, next_quiet(pages))
    assert fixture.outcomes[-1]["status"] == "failed"
    pages.runtime._native_posture.drain()
    assert pages.runtime.harness.posture_memory.list_experiences(
        subject_id=pages.runtime.continuity_engram_id) == []


@pytest.mark.parametrize("fault", ["unaccepted", "parent-mismatch", "empty-user-input"])
def test_empty_input_support_keeps_original_source_checks(local, fault):
    pages, _fixture = local
    source = user_result(pages)
    if fault != "empty-user-input":
        source = run_event(pages, next_quiet(pages))
    runtime = pages.runtime
    with runtime.storage._lock:
        if fault == "unaccepted":
            runtime.storage._conn.execute("UPDATE harness_turns SET prompt_accepted=0 WHERE result_event_id=?",
                                          (source.id,))
        elif fault == "parent-mismatch":
            runtime.storage._conn.execute("UPDATE causal_events SET parent_event_id=NULL WHERE id=?", (source.id,))
        else:
            runtime.storage._conn.execute("UPDATE causal_events SET content=NULL WHERE id=?", (source.parent_event_id,))
    with pytest.raises(ScopeConflict):
        capture_example(runtime, runtime.continuity_engram_id, "", source.id, source.content)


def test_a_contentless_event_without_an_active_kernel_turn_is_not_a_capture_trigger(local):
    pages, _fixture = local
    source = user_result(pages)
    trigger = next_quiet(pages)  # Still queued: it has never been accepted by Kernel.
    with pytest.raises(ScopeConflict, match="unique native turn"):
        capture_example(pages.runtime, pages.runtime.continuity_engram_id, "", source.id,
                        source.content, latest_input_id=trigger.id)
