"""Work-material attention follows substantive experience changes, not mere report recency.

Real persistent World components and bounded material packing are exercised.
Source episodes are explicit constructed fixtures, not provider observations.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import time

import pytest

from pulse_harness.core import HarnessScope
from pulse_harness.plugins.posture import pack_posture_materials
from pulse_system.service.expert_computation import ExpertRuntime
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

        # Native retrieval still exposes last-journal-update recency.
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET)) == [
            "earlier", "later"]
        material = memory.materials(subject_id=SUBJECT, context_id=TARGET, prioritize_content=True)
        assert _ids(material) == ["later", "earlier"]

        # The existing expert's pre-action material path consumes this order,
        # rather than a custom test-only selector or separate data store.
        expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None,
                               posture_memory=memory, expert_active=False)
        try:
            scope = HarnessScope("world", SUBJECT, "session", 0, "owner",
                                 context_id=TARGET, turn_id="next")
            assert _ids(expert.posture_materials(scope)) == ["later", "earlier"]
        finally:
            expert.close()
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
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET, prioritize_content=True)) == [
            "earlier", "later"]
    finally:
        storage.close()

    # Attention ordering comes from the versioned World journal after restart;
    # no auxiliary cache or new authority is introduced.
    reopened = Storage(path)
    try:
        memory = PostureMemory(reopened, "world")
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET, prioritize_content=True)) == [
            "earlier", "later"]
        withdrawn = memory.record_feedback(
            old["id"], expected_revision=3, verdict="withdrawn",
            evidence="This judgment was later withdrawn.",
        )
        assert not withdrawn["active"]
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET, prioritize_content=True)) == [
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
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET, prioritize_content=True)) == [
            "example-second", "example-first"]
        time.sleep(0.002)
        memory.revise_experience(
            earlier["id"], expected_revision=2,
            changes={"note": "The passage only applies to the original exhibit context."})
        assert _ids(memory.materials(subject_id=SUBJECT, context_id=TARGET, prioritize_content=True)) == [
            "example-first", "example-second"]
        assert "PRIVATE_EPISODE" not in json.dumps(
            memory.materials(subject_id=SUBJECT, context_id=TARGET, prioritize_content=True))
    finally:
        storage.close()


def test_old_task_matched_experience_reaches_ordinary_preaction_work_after_restart(
        tmp_path, monkeypatch):
    """Old shared experience is actually offered before Work B's next decision.

    The transport explicitly scripts PASS; this proves native retrieval and
    model-request material, not a model's independent tool choice.
    """
    from pulse_harness.core import HarnessStore, TurnRunner, TurnStatus
    from test_posture_runtime import NoTools, model_fixture

    path = tmp_path / "world.db"
    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    old = _experience(memory, "provenance-manifest-original")
    for index in range(180):
        _experience(memory, f"routine-{index:03d}")
    without_task = memory.materials(
        subject_id=SUBJECT, context_id=TARGET, prioritize_content=True,
        max_records=128, compact_backfill=16,
    )
    assert old["id"] not in _ids(without_task)
    storage.close()

    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None,
                           posture_memory=memory, expert_active=False)
    kernel = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", SUBJECT, "session", 0, "owner",
                         context_id=TARGET, turn_id="work-b")
    requests = []
    model = model_fixture(monkeypatch, requests)
    runner = TurnRunner(kernel, model_adapter=model, tool_provider=NoTools(),
                        turn_advisor=expert.advisor(kernel, model, scope))
    try:
        outcome = runner.run(
            scope, prompt="Create a provenance manifest for the final dataset, "
                          "with original source attribution in the artifact.",
        )
        assert outcome.status is TurnStatus.SUCCEEDED
        preaction = next(json.dumps(item["messages"], ensure_ascii=False)
                         for item in requests
                         if "WORK_MATERIAL_JSON" in json.dumps(item["messages"]))
        assert old["id"] in preaction
        assert "PRIVATE_EPISODE" not in preaction
        packet = expert.posture_materials(
            scope, task_terms=("provenance", "manifest"))
        assert packet["offered_posture_revisions"][0] == {
            "id": old["id"], "revision": 1}
        assert packet["semantic_applicability"] == "not_yet_assessed"
        # A subject-local but unshared record is not a candidate in Work B.
        assert memory.materials(
            subject_id="other-subject", context_id=TARGET,
            prioritize_content=True, max_records=128,
            task_terms=("provenance", "manifest"))["episodes"] == []
    finally:
        runner.close()
        kernel.close()
        expert.close()
        storage.close()

    reopened = Storage(path)
    try:
        memory = PostureMemory(reopened, "world")
        memory.record_feedback(
            old["id"], expected_revision=1, verdict="withdrawn",
            evidence="The original instruction no longer applies.")
        packet = memory.materials(
            subject_id=SUBJECT, context_id=TARGET, prioritize_content=True,
            max_records=128, compact_backfill=16,
            task_terms=("provenance", "manifest"))
        assert old["id"] not in _ids(packet)
        assert old["id"] not in {ref["id"] for ref in packet["omitted_refs"]}
    finally:
        reopened.close()



def test_chinese_work_b_recalls_old_shared_source_before_decision_after_restart(
        tmp_path, monkeypatch):
    """Ordinary unspaced Chinese Work requests can see older transferable lessons.

    Synthetic HTTP models expose the actual pre-action prompt. Native World,
    SQLite restart, subject privacy and experience withdrawal are real.
    """
    from types import SimpleNamespace

    from pulse_harness.core import HarnessStore, TurnRunner, TurnStatus
    from pulse_system.service.expert_computation import _task_terms
    from test_posture_runtime import NoTools, model_fixture

    prompt = "请制作来源清单，并将原始资料的链接写入最终成果。"
    cues = _task_terms(SimpleNamespace(messages=[
        {"role": "tool", "content": "PRIVATE_TOOL_SECRET"},
        {"role": "user", "content": prompt},
    ]))
    assert "来源清单" in cues and "原始资料" in cues
    assert "PRIVATE" not in str(cues)
    mixed = _task_terms(SimpleNamespace(messages=[
        {"role": "user", "content":
         "请为最终报告生成来源清单，请保留原始链接。 Generate a provenance manifest."},
    ]))
    assert "来源清单" in mixed
    assert "provenance" in mixed

    path = tmp_path / "chinese-world.db"
    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    old = _experience(memory, "earlier-chinese-file-source")
    memory.revise_experience(
        old["id"], expected_revision=1,
        changes={
            "title": "来源清单需要原始资料与真实链接",
            "lesson": "制作来源清单时保留原始资料链接与可核查的文件收据。",
        },
    )
    for index in range(180):
        _experience(memory, f"chinese-routine-{index:03d}")
    without_task = memory.materials(
        subject_id=SUBJECT, context_id=TARGET, prioritize_content=True,
        max_records=128, compact_backfill=16)
    assert old["id"] not in _ids(without_task)
    storage.close()

    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    expert = ExpertRuntime(tmp_path / "chinese-expert.db", guard=lambda: None,
                           posture_memory=memory, expert_active=False)
    kernel = HarnessStore(tmp_path / "chinese-kernel.db")
    scope = HarnessScope("world", SUBJECT, "session", 0, "owner",
                         context_id=TARGET, turn_id="chinese-work-b")
    requests = []
    model = model_fixture(monkeypatch, requests)
    runner = TurnRunner(kernel, model_adapter=model, tool_provider=NoTools(),
                        turn_advisor=expert.advisor(kernel, model, scope))
    try:
        outcome = runner.run(scope, prompt=prompt)
        assert outcome.status is TurnStatus.SUCCEEDED
        preaction = next(json.dumps(item["messages"], ensure_ascii=False)
                         for item in requests
                         if "WORK_MATERIAL_JSON" in json.dumps(item["messages"]))
        assert old["id"] in preaction
        assert "制作来源清单时保留原始资料链接" in preaction
        assert "PRIVATE_EPISODE" not in preaction
        packet = expert.posture_materials(scope, task_terms=cues)
        assert packet["offered_posture_revisions"][0] == {
            "id": old["id"], "revision": 2}
        assert memory.materials(
            subject_id="other-subject", context_id=TARGET, max_records=128,
            compact_backfill=16, task_terms=cues)["episodes"] == []
    finally:
        runner.close()
        kernel.close()
        expert.close()
        storage.close()

    reopened = Storage(path)
    try:
        memory = PostureMemory(reopened, "world")
        memory.record_feedback(
            old["id"], expected_revision=2, verdict="withdrawn",
            evidence="This Chinese source rule has been retired.")
        packet = memory.materials(
            subject_id=SUBJECT, context_id=TARGET, prioritize_content=True,
            max_records=128, compact_backfill=16, task_terms=cues)
        assert old["id"] not in _ids(packet)
        assert old["id"] not in {ref["id"] for ref in packet["omitted_refs"]}
    finally:
        reopened.close()


def test_old_cue_rescued_experience_changes_ordinary_work_admission_and_survives_restart(
        tmp_path, monkeypatch):
    """An old Work A lesson reaches Work B's *main* decision, not only its fork.

    The model is explicitly scripted; this validates the native admission,
    durable continuation, exact-source revocation, and privacy boundaries.
    """
    from pulse_harness.core import HarnessStore, TurnRunner, TurnStatus
    from pulse_harness.core.turn_advice import TurnAdvice
    from test_posture_runtime import NoTools, model_fixture

    world_path = tmp_path / "world.db"
    storage = Storage(world_path)
    memory = PostureMemory(storage, "world")
    old = _experience(memory, "provenance-manifest-original")
    for index in range(180):
        _experience(memory, f"routine-{index:03d}")
    storage.close()

    storage = Storage(world_path)
    memory = PostureMemory(storage, "world")
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None,
                           posture_memory=memory, expert_active=False)
    kernel = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", SUBJECT, "session", 0, "owner",
                         context_id=TARGET, turn_id="ordinary-b")
    requests = []
    model = model_fixture(monkeypatch, requests, all_contributions=True)
    runner = TurnRunner(kernel, model_adapter=model, tool_provider=NoTools(),
                        turn_advisor=expert.advisor(kernel, model, scope))
    advice_text = "Use the direct worked example already available before the setup form."
    original_ref = {"id": old["id"], "revision": 1}
    try:
        assert old["id"] not in _ids(expert.posture_materials(scope))
        outcome = runner.run(
            scope, prompt="Create a provenance manifest for the final dataset "
                          "with original source attribution in the artifact.")
        assert outcome.status is TurnStatus.SUCCEEDED
        assert len(requests) == 2
        fork = next(json.dumps(r["messages"], ensure_ascii=False) for r in requests
                    if "WORK_MATERIAL_JSON" in json.dumps(r["messages"]))
        main = next(json.dumps(r["messages"], ensure_ascii=False) for r in requests
                    if "WORK_MATERIAL_JSON" not in json.dumps(r["messages"]))
        assert old["id"] in fork
        assert advice_text in main
        assert "PRIVATE_EPISODE" not in fork + main
        expert.record_outcome(kernel, scope, outcome)
        previous = expert.work_materials(scope, expert.posture_materials(scope))
        assert any(advice_text in (record["context"] or "")
                   for record in previous["previous_computations"])
        assert expert.current_advice(
            scope, TurnAdvice(context=advice_text,
                              receipt={"offered_posture_revisions": [original_ref]})).context == advice_text
    finally:
        runner.close()
        kernel.close()
        expert.close()
        storage.close()

    storage = Storage(world_path)
    memory = PostureMemory(storage, "world")
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None,
                           posture_memory=memory, expert_active=False)
    try:
        # The exact older source remains useful even without cue-based rescue.
        assert old["id"] not in _ids(expert.posture_materials(scope))
        restored = expert.work_materials(scope, expert.posture_materials(scope))
        assert any(advice_text in (record["context"] or "")
                   for record in restored["previous_computations"])
        foreign = HarnessScope("world", "different-subject", "other", 0, "owner",
                               context_id=TARGET, turn_id="next")
        assert expert.current_advice(
            foreign, TurnAdvice(context=advice_text,
                                receipt={"offered_posture_revisions": [original_ref]})).context is None
        memory.record_feedback(old["id"], expected_revision=1,
                               verdict="withdrawn", evidence="No longer applicable.")
        suppressed = expert.current_advice(
            scope, TurnAdvice(context=advice_text,
                              receipt={"offered_posture_revisions": [original_ref]}))
        assert suppressed.context is None
        assert suppressed.receipt["reason"] == "posture_revision_no_longer_eligible"
        after = expert.work_materials(scope, expert.posture_materials(scope))
        assert not any(advice_text in (record["context"] or "")
                       for record in after["previous_computations"])
    finally:
        expert.close()
        storage.close()
