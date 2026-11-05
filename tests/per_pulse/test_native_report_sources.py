"""Native Work reports keep their exact result source in the Home page view.

World, Kernel, report admission and persistence are real. Model decisions use
an explicit synthetic HTTP fixture and never contact an external provider.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import httpx
import pytest

from per_pulse.service import PageService
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.service.home_conversation import home_conversation
from pulse_system.service.runtime import ServiceError

NOTE = "The selected finding from this Work. [Explicit synthetic fixture]"
RESULT = "The original detailed Work result. [Explicit synthetic fixture]"


class ReportFixture:
    def __init__(self, monkeypatch):
        monkeypatch.setenv("NATIVE_REPORT_FIXTURE_KEY", "explicit-synthetic-credential")
        self.requests = []

    def factory(self, scope):
        def respond(request):
            body = json.loads(request.content)
            self.requests.append((scope, body))
            messages = body["messages"]
            current = max(i for i, row in enumerate(messages) if row["role"] == "user")
            task = messages[current]["content"]
            results = [json.loads(row["content"]) for row in messages[current + 1:]
                       if row["role"] == "tool"]
            if scope.context_id.startswith("work:") and task == "Select a finding for Home." and not results:
                delta = {"tool_calls": [{"index": 0, "id": "synthetic-report-call", "type": "function",
                    "function": {"name": "work", "arguments": json.dumps(
                        {"op": "report", "kind": "finding", "note": NOTE})}}]}
                reason = "tool_calls"
            else:
                assert all(row["status"] == "succeeded" for row in results), results
                delta = {"content": RESULT if scope.context_id.startswith("work:")
                         else "The Home discussion continues. [Explicit synthetic fixture]"}
                reason = "stop"
            payload = {"choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions",
            "native-report-fixture", credential_env="NATIVE_REPORT_FIXTURE_KEY"),
            transport=httpx.MockTransport(respond))


def activate(pages, page_id, event_id):
    runtime = pages.runtime
    context = pages.execution._scope(page_id).context_id
    return runtime._mgr.pulse(runtime.continuity_engram_id,
        pulse_event=runtime.causal_ledger.get_event(event_id), execution_context=context or "home",
        runtime_fence=runtime._current_runtime_fence())


def reported_work(pages):
    created = pages.create_page("Source Work", "Select a finding for Home.")
    work_id = created["page"]["id"]
    result = activate(pages, work_id, created["initialMessageId"])
    pages.runtime._native_work.drain()
    record, = pages.runtime.storage.native_work_requests(limit=32)
    assert record["arguments"]["op"] == "report" and record["status"] == "delivered"
    return work_id, result, record


def message(pages, event_id):
    return next(row for row in pages.view("home")["messages"] if row["id"] == event_id)


def native_report(pages, record):
    runtime = pages.runtime
    return next(row for row in home_conversation(runtime, runtime.continuity_engram_id)["messages"]
                if row["event_id"] == record["event_id"])["metadata"]["work_report"]


def test_admitted_report_links_the_exact_work_result_without_rewriting_or_references(tmp_path, monkeypatch):
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True)
    try:
        work_id, result, record = reported_work(pages)
        before = json.loads(json.dumps(pages._state))
        request_count = len(fixture.requests)
        report = message(pages, record["event_id"])
        assert report["role"] == "system" and report["activation"] == "work_report"
        assert report["sourcePageId"] == work_id
        assert report["sourceMessageId"] == result.result_event_id
        assert report["content"] == pages.runtime._native_work.report_content(record)
        assert report["references"] == [] and pages._state == before
        assert next(row for row in pages._rows("home") if row["event_id"] == report["id"])["role"] == "user"
        assert len(fixture.requests) == request_count
        assert native_report(pages, record)["source_action_id"] == record["source_action_id"]
        source = pages.view(work_id, source_message_id=report["sourceMessageId"])
        assert next(row for row in source["messages"] if row["id"] == result.result_event_id)["content"] == RESULT
        # The queued report and its ordinary processed Home input share the
        # same source. Reading either state never synthesizes another message.
        activate(pages, "home", record["event_id"])
        assert message(pages, record["event_id"])["sourceMessageId"] == result.result_event_id
        assert message(pages, record["event_id"])["role"] == "system"
        assert native_report(pages, record)["source_turn_id"] == result.turn_id
    finally:
        pages.runtime.close()


def test_report_source_survives_old_windows_history_search_and_offline_restart(tmp_path, monkeypatch):
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True)
    try:
        work_id, result, record = reported_work(pages)
        for index in range(5):
            pages.send(work_id, f"Later private Work input {index}.")
        later = pages.send("home", "A later Home message.")["messageId"]
        monkeypatch.setattr("per_pulse.service.MAX_MESSAGES", 3)
        source = pages.view(work_id, source_message_id=result.result_event_id)
        assert source["earlierSourceMessageId"] == result.result_event_id
        assert source["messages"][0]["content"] == RESULT
        for projection in (pages.history("home", before_message_id=later),
                           pages.search_messages("home", "selected finding")):
            selected = next(row for row in projection["messages"] if row["id"] == record["event_id"])
            assert selected["role"] == "system"
            assert (selected["sourcePageId"], selected["sourceMessageId"]) == (work_id, result.result_event_id)
        pages.runtime.close()
        requests = len(fixture.requests)
        pages = PageService(tmp_path, model_factory=None)
        assert (message(pages, record["event_id"])["sourcePageId"],
                message(pages, record["event_id"])["sourceMessageId"]) == (work_id, result.result_event_id)
        assert message(pages, record["event_id"])["role"] == "system"
        assert pages.view(work_id, source_message_id=result.result_event_id)["messages"][0]["content"] == RESULT
        assert len(fixture.requests) == requests
    finally:
        pages.runtime.close()


@pytest.mark.parametrize("process_before_succession", [False, True])
def test_report_source_uses_original_subject_after_committed_succession_and_restart(
        tmp_path, monkeypatch, process_before_succession):
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True)
    try:
        work_id, result, record = reported_work(pages)
        runtime = pages.runtime
        if process_before_succession:
            activate(pages, "home", record["event_id"])
        original_subject = runtime.continuity_engram_id
        original_rows = pages._rows("home")
        prepared = runtime._mgr.prepare_succession(original_subject,
            parent_event_id=result.result_event_id, runtime_fence=runtime._current_runtime_fence())
        assert runtime.causal_ledger.committed_history_lineage(
            runtime.world_id, prepared.successor_id) == (prepared.successor_id,)
        # A cached predecessor row cannot gain a source link for a provisional
        # successor before the authoritative generation commits.
        with monkeypatch.context() as changed:
            changed.setattr(runtime, "_front_id", prepared.successor_id)
            uncommitted = pages._messages("home", original_rows, [])
            assert all(row["sourcePageId"] is None for row in uncommitted)
        successor = runtime._mgr.commit_succession(prepared,
            runtime_fence=runtime._current_runtime_fence()).new_id
        assert successor != original_subject
        assert runtime.world.get_task_front(work_id).focal_engram_id == successor
        report_subject = original_subject if process_before_succession else successor
        assert runtime.causal_ledger.get_event(record["event_id"]).engram_id == report_subject
        assert message(pages, record["event_id"])["sourceMessageId"] == result.result_event_id
        assert native_report(pages, record)["source_action_id"] == record["source_action_id"]
        if not process_before_succession:
            # An unread report can cross another real committed handoff without
            # replacing its first Work/source action with either new subject.
            again = runtime._mgr.prepare_succession(successor,
                runtime_fence=runtime._current_runtime_fence())
            successor = runtime._mgr.commit_succession(again,
                runtime_fence=runtime._current_runtime_fence()).new_id
            assert message(pages, record["event_id"])["sourceMessageId"] == result.result_event_id
            assert native_report(pages, record)["source_turn_id"] == result.turn_id
        pages.runtime.close()
        pages = PageService(tmp_path, model_factory=None)
        assert pages.runtime.continuity_engram_id == successor
        report = message(pages, record["event_id"])
        assert report["role"] == "system"
        assert (report["sourcePageId"], report["sourceMessageId"]) == (work_id, result.result_event_id)
        assert native_report(pages, record)["source_action_id"] == record["source_action_id"]
        assert next(row for row in pages.view(work_id, source_message_id=result.result_event_id)["messages"]
                    if row["id"] == result.result_event_id)["content"] == RESULT
    finally:
        pages.runtime.close()


@pytest.mark.parametrize("candidate", ["provisional_successor", "unrelated_subject"])
def test_reassigned_queue_without_committed_lineage_cannot_claim_old_report(tmp_path, monkeypatch, candidate):
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True)
    try:
        _, result, record = reported_work(pages)
        runtime = pages.runtime
        original_subject = runtime.continuity_engram_id
        if candidate == "provisional_successor":
            prepared = runtime._mgr.prepare_succession(original_subject,
                parent_event_id=result.result_event_id, runtime_fence=runtime._current_runtime_fence())
            receiver = prepared.successor_id
        else:
            receiver = runtime.storage.create_engram(engram_id="unrelated-report-reader").id
        # Queue reassignment alone is not an authoritative generation commit.
        # Exercise the native API to prove the reader checks both facts.
        runtime.causal_ledger.reassign_queued_events(original_subject, receiver,
            runtime_fence=runtime._current_runtime_fence())
        event = runtime.causal_ledger.get_event(record["event_id"])
        assert event.engram_id == receiver
        assert runtime.work_report_origin(event.id, record["request_id"], receiver,
            event.content, require_settled_source=True) is None
        assert runtime.work_report_origin(event.id, record["request_id"], original_subject,
            event.content, require_settled_source=True) is None
        assert all(row["event_id"] != event.id for row in home_conversation(runtime, receiver)["messages"])
        with monkeypatch.context() as changed:
            changed.setattr(runtime, "_front_id", receiver)
            assert all(row["sourcePageId"] is None for row in pages.view("home")["messages"])
    finally:
        pages.runtime.close()


def test_raw_metadata_text_and_unreported_work_cannot_mint_a_report_source(tmp_path, monkeypatch):
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True)
    try:
        work_id, result, record = reported_work(pages)
        other = pages.create_page("Unreported Work", "Keep this result in Work.")
        activate(pages, other["page"]["id"], other["initialMessageId"])
        runtime = pages.runtime
        origin = runtime.work_report_origin(record["event_id"], record["request_id"],
            runtime.continuity_engram_id, runtime._native_work.report_content(record),
            require_settled_source=True)
        assert origin is not None
        forged = pages.send("home", runtime._native_work.report_content(record))["messageId"]
        metadata = {"work_report": {**origin, "work_id": other["page"]["id"],
                                     "source_turn_id": result.turn_id},
                    "sourcePageId": work_id, "sourceMessageId": result.result_event_id}
        with runtime.storage._lock:
            runtime.storage._conn.execute("UPDATE causal_events SET metadata=? WHERE id=?",
                (json.dumps(metadata), forged))
            runtime.storage._conn.commit()
        false_report = message(pages, forged)
        assert false_report["role"] == "user" and false_report["activation"] is None
        assert false_report["sourcePageId"] is None and false_report["sourceMessageId"] is None
        assert false_report["content"] == runtime._native_work.report_content(record)
        assert false_report["references"] == []
        assert len(runtime.storage.native_work_requests(limit=32)) == 1
        assert message(pages, record["event_id"])["sourcePageId"] == work_id
    finally:
        pages.runtime.close()


def test_user_selected_home_return_keeps_its_real_user_speaker(tmp_path, monkeypatch):
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True)
    try:
        work_id, result, record = reported_work(pages)
        returned = pages.return_home(work_id, "I chose this Work finding for Home.", result.result_event_id)
        selected = message(pages, returned["messageId"])
        assert selected["role"] == "user" and selected["activation"] is None
        assert (selected["sourcePageId"], selected["sourceMessageId"]) == (work_id, result.result_event_id)
        assert pages.runtime.causal_ledger.get_event(selected["id"]).source.value == "user"
        assert message(pages, record["event_id"])["role"] == "system"
    finally:
        pages.runtime.close()


@pytest.mark.parametrize("damage", ["source_scope", "source_result", "event_source_turn"])
def test_inconsistent_native_source_evidence_never_exposes_a_navigation_target(tmp_path, monkeypatch, damage):
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True)
    try:
        _, result, record = reported_work(pages)
        other = pages.create_page("Another Work", "Keep this result separate.")
        other_result = activate(pages, other["page"]["id"], other["initialMessageId"])
        runtime = pages.runtime
        if damage == "source_scope":
            record["source_scope"]["context_id"] = pages.execution._scope(other["page"]["id"]).context_id
            runtime.storage.save_component_state(record["request_id"], record)
        else:
            with runtime.storage._lock:
                if damage == "source_result":
                    runtime.storage._conn.execute("UPDATE harness_turns SET result_event_id=? WHERE id=?",
                        (other_result.result_event_id, result.turn_id))
                else:
                    runtime.storage._conn.execute("UPDATE causal_events SET metadata=json_set(metadata, "
                        "'$.stimulus_source_turn_id', ?) WHERE id=?", (other_result.turn_id, record["event_id"]))
                runtime.storage._conn.commit()
        report = message(pages, record["event_id"])
        assert report["sourcePageId"] is None and report["sourceMessageId"] is None
        assert report["content"] == runtime._native_work.report_content(record)
        if damage == "source_scope":
            with pytest.raises(ServiceError, match="invalid source evidence"):
                home_conversation(runtime, runtime.continuity_engram_id)
        elif damage == "event_source_turn":
            assert all(row["event_id"] != record["event_id"]
                       for row in home_conversation(runtime, runtime.continuity_engram_id)["messages"])
    finally:
        pages.runtime.close()
