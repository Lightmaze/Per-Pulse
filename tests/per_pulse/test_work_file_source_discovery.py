"""A Work can use native file receipts to find a prior Work's actual deliverable.

The model's tool choices are explicitly synthetic. Original file writes, Kernel
actions, SQLite recovery, source attribution, Home delivery and Undo are real.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from per_pulse.service import PageService, PulseSettings
from pulse_harness.core import session_store
from pulse_system.service.native_work import validate
from test_internal_work_organization import WorkFixture, run_event


def _activate(pages, event_id):
    return run_event(pages.runtime, pages.runtime.causal_ledger.get_event(event_id))


def test_prior_work_file_receipts_drive_a_real_follow_on_file_after_restart(tmp_path, monkeypatch):
    """Select between similar finished files even when conversation text omits paths."""
    fixture = WorkFixture(monkeypatch.setenv)
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
        _activate(first, created["initialMessageId"])
        assert [result["status"] for result in fixture.outcomes] == ["succeeded", "succeeded"]
        originals = {item["path"]: item for item in first.artifacts.snapshot(source_id)["artifacts"]}
        source_turn = next(iter(originals.values()))["turnId"]
        assert set(originals) == {"source-draft.md", "source-approved.md"}
        assert (tmp_path / "workspace" / "source-approved.md").read_text() == (
            "# Evidence\nVerified original source.\n"
        )
    finally:
        first.runtime.close()

    second = PageService(
        tmp_path, model_factory=fixture.factory, posture=False,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=False),
    )
    try:
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
