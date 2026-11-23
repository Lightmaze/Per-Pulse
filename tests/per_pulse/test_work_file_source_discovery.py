"""A Work can use native file receipts to find a prior Work's actual deliverable.

The model's tool choices are explicitly synthetic. Original file writes, Kernel
actions, SQLite recovery, source attribution, Home delivery and Undo are real.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from per_pulse.service import PageService, PulseSettings
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
            ("read_file", {"path": "source-approved.md"}),
            ("mutate_file", {"path": "delivered-evidence.md", "expected_digest": None,
                             "content": "# Delivered\nVerified original source, not the draft.\n"}),
            ("work", {"op": "report", "kind": "result",
                      "note": "Used the approved source and completed the file.",
                      "artifact_path": "delivered-evidence.md"}),
        ]
        created = second.create_page(
            "Follow-on delivered Work",
            "Find which of the earlier Work's two finished files is approved. "
            "Read the original and deliver the derived file to Home.",
        )
        target_id = created["page"]["id"]
        _activate(second, created["initialMessageId"])
        assert [result["status"] for result in fixture.outcomes] == [
            "succeeded", "succeeded", "succeeded", "succeeded",
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
        assert "Verified original source" in fixture.outcomes[1]["output"]["content"]
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
        third.artifacts.undo(target_id, target_artifact["id"])
        final = next(row for row in third.view("home")["messages"]
                     if row["id"] == reports[0]["event_id"])
        assert final["reportedArtifact"]["undone"] is True
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
