"""A Work can use native file receipts to find a prior Work's actual deliverable.

The model's tool choices are explicitly synthetic. Original file writes, Kernel
actions, SQLite recovery, source attribution, Home delivery and Undo are real.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import os
import statistics
import time

from per_pulse.service import PageService, PulseSettings
from pulse_harness.core import session_store
from pulse_harness.core.turn_runner import TurnRunner
from pulse_system.service.native_work import validate
from test_internal_work_organization import WorkFixture, run_event


def _activate(pages, event_id):
    return run_event(pages.runtime, pages.runtime.causal_ledger.get_event(event_id))


def _check_nested_work_view(pages, monkeypatch, source_id, source_message_id, target_id):
    # Capture the actual reads during a user's navigation into the nested
    # Work. Source-specific reads are legitimate; full ancestor projections
    # just to display a breadcrumb are not.
    history_reads = []
    execution_reads = []
    read_rows = pages._rows
    snapshot = pages.execution.snapshot

    def record_rows(page_id, **kwargs):
        if not kwargs:
            history_reads.append(page_id)
        return read_rows(page_id, **kwargs)

    def record_execution(page_id):
        execution_reads.append(page_id)
        return snapshot(page_id)

    with monkeypatch.context() as observed:
        observed.setattr(pages, "_rows", record_rows)
        observed.setattr(pages.execution, "snapshot", record_execution)
        view = pages.view(target_id)
    assert history_reads == [target_id]
    assert execution_reads == [target_id]
    assert view["breadcrumbs"] == [
        {"id": "home", "title": "Home"},
        {"id": source_id, "title": "Prior evidence Work"},
        {"id": target_id, "title": "Follow-on delivered Work"},
    ]
    assert view["page"]["sourcePageId"] == source_id
    assert view["page"]["sourceMessageId"] == source_message_id
    assert view["page"]["status"] == "idle"
    assert not view["execution"]["pendingActions"]
    original = pages.view(source_id, source_message_id=source_message_id)
    assert any(row["id"] == source_message_id for row in original["messages"])
    assert any(ref["targetPageId"] == target_id and ref["sourceMessageId"] == source_message_id
               for ref in original["references"])


def test_prior_work_file_receipts_drive_a_real_follow_on_file_after_restart(tmp_path, monkeypatch):
    """Select between similar finished files even after a long Work and restart.

    Set PER_PULSE_LONG_WORK_TURNS=200 for the manual long-history timing run.
    Timings include the actual native turn, not just the authority query.
    """
    turn_count = int(os.environ.get("PER_PULSE_LONG_WORK_TURNS", "12"))
    assert turn_count >= 2
    fixture = WorkFixture(monkeypatch.setenv)
    authority_reads = []
    admission_reads = []
    original_unsettled = session_store.HarnessStore.unsettled_turns_for_session

    def record_unsettled(self, scope):
        rows = original_unsettled(self, scope)
        admission_reads.append(len(rows))
        return rows

    monkeypatch.setattr(session_store.HarnessStore, "unsettled_turns_for_session", record_unsettled)
    pending_turns = None
    original_validate = TurnRunner._validate_assistant_messages
    original_read = session_store.HarnessStore.model_rounds_for_session

    def validate_pending(self, scope, messages):
        nonlocal pending_turns
        pending_turns = {message.scope.turn_id for message in messages}
        try:
            return original_validate(self, scope, messages)
        finally:
            pending_turns = None

    def record_rounds(self, scope, *, limit=None, turn_ids=None):
        kwargs = {"limit": limit}
        if turn_ids is not None:
            kwargs["turn_ids"] = turn_ids
        rows = original_read(self, scope, **kwargs)
        if pending_turns is not None:
            authority_reads.append((set(pending_turns), turn_ids, len(rows)))
            assert turn_ids is not None
            assert set(turn_ids) <= pending_turns
            assert all(row.scope.turn_id in turn_ids for row in rows)
        return rows

    monkeypatch.setattr(TurnRunner, "_validate_assistant_messages", validate_pending)
    monkeypatch.setattr(session_store.HarnessStore, "model_rounds_for_session", record_rounds)
    source_id = None
    originals = None
    source_turn = None
    first = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        fixture.plan = [
            ("mutate_file", {"path": "source-draft.md", "expected_digest": None,
                             "content": "# Evidence\nPreliminary unverified draft.\n"}),
            ("mutate_file", {"path": "source-approved.md", "expected_digest": None,
                             "content": "# Evidence\nVerified original source.\n"}),
        ]
        created = first.create_page("Prior evidence Work", "Write two versions of the research.")
        source_id = created["page"]["id"]
        source_message_id = created["initialMessageId"]
        _activate(first, source_message_id)
        assert [result["status"] for result in fixture.outcomes] == ["succeeded", "succeeded"]
        originals = {item["path"]: item for item in first.artifacts.snapshot(source_id)["artifacts"]}
        source_turn = next(iter(originals.values()))["turnId"]
        assert set(originals) == {"source-draft.md", "source-approved.md"}
        assert (tmp_path / "workspace" / "source-approved.md").read_text() == (
            "# Evidence\nVerified original source.\n"
        )
        fixture.plan = []
        durations = []
        profiles = None
        if os.environ.get("PER_PULSE_LONG_WORK_PROFILE") == "1":
            import cProfile
            profiles = (cProfile.Profile(), cProfile.Profile())
        sample = min(20, max(1, turn_count // 4))
        for index in range(turn_count):
            profile = None
            if profiles is not None:
                if index < sample:
                    profile = profiles[0]
                elif index >= turn_count - sample:
                    profile = profiles[1]
            started = time.perf_counter()
            if profile is not None:
                profile.enable()
            try:
                sent = first.send(source_id, f"Review the evidence again. Revision {index + 1}.")
                result = _activate(first, sent["messageId"])
            finally:
                if profile is not None:
                    profile.disable()
            assert "selected internal work is complete" in result.content
            durations.append(time.perf_counter() - started)
            if (index + 1) % 20 == 0:
                print(f"Native Work through follow-up {index + 1}: "
                      f"last 20 median={statistics.median(durations[-20:]):.3f}s", flush=True)
        assert authority_reads
        # The real foreground admission path must not load completed turns
        # simply to decide whether the next Work message can run.
        assert len(admission_reads) >= turn_count
        assert not any(admission_reads)
        if profiles is not None:
            import pstats
            for label, profile in zip(("first", "last"), profiles):
                stats = pstats.Stats(profile).strip_dirs()
                print(f"Profile: {label} {sample} complete follow-ups "
                      "(caller thread; cumulative entries can overlap)", flush=True)
                stats.sort_stats("cumulative").print_stats(30)
                stats.print_stats(r"session_store\.py|turn_runner\.py|context_window\.py", 40)
                stats.sort_stats("tottime").print_stats(30)
        print(f"Native Work follow-ups: {turn_count}; "
              f"first {sample} median={statistics.median(durations[:sample]):.3f}s; "
              f"last {sample} median={statistics.median(durations[-sample:]):.3f}s; "
              f"total={sum(durations):.3f}s; explicit synthetic HTTP.")
    finally:
        first.runtime.close()

    second = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        # Re-enter the original long-lived Work before starting a different one.
        # A fresh process must be able to validate its durable terminal history.
        fixture.plan = []
        authority_reads.clear()
        started = time.perf_counter()
        resumed = second.send(source_id, "Continue reviewing the original evidence after reopening.")
        result = _activate(second, resumed["messageId"])
        assert "selected internal work is complete" in result.content
        assert authority_reads
        print(f"Reopened original Work: {time.perf_counter() - started:.3f}s; "
              f"authority batches={[len(batch) for _, batch, _ in authority_reads]}", flush=True)
        fixture.plan = [
            ("work", {"op": "inspect", "work_id": source_id, "include_files": True}),
            ("work", {"op": "inspect", "work_id": source_id, "include_files": True,
                      "file_before_action_id": originals["source-approved.md"]["id"]}),
            ("read_file", {"path": "source-approved.md",
                           "expected_digest": originals["source-approved.md"]["sha256"]}),
            ("mutate_file", {"path": "delivered-evidence.md", "expected_digest": None,
                             "content": "# Delivered\nVerified original source, not the draft.\n"}),
            ("work", {"op": "report", "kind": "result",
                      "note": "Used the approved source and completed the file.",
                      "artifact_path": "delivered-evidence.md",
                      "input_file_work_id": source_id,
                      "input_file_artifact_id": originals["source-approved.md"]["id"]}),
        ]
        created = second.create_page(
            "Follow-on delivered Work",
            "Find which of the earlier Work's two finished files is approved. "
            "Read the original and deliver the derived file to Home.",
            source_page_id=source_id, source_message_id=source_message_id,
        )
        target_id = created["page"]["id"]
        # Real model/tool rounds may finish within one timestamp millisecond.
        # The Kernel event sequence, not an arbitrary ID or a relaxed clock
        # comparison, must preserve the read-before-write relationship.
        with monkeypatch.context() as clock:
            same_millisecond = session_store.utc_now()
            clock.setattr(session_store, "utc_now", lambda: same_millisecond)
            _activate(second, created["initialMessageId"])
        assert [result["status"] for result in fixture.outcomes] == [
            "succeeded", "succeeded", "succeeded", "succeeded", "succeeded",
        ], fixture.outcomes
        inspection = fixture.outcomes[0]["output"]
        assert inspection["file_receipts_are_historical"] is True
        assert inspection["file_history_complete"] is True
        files = inspection["completed_files"]
        assert {entry["path"] for entry in files} == set(originals)
        assert all(entry["source_work_id"] == source_id for entry in files)
        assert all(entry["source_turn_id"] == source_turn for entry in files)
        assert {entry["artifact_id"] for entry in files} == {
            item["id"] for item in originals.values()
        }
        assert {entry["path"]: entry["sha256"] for entry in files} == {
            path: artifact["sha256"] for path, artifact in originals.items()
        }
        older = fixture.outcomes[1]["output"]["completed_files"]
        assert [entry["path"] for entry in older] == ["source-draft.md"]
        assert "Verified original source" in fixture.outcomes[2]["output"]["content"]
        assert (tmp_path / "workspace" / "delivered-evidence.md").read_text() == (
            "# Delivered\nVerified original source, not the draft.\n"
        )
        second.runtime._native_work.drain()
        reports = [row for row in second.runtime.storage.native_work_requests(limit=32)
                   if row["arguments"]["op"] == "report"]
        assert len(reports) == 1 and reports[0]["status"] == "delivered"
        target_artifact, = second.artifacts.snapshot(target_id)["artifacts"]
        assert reports[0]["artifact_source"]["artifact_id"] == target_artifact["id"]
        assert second.artifacts.download(target_id, target_artifact["id"])["content"] == (
            b"# Delivered\nVerified original source, not the draft.\n"
        )
        home = next(row for row in second.view("home")["messages"]
                    if row["id"] == reports[0]["event_id"])
        assert home["reportedArtifact"]["artifactId"] == target_artifact["id"]
        assert home["sourcePageId"] == target_id
        assert home["inputArtifact"] == {
            "pageId": source_id,
            "artifactId": originals["source-approved.md"]["id"],
            "path": "source-approved.md",
            "sha256": originals["source-approved.md"]["sha256"],
            "undone": False,
        }
        assert "Verified input read from earlier Work" in home["content"]
        _check_nested_work_view(second, monkeypatch, source_id, source_message_id, target_id)
    finally:
        second.runtime.close()

    third = PageService(
        tmp_path, model_factory=None, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        assert third.artifacts.download(target_id, target_artifact["id"])["content"] == (
            b"# Delivered\nVerified original source, not the draft.\n"
        )
        assert third.artifacts.download(source_id, originals["source-approved.md"]["id"])[
            "content"] == b"# Evidence\nVerified original source.\n"
        _check_nested_work_view(third, monkeypatch, source_id, source_message_id, target_id)
        third.artifacts.undo(target_id, target_artifact["id"])
        final = next(row for row in third.view("home")["messages"]
                     if row["id"] == reports[0]["event_id"])
        assert final["reportedArtifact"]["undone"] is True
        assert final["inputArtifact"]["artifactId"] == originals["source-approved.md"]["id"]
        assert final["inputArtifact"]["undone"] is False
        assert third.artifacts.read(target_id, target_artifact["id"])["state"] == "unavailable"
    finally:
        third.runtime.close()


def test_include_files_only_accepts_explicit_boolean():
    assert validate({"op": "inspect", "work_id": "owned-work", "include_files": True})[
        "include_files"
    ] is True
    for invalid in ("yes", 1, None, [], {}):
        try:
            validate({"op": "inspect", "work_id": "owned-work", "include_files": invalid})
        except ValueError as error:
            assert "include_files" in str(error)
        else:
            raise AssertionError("include_files must be a boolean")


def test_file_history_cursor_requires_inspection():
    try:
        validate({"op": "inspect", "work_id": "owned-work",
                  "file_before_action_id": "a-prior-action"})
    except ValueError as error:
        assert "include_files" in str(error)
    else:
        raise AssertionError("a file cursor must request file history")

def test_input_file_source_requires_both_owned_work_and_action():
    good = {"op": "report", "kind": "result", "note": "The file was used.",
            "input_file_work_id": "source-work",
            "input_file_artifact_id": "original-file-action"}
    assert validate(good)["input_file_work_id"] == "source-work"
    for missing in ("input_file_work_id", "input_file_artifact_id"):
        incomplete = {key: value for key, value in good.items() if key != missing}
        try:
            validate(incomplete)
        except ValueError as error:
            assert "input Work" in str(error)
        else:
            raise AssertionError("input attribution must include both original IDs")

def test_source_file_claim_without_matching_native_read_does_not_reach_home(tmp_path, monkeypatch):
    fixture = WorkFixture(monkeypatch.setenv)
    first = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        fixture.plan = [("mutate_file", {
            "path": "unread-original.md", "expected_digest": None,
            "content": "# Original\\nUnseen by the later Work.\\n",
        })]
        created = first.create_page("Original source Work", "Create the source file.")
        source_id = created["page"]["id"]
        _activate(first, created["initialMessageId"])
        artifact, = first.artifacts.snapshot(source_id)["artifacts"]
    finally:
        first.runtime.close()

    second = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        fixture.plan = [("work", {
            "op": "report", "kind": "result",
            "note": "Claimed to have used the unread original.",
            "input_file_work_id": source_id,
            "input_file_artifact_id": artifact["id"],
        })]
        created = second.create_page("Unverified source claim", "Report the original file.")
        _activate(second, created["initialMessageId"])
        assert fixture.outcomes[0]["status"] != "succeeded"
        second.runtime._native_work.drain()
        assert not any(
            "Claimed to have used" in str(row.get("arguments", {}))
            for row in second.runtime.storage.native_work_requests(limit=32)
        )
        assert not any(
            "Claimed to have used" in message["content"]
            for message in second.view("home")["messages"]
        )
    finally:
        second.runtime.close()


def test_report_rejects_source_read_only_after_output_was_written(tmp_path, monkeypatch):
    """A late read cannot be attributed as an input to an already-created file."""
    fixture = WorkFixture(monkeypatch.setenv)
    first = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        fixture.plan = [("mutate_file", {
            "path": "chronology-original.md", "expected_digest": None,
            "content": "# Original\nSource facts.\n",
        })]
        created = first.create_page("Chronology source Work", "Write a source file.")
        source_id = created["page"]["id"]
        _activate(first, created["initialMessageId"])
        original, = first.artifacts.snapshot(source_id)["artifacts"]
    finally:
        first.runtime.close()

    second = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        fixture.plan = [
            ("mutate_file", {"path": "chronology-output.md", "expected_digest": None,
                             "content": "# Result\nCompleted without reading source.\n"}),
            ("read_file", {"path": "chronology-original.md",
                           "expected_digest": original["sha256"]}),
            ("work", {"op": "report", "kind": "result",
                      "note": "Incorrectly claimed the late read as input.",
                      "artifact_path": "chronology-output.md",
                      "input_file_work_id": source_id,
                      "input_file_artifact_id": original["id"]}),
        ]
        created = second.create_page("Chronology result Work", "Write, read, and report.")
        result_work_id = created["page"]["id"]
        _activate(second, created["initialMessageId"])
        assert [outcome["status"] for outcome in fixture.outcomes[:2]] == [
            "succeeded", "succeeded"]
        assert fixture.outcomes[2]["status"] != "succeeded"
        assert (tmp_path / "workspace" / "chronology-output.md").read_text() == (
            "# Result\nCompleted without reading source.\n")
        assert second.artifacts.snapshot(result_work_id)["artifacts"]
        second.runtime._native_work.drain()
        assert not any(
            row["arguments"].get("note") == "Incorrectly claimed the late read as input."
            for row in second.runtime.storage.native_work_requests(limit=32))
        assert not any(
            "Incorrectly claimed the late read as input." in message["content"]
            for message in second.view("home")["messages"])
    finally:
        second.runtime.close()


def test_prepared_report_replacement_requires_the_latest_action_and_delivers_once(tmp_path, monkeypatch):
    """A changed report cannot overwrite another revision or leak an obsolete file."""
    import hashlib
    import json

    draft = "# Draft\n"
    final = "# Final\n"

    class RevisionFixture(WorkFixture):
        def handler(self, request):
            messages = json.loads(request.content)["messages"]
            current = max(i for i, item in enumerate(messages) if item["role"] == "user")
            results = [json.loads(item["content"]) for item in messages[current + 1:]
                       if item["role"] == "tool"]
            index = len(results)
            if index == 0:
                args = {"path": "revision.md", "expected_digest": None, "content": draft}
                return self.response(tool="mutate_file", arguments=args, sequence=index)
            if index == 1:
                args = {"op": "report", "kind": "result", "note": "Draft report.",
                        "artifact_path": "revision.md"}
            elif index == 2:
                args = {"path": "revision.md",
                        "expected_digest": hashlib.sha256(draft.encode()).hexdigest(),
                        "content": final}
                return self.response(tool="mutate_file", arguments=args, sequence=index)
            elif index in {3, 4, 5, 6}:
                args = {"op": "report", "kind": "result",
                        "note": "Final report." if index == 5 else "Wrong revision.",
                        "artifact_path": "revision.md"}
                if index == 4:
                    args["replace_report_action_id"] = "foreign-action"
                elif index in {5, 6}:
                    args["replace_report_action_id"] = results[1]["output"]["report_action_id"]
            else:
                self.outcomes = results
                return self.response("The final file is ready. [Explicit synthetic fixture]")
            return self.response(tool="work", arguments=args, sequence=index)

    fixture = RevisionFixture(monkeypatch.setenv)
    pages = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        created = pages.create_page("Revised Work", "Prepare and revise the file before returning Home.")
        work_id = created["page"]["id"]
        _activate(pages, created["initialMessageId"])
        assert [item["status"] for item in fixture.outcomes] == [
            "succeeded", "succeeded", "succeeded", "failed", "failed", "succeeded", "failed",
        ], fixture.outcomes
        first, last = fixture.outcomes[1]["output"], fixture.outcomes[5]["output"]
        assert first["request_id"] == last["request_id"]
        assert first["report_action_id"] != last["report_action_id"]
        pages.runtime._native_work.drain()
        record, = pages.runtime.storage.native_work_requests(limit=32)
        assert record["status"] == "delivered"
        assert record["source_action_id"] == last["report_action_id"]
        assert record["arguments"]["note"] == "Final report."
        event_id = record["event_id"]
        artifact_id = record["artifact_source"]["artifact_id"]
        assert pages.artifacts.download(work_id, artifact_id)["content"] == final.encode()
    finally:
        pages.runtime.close()

    restored = PageService(
        tmp_path, model_factory=None, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
        restored.runtime._native_work.drain()
        messages = restored.view("home")["messages"]
        delivered = [row for row in messages if row["id"] == event_id]
        assert len(delivered) == 1
        assert delivered[0]["reportedArtifact"]["artifactId"] == artifact_id
        assert "Final report." in delivered[0]["content"]
        assert not any("Draft report." in row["content"] or "Wrong revision." in row["content"]
                       for row in messages)
        assert restored.artifacts.download(work_id, artifact_id)["content"] == final.encode()
    finally:
        restored.runtime.close()


def test_long_work_keeps_its_original_input_through_delivery(tmp_path, monkeypatch):
    """A real version-pinned input stays usable after unrelated actions and reopening."""
    fixture = WorkFixture(monkeypatch.setenv)

    def open_world():
        return PageService(
            tmp_path, model_factory=fixture.factory, posture=False,
            allow_workspace_actions=True, auto_apply_edits=True,
            pulse=PulseSettings(enabled=False),
        )

    first = open_world()
    try:
        fixture.plan = [("mutate_file", {
            "path": "long-input.md", "expected_digest": None,
            "content": "Verified source: north entrance.\n",
        }), ("mutate_file", {
            "path": "other-input.md", "expected_digest": None,
            "content": "Unrelated material.\n",
        })]
        source = first.create_page("Source Work", "Save the approved source and unrelated material.")
        source_id = source["page"]["id"]
        _activate(first, source["initialMessageId"])
        originals = {row["path"]: row for row in first.artifacts.snapshot(source_id)["artifacts"]}
        original = originals["long-input.md"]
        other = originals["other-input.md"]

        fixture.plan = [("read_file", {
            "path": "long-input.md", "expected_digest": original["sha256"],
        })]
        target = first.create_page("Long follow-on Work", "Read the approved source before working.")
        target_id = target["page"]["id"]
        _activate(first, target["initialMessageId"])
        assert "north entrance" in fixture.outcomes[0]["output"]["content"]
        # Exercise the native actions, not fake rows or a reduced test limit.
        # All later calls are successful reads of a different version/path.
        for index in range(33):
            fixture.plan = [("read_file", {
                "path": "other-input.md", "expected_digest": other["sha256"],
            }) for _ in range(8)]
            sent = first.send(target_id, f"Review the unrelated material, batch {index + 1}.")
            _activate(first, sent["messageId"])
            assert len(fixture.outcomes) == 8
            assert all(row["status"] == "succeeded" for row in fixture.outcomes)
            fixture.requests.clear()
    finally:
        first.runtime.close()

    second = open_world()
    try:
        fixture.plan = [
            ("mutate_file", {"path": "long-delivery.md", "expected_digest": None,
                             "content": "Use the verified north entrance.\n"}),
            ("work", {"op": "report", "kind": "result", "note": "Completed the sourced file.",
                      "artifact_path": "long-delivery.md", "input_file_work_id": source_id,
                      "input_file_artifact_id": original["id"]}),
        ]
        sent = second.send(target_id, "Deliver the result based on the approved original source.")
        _activate(second, sent["messageId"])
        assert [row["status"] for row in fixture.outcomes] == ["succeeded", "succeeded"], fixture.outcomes
        second.runtime._native_work.drain()
        reports = [row for row in second.runtime.storage.native_work_requests(limit=32)
                   if row["arguments"]["op"] == "report"]
        assert len(reports) == 1 and reports[0]["status"] == "delivered"
        report = reports[0]
        result, = second.artifacts.snapshot(target_id)["artifacts"]
        assert second.artifacts.download(target_id, result["id"])["content"] == (
            b"Use the verified north entrance.\n"
        )
        home = next(row for row in second.view("home")["messages"] if row["id"] == report["event_id"])
        assert home["inputArtifact"]["artifactId"] == original["id"]
        assert home["reportedArtifact"]["artifactId"] == result["id"]
    finally:
        second.runtime.close()

    third = open_world()
    try:
        assert third.artifacts.download(source_id, original["id"])["content"] == (
            b"Verified source: north entrance.\n"
        )
        assert third.artifacts.download(target_id, result["id"])["content"] == (
            b"Use the verified north entrance.\n"
        )
        home = next(row for row in third.view("home")["messages"] if row["id"] == report["event_id"])
        assert home["inputArtifact"]["artifactId"] == original["id"]
        third.artifacts.undo(target_id, result["id"])
        home = next(row for row in third.view("home")["messages"] if row["id"] == report["event_id"])
        assert home["reportedArtifact"]["undone"] is True
        assert home["inputArtifact"]["undone"] is False
    finally:
        third.runtime.close()
