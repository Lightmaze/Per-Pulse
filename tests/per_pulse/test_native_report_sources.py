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
from pulse_system.service.native_work import validate as validate_work

NOTE = "The selected finding from this Work. [Explicit synthetic fixture]"
FILE_TASK = "Write an attributed file and report its verified result to Home."
PRIOR_WRITE_TASK = "Write the completed file now; report it during a later Work turn."
PRIOR_REPORT_TASK = "Report the file written during my earlier Work turn."
PRIOR_REPORT_PATH_TASK = "Report the prior-delivery.md file by its path, without using an old action ID."
FOREIGN_REPORT_TASK = "Try reporting a file from another Work."
FOREIGN_REPORT_PATH_TASK = "Try reporting another Work's prior-delivery.md by path."
FILE_TEXT = "# Delivery\n\nDerived from the earlier source Work.\n"
RESULT = "The original detailed Work result. [Explicit synthetic fixture]"


class ReportFixture:
    def __init__(self, monkeypatch):
        monkeypatch.setenv("NATIVE_REPORT_FIXTURE_KEY", "explicit-synthetic-credential")
        self.requests = []
        self.prior_artifact_id = None

    def factory(self, scope):
        def respond(request):
            body = json.loads(request.content)
            self.requests.append((scope, body))
            messages = body["messages"]
            current = max(i for i, row in enumerate(messages) if row["role"] == "user")
            task = messages[current]["content"]
            # Committed succession may bring the predecessor result into the
            # user message that starts this Work turn. Match the exact last
            # authored task instead of treating the carried context as a new task.
            latest_task = task.rsplit("\n\n", 1)[-1]
            if latest_task in {PRIOR_WRITE_TASK, PRIOR_REPORT_TASK, PRIOR_REPORT_PATH_TASK,
                               FOREIGN_REPORT_TASK, FOREIGN_REPORT_PATH_TASK}:
                task = latest_task
            results = [json.loads(row["content"]) for row in messages[current + 1:]
                       if row["role"] == "tool"]
            if scope.context_id.startswith("work:") and task == FILE_TASK and len(results) < 3:
                if not results:
                    name = "mutate_file"
                    arguments = {"path": "delivery.md", "expected_digest": None, "content": FILE_TEXT}
                elif len(results) == 1:
                    assert results[0]["status"] == "succeeded", results
                    name = "work"
                    arguments = {"op": "report", "kind": "result", "note": "A finished file was produced.",
                                 "artifact_id": "forged-artifact-not-in-work"}
                else:
                    assert results[1]["status"] == "failed", results
                    name = "work"
                    arguments = {"op": "report", "kind": "result", "note": "A finished file was produced.",
                                 "artifact_id": results[0]["output"]["action_request_id"]}
                delta = {"tool_calls": [{"index": 0, "id": f"synthetic-{name}-{len(results)}",
                    "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
                reason = "tool_calls"
            elif scope.context_id.startswith("work:") and task == PRIOR_WRITE_TASK and not results:
                delta = {"tool_calls": [{"index": 0, "id": "synthetic-prior-file-action",
                    "type": "function", "function": {"name": "mutate_file", "arguments": json.dumps(
                        {"path": "prior-delivery.md", "expected_digest": None, "content": FILE_TEXT})}}]}
                reason = "tool_calls"
            elif scope.context_id.startswith("work:") and task in {
                    PRIOR_REPORT_TASK, PRIOR_REPORT_PATH_TASK,
                    FOREIGN_REPORT_TASK, FOREIGN_REPORT_PATH_TASK} and not results:
                by_path = task in {PRIOR_REPORT_PATH_TASK, FOREIGN_REPORT_PATH_TASK}
                args = {"op": "report", "kind": "result", "note": "The earlier file is finished."}
                if by_path:
                    args["artifact_path"] = "prior-delivery.md"
                else:
                    assert self.prior_artifact_id is not None
                    args["artifact_id"] = self.prior_artifact_id
                delta = {"tool_calls": [{"index": 0, "id": "synthetic-prior-report",
                    "type": "function", "function": {"name": "work", "arguments": json.dumps(args)}}]}
                reason = "tool_calls"
            elif scope.context_id.startswith("work:") and task == "Select a finding for Home." and not results:
                delta = {"tool_calls": [{"index": 0, "id": "synthetic-report-call", "type": "function",
                    "function": {"name": "work", "arguments": json.dumps(
                        {"op": "report", "kind": "finding", "note": NOTE})}}]}
                reason = "tool_calls"
            else:
                if task == FILE_TASK:
                    assert [row["status"] for row in results] == ["succeeded", "failed", "succeeded"], results
                elif task == PRIOR_WRITE_TASK:
                    assert len(results) == 1 and results[0]["status"] == "succeeded", results
                    self.prior_artifact_id = results[0]["output"]["action_request_id"]
                elif task in {PRIOR_REPORT_TASK, PRIOR_REPORT_PATH_TASK}:
                    assert len(results) == 1 and results[0]["status"] == "succeeded", results
                elif task in {FOREIGN_REPORT_TASK, FOREIGN_REPORT_PATH_TASK}:
                    assert len(results) == 1 and results[0]["status"] == "failed", results
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


def test_work_delivers_verified_file_through_home_and_undo_after_restart(tmp_path, monkeypatch):
    """An ordinary Work actually writes a file, reports its action, and returns Home.

    Model calls and its corrected tool choice are explicit synthetic HTTP.
    File bytes, Kernel action, outbox, source links and Undo are native.
    """
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory,
                        allow_workspace_actions=True, auto_apply_edits=True)
    try:
        earlier = pages.create_page("Earlier source", "Keep the original source for the next Work.")
        source_work = earlier["page"]["id"]
        activate(pages, source_work, earlier["initialMessageId"])
        correction = pages.send(source_work, "Use the original source in the delivered file.")
        activate(pages, source_work, correction["messageId"])
        later = pages.create_page("Completed file delivery", FILE_TASK, source_page_id=source_work,
                                  source_message_id=correction["messageId"])
        work_id = later["page"]["id"]
        completed = activate(pages, work_id, later["initialMessageId"])
        assert completed.result_event_id is not None
        pages.runtime._native_work.drain()
        report, = [row for row in pages.runtime.storage.native_work_requests(limit=32)
                   if row["arguments"]["op"] == "report"]
        assert report["status"] == "delivered"
        artifact, = pages.artifacts.snapshot(work_id)["artifacts"]
        assert (tmp_path / "workspace" / "delivery.md").read_text() == FILE_TEXT
        assert artifact["path"] == "delivery.md"
        assert report["arguments"]["artifact_id"] == artifact["id"]
        assert report["artifact_source"] == {
            "artifact_id": artifact["id"], "path": artifact["path"], "sha256": artifact["sha256"],
        }
        report_messages = [row for row in pages.view("home")["messages"]
                           if row["id"] == report["event_id"]]
        assert len(report_messages) == 1
        home_result, = report_messages
        assert home_result["activation"] == "work_report"
        assert home_result["sourcePageId"] == work_id
        assert home_result["sourceMessageId"] == completed.result_event_id
        assert home_result["reportedArtifact"] == {
            "pageId": work_id, "artifactId": artifact["id"],
            "path": "delivery.md", "sha256": artifact["sha256"], "undone": False,
        }
        assert "historical receipt" in home_result["content"]
        assert pages.artifacts.read(work_id, artifact["id"])["content"] == FILE_TEXT
        # The old Work correction remains an exact, independent native message.
        assert next(row for row in pages.view(source_work)["messages"]
                    if row["id"] == correction["messageId"])["content"] == (
                        "Use the original source in the delivered file."
                    )
        assert any(result["status"] == "failed"
                   for _, body in fixture.requests
                   for result in [json.loads(msg["content"])
                                  for msg in body["messages"] if msg["role"] == "tool"]
                   if isinstance(result, dict) and result.get("status") == "failed")
    finally:
        pages.runtime.close()

    restarted = PageService(tmp_path, model_factory=None, allow_workspace_actions=True,
                            auto_apply_edits=True)
    try:
        before = next(row for row in restarted.view("home")["messages"]
                      if row["id"] == report["event_id"])
        assert before["reportedArtifact"]["artifactId"] == artifact["id"]
        assert restarted.artifacts.read(work_id, artifact["id"])["content"] == FILE_TEXT
        assert restarted.artifacts.undo(work_id, artifact["id"])
        after = next(row for row in restarted.view("home")["messages"]
                     if row["id"] == report["event_id"])
        assert after["reportedArtifact"]["undone"] is True
        assert restarted.artifacts.read(work_id, artifact["id"])["state"] == "unavailable"
        # A human imitation of report text is never a native file receipt.
        forged = restarted.send("home", before["content"])
        unrelated = next(row for row in restarted.view("home")["messages"]
                         if row["id"] == forged["messageId"])
        assert unrelated.get("reportedArtifact") is None
    finally:
        restarted.runtime.close()


@pytest.mark.parametrize("cross_generation", [False, True])
@pytest.mark.parametrize("report_by_path", [False, True])
def test_prior_work_turn_file_reports_after_restart_but_another_work_cannot_claim_it(
        tmp_path, monkeypatch, cross_generation, report_by_path):
    """Finish a file, restart, and report it later without minting a new file action.

    Model tool calls are authored synthetic HTTP choices; real Kernel actions,
    native outbox delivery, Work ownership, persisted bytes and Undo are used.
    """
    fixture = ReportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory,
                        allow_workspace_actions=True, auto_apply_edits=True)
    try:
        created = pages.create_page("Long-running delivery", PRIOR_WRITE_TASK)
        work_id = created["page"]["id"]
        first = activate(pages, work_id, created["initialMessageId"])
        assert first.result_event_id is not None
        artifact, = pages.artifacts.snapshot(work_id)["artifacts"]
        assert fixture.prior_artifact_id == artifact["id"]
        assert (tmp_path / "workspace" / "prior-delivery.md").read_text() == FILE_TEXT
        assert not [r for r in pages.runtime.storage.native_work_requests(limit=32)
                    if r["arguments"]["op"] == "report"]
        if cross_generation:
            runtime = pages.runtime
            prepared = runtime._mgr.prepare_succession(
                runtime.continuity_engram_id, parent_event_id=first.result_event_id,
                runtime_fence=runtime._current_runtime_fence())
            successor = runtime._mgr.commit_succession(
                prepared, runtime_fence=runtime._current_runtime_fence()).new_id
            assert runtime.world.get_task_front(work_id).focal_engram_id == successor
    finally:
        pages.runtime.close()

    pages = PageService(tmp_path, model_factory=fixture.factory,
                        allow_workspace_actions=True, auto_apply_edits=True)
    try:
        # The same known action ID must not let another Work report the file.
        outsider = pages.create_page("Another Work",
                                    FOREIGN_REPORT_PATH_TASK if report_by_path else FOREIGN_REPORT_TASK)
        activate(pages, outsider["page"]["id"], outsider["initialMessageId"])
        assert not [r for r in pages.runtime.storage.native_work_requests(limit=32)
                    if r["arguments"]["op"] == "report"]

        # The actual original file must remain resolvable in the Work's
        # committed Kernel history before a successor attempts to report it.
        assert pages.artifacts._resolve_action(work_id, artifact["id"])[0]["sha256"] == artifact["sha256"]
        sent = pages.send(work_id, PRIOR_REPORT_PATH_TASK if report_by_path else PRIOR_REPORT_TASK)
        second = activate(pages, work_id, sent["messageId"])
        assert second.turn_id != first.turn_id
        pages.runtime._native_work.drain()
        reports = [r for r in pages.runtime.storage.native_work_requests(limit=32)
                   if r["arguments"]["op"] == "report"]
        if not reports:
            scope = pages.execution._scope(work_id)
            histories = pages.execution._history_scopes(scope)
            actions = [
                (action.tool_name, action.status.value, action.error_code)
                for previous in histories
                for action in pages.runtime.kernel_service.store.actions_for_session(previous)
            ]
            pytest.fail(f"No native report admitted after succession: {actions!r}; "
                        f"last fixture requests: {fixture.requests[-3:]!r}")
        report, = reports
        assert report["status"] == "delivered"
        if report_by_path:
            assert report["arguments"]["artifact_path"] == "prior-delivery.md"
            assert "artifact_id" not in report["arguments"]
        else:
            assert report["arguments"]["artifact_id"] == artifact["id"]
        assert report["source_turn_id"] == second.turn_id
        assert report["artifact_source"] == {
            "artifact_id": artifact["id"], "path": artifact["path"], "sha256": artifact["sha256"],
        }
        home = message(pages, report["event_id"])
        assert home["sourcePageId"] == work_id
        assert home["sourceMessageId"] == second.result_event_id
        assert home["reportedArtifact"]["artifactId"] == artifact["id"]
        assert pages.artifacts.download(work_id, artifact["id"])["content"] == FILE_TEXT.encode()
        pages.runtime.close()
        pages = PageService(tmp_path, model_factory=None,
                            allow_workspace_actions=True, auto_apply_edits=True)
        assert pages.artifacts.read(work_id, artifact["id"])["content"] == FILE_TEXT
        assert message(pages, report["event_id"])["reportedArtifact"]["artifactId"] == artifact["id"]
        pages.artifacts.undo(work_id, artifact["id"])
        assert message(pages, report["event_id"])["reportedArtifact"]["undone"] is True
    finally:
        pages.runtime.close()


def test_report_by_path_rejects_ambiguous_identity_and_unreadable_paths():
    base = {"op": "report", "kind": "result", "note": "Finished file."}
    assert validate_work({**base, "artifact_path": "deliverables/final.md"})["artifact_path"] == (
        "deliverables/final.md")
    with pytest.raises(ValueError, match="artifact ID or a file path"):
        validate_work({**base, "artifact_path": "deliverables/final.md",
                       "artifact_id": "opaque-old-id"})
    with pytest.raises(ValueError, match="invalid artifact_path"):
        validate_work({**base, "artifact_path": "deliverables/final.md\nforged"})
