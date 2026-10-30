"""Work-material attention follows substantive experience changes, not mere report recency.

Real persistent World components and bounded material packing are exercised.
Source episodes are explicit constructed fixtures, not provider observations.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import time

import pytest

from pulse_harness.plugins.posture import pack_posture_materials
from pulse_system.service.posture import PostureMemory
from pulse_system.substrate.storage import Storage


SUBJECT = "continuing-subject"
SOURCE = "work:original"
TARGET = "work:next-task"


def _experience(memory, identity, *, representation="lesson"):
    common = dict(
        experience_id=identity,
        subject_id=SUBJECT,
        context_id=SOURCE,
        title=f"Judgment {identity}",
        encounter={"situation": "PRIVATE_EPISODE_SITUATION",
                   "action": "PRIVATE_EPISODE_ACTION",
                   "outcome": "PRIVATE_EPISODE_OUTCOME"},
        source={"kind": "synthetic_fixture", "message_id": "PRIVATE_EPISODE_SOURCE"},
        record_type="constructed_fixture",
        scope="subject",
    )
    if representation == "example":
        return memory.add_experience(
            **common, representation="example", excerpt="The exact source passage.",
            note="Use this passage only when its setting fits.")
    return memory.add_experience(
        **common, lesson=f"Act on the actual purpose of {identity}.",
        conditions={"all": ["The new task matches this context",
                            {"not": "A fixed registration form was requested"}]},
        counterexamples=["A simple form may already be the complete deliverable."],
        shift={"understanding": "Determine the actual task first.",
               "candidates": "Consider a suitable next action.",
               "delivery": "Inspect the finished artifact."},
    )


def _ids(packet):
    return [record["id"] for record in packet["episodes"]]


def _one_record_budget(record):
    # pack_posture_materials adds clipped_fields to a record before counting it.
    packed = pack_posture_materials({"episodes": [record]})
    item = packed["episodes"][0]
    return len(json.dumps(item, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode())


@pytest.mark.parametrize("report", ["useful", "not_useful"])
def test_feedback_only_report_does_not_displace_newer_work_material(tmp_path, report):
    path = tmp_path / "world.db"
    storage = Storage(path)
    try:
        memory = PostureMemory(storage, "world")
        old = _experience(memory, "earlier")
        time.sleep(0.002)
        new = _experience(memory, "later")
        time.sleep(0.002)
        reported = memory.record_feedback(
            old["id"], expected_revision=1, verdict=report,
            evidence="PRIVATE_RECEIVING_WORK_REPORT",
            source={"kind": "operator_report", "page_id": TARGET},
        )
        assert reported["revision"] == 2 and reported["active"]
        # A user-facing journal still shows the latest report first.
        assert [r["id"] for r in memory.list_experiences(
            subject_id=SUBJECT, context_id=TARGET)] == ["earlier", "later"]

        material = memory.materials(subject_id=SUBJECT, context_id=TARGET)
        assert _ids(material) == ["later", "earlier"]
        assert material["episodes"][1]["feedback"] == [
            {"verdict": report, "revision": 1}]
        assert "PRIVATE_" not in json.dumps(material)
        budgeted = pack_posture_materials(
            material, byte_budget=_one_record_budget(material["episodes"][0]))
        assert [ref["id"] for ref in budgeted["offered_posture_revisions"]] == ["later"]
        assert [ref["id"] for ref in budgeted["omitted_posture_revisions"]] == ["earlier"]

        # Correcting the lesson itself, unlike appending a report, promotes the
        # new revision to the front of the actual work material.
        time.sleep(0.002)
        revised = memory.revise_experience(
            old["id"], expected_revision=2,
            changes={"lesson": "The corrected rule applies only to exploratory tasks."})
        assert revised["revision"] == 3
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET)) == [
            "earlier", "later"]
    finally:
        storage.close()

    # Attention ordering comes from the versioned World journal after restart;
    # no auxiliary cache or new authority is introduced.
    reopened = Storage(path)
    try:
        memory = PostureMemory(reopened, "world")
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET)) == [
            "earlier", "later"]
        withdrawn = memory.record_feedback(
            old["id"], expected_revision=3, verdict="withdrawn",
            evidence="This judgment was later withdrawn.",
        )
        assert not withdrawn["active"]
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET)) == [
            "later"]
        assert memory.materials(subject_id="different-subject", context_id=TARGET)["episodes"] == []
    finally:
        reopened.close()


def test_example_note_change_counts_but_outcome_report_does_not(tmp_path):
    storage = Storage(tmp_path / "examples.db")
    try:
        memory = PostureMemory(storage, "world")
        earlier = _experience(memory, "example-first", representation="example")
        time.sleep(0.002)
        _experience(memory, "example-second", representation="example")
        time.sleep(0.002)
        memory.record_feedback(
            earlier["id"], expected_revision=1, verdict="not_useful",
            evidence="The receiving task had different constraints.")
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET)) == [
            "example-second", "example-first"]
        time.sleep(0.002)
        memory.revise_experience(
            earlier["id"], expected_revision=2,
            changes={"note": "The passage only applies to the original exhibit context."})
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET)) == [
            "example-first", "example-second"]
        assert "PRIVATE_EPISODE" not in json.dumps(
            memory.materials(subject_id=SUBJECT, context_id=TARGET))
    finally:
        storage.close()
