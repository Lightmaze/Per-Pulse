"""Durable growth, atomic legacy import and cross-connection revision checks.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All experience content is explicitly constructed test material.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from threading import Barrier

import pytest

from pulse_system.service.posture import CONTROL_SCHEMA, SCHEMA, PostureMemory
from pulse_system.substrate.storage import Storage
from test_posture_runtime import episode


def legacy(storage, records, *, stops=None):
    memory = PostureMemory(storage, "world")
    state = {"schema": SCHEMA, "world_id": "world", "experiences": {row["id"]: row for row in records}}
    storage.save_component_state(memory.component, state)
    if stops:
        storage.save_component_state(memory.control_component,
                                     {"schema": CONTROL_SCHEMA, "world_id": "world", "stops": stops})
    return memory, state


def test_growth_beyond_lifetime_count_and_aggregate_bytes_survives_restart(tmp_path):
    path = tmp_path / "world.db"
    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    encounter = {key: "界" * 4000 for key in ("situation", "action", "outcome")}
    try:
        for index in range(132):
            memory.add_experience(**episode(experience_id=f"long-{index}", encounter=encounter))
        logical_bytes = storage._conn.execute("SELECT SUM(length(CAST(record AS BLOB))) FROM posture_experiences").fetchone()[0]
        assert logical_bytes > 4 * 1024 * 1024
        paused = memory.record_feedback("long-0", expected_revision=1, verdict="harmful", evidence="Keep this correction.")
        corrected = memory.revise_experience("long-1", expected_revision=1, changes={"lesson": "A later complete correction."})
        for index in range(132, 260):
            memory.add_experience(**episode(experience_id=f"long-{index}"))
        all_records = memory.list_experiences(subject_id="subject", include_inactive=True)
        assert len(all_records) == 260
        by_id = {value["id"]: value for value in all_records}
        assert by_id["long-0"] == paused
        assert by_id["long-1"] == corrected
        assert len(memory.list_experiences(subject_id="subject")) == 259
        # The ordinary material read must not issue two SQL queries *per*
        # experience, or scan revision rows, as the lifelong journal grows.
        statements = []
        storage._conn.set_trace_callback(statements.append)
        try:
            packet = memory.materials(subject_id="subject", context_id="origin", prioritize_content=True)
        finally:
            storage._conn.set_trace_callback(None)
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        assert len(selects) <= 4, selects
        assert all("posture_experience_revisions" not in sql for sql in statements)
        assert len(packet["episodes"]) == 259
        assert "long-0" not in {item["id"] for item in packet["episodes"]}
        assert PostureMemory(storage, "other-world").list_experiences(subject_id="subject") == []
        storage.close()
        storage = Storage(path)
        memory = PostureMemory(storage, "world")
        assert memory.get_experience("long-0") == paused
        assert memory.get_experience("long-1") == corrected
        assert memory.get_experience("long-259")["source"]["kind"] == "synthetic_fixture"
        resumed = memory.revise_experience("long-0", expected_revision=2, changes={"active": True})
        assert resumed["feedback"] == paused["feedback"] and resumed["history"][-1]["active"] is False
        with pytest.raises(ValueError, match="revision conflict"):
            memory.revise_experience("long-0", expected_revision=2, changes={"active": False})
    finally:
        storage.close()


def test_many_revisions_store_feedback_once_and_preserve_each_snapshot(tmp_path):
    storage = Storage(tmp_path / "world.db")
    memory = PostureMemory(storage, "world")
    try:
        current = memory.add_experience(**episode(experience_id="revisited"))
        versions = [deepcopy(current)]
        for index in range(140):
            current = memory.record_feedback("revisited", expected_revision=current["revision"],
                                             verdict="not_useful", evidence="A separate observation " + str(index))
            versions.append(deepcopy(current))
        actual = memory.get_experience("revisited")
        assert actual == current
        for previous, expected in zip(actual["history"], versions):
            assert previous == {key: value for key, value in expected.items() if key != "history"}
        assert storage._conn.execute("SELECT COUNT(*) FROM posture_experience_feedback").fetchone()[0] == 140
        assert storage._conn.execute("SELECT COUNT(*) FROM posture_experience_revisions").fetchone()[0] == 140
        assert all('"feedback"' not in row[0] for row in storage._conn.execute("SELECT record FROM posture_experience_revisions"))
    finally:
        storage.close()


def test_legacy_stop_controls_and_history_import_once_without_rewriting_source(tmp_path):
    path = tmp_path / "world.db"
    storage = Storage(path)
    first = PostureMemory.validate_experience(**episode(experience_id="old"))
    second = deepcopy(first)
    PostureMemory._advance(second)
    second["lesson"] = "The old deliberate correction."
    feedback = {"id": "old-feedback", "revision": 2, "verdict": "withdrawn", "evidence": "Old stop.",
                "source": {"kind": "operator_report"}, "created_at": "2026-11-01"}
    control = {"id": "old", "base_revision": 2, "revision": 3, "updated_at": "2026-11-01", "feedback": feedback}
    memory, state = legacy(storage, [second], stops={"old": control})
    before = storage._conn.execute("SELECT * FROM component_state ORDER BY component").fetchall()
    try:
        stopped = memory.get_experience("old")
        assert stopped["revision"] == 3 and not stopped["active"]
        assert stopped["history"][0] == {key: value for key, value in first.items() if key != "history"}
        assert storage._conn.execute("SELECT COUNT(*) FROM posture_journals").fetchone()[0] == 0
        new = memory.add_experience(**episode(experience_id="new"))
        assert memory.get_experience("old") == stopped
        assert storage._conn.execute("SELECT * FROM component_state ORDER BY component").fetchall() == before
        resumed = memory.revise_experience("old", expected_revision=3, changes={"active": True})
        # The obsolete source cannot become authoritative again after import.
        storage.save_component_state(memory.component, {**state, "experiences": {}})
        storage.close()
        storage = Storage(path)
        memory = PostureMemory(storage, "world")
        assert memory.get_experience("old") == resumed
        assert memory.get_experience("new") == new
    finally:
        storage.close()


def test_import_and_requested_mutation_roll_back_together_then_retry():
    storage = Storage()
    record = PostureMemory.validate_experience(**episode(experience_id="old"))
    memory, state = legacy(storage, [record])
    try:
        storage._conn.execute("CREATE TRIGGER reject_new_experience BEFORE INSERT ON posture_experiences "
                              "WHEN NEW.experience_id='new' BEGIN SELECT RAISE(ABORT, 'test write fault'); END")
        storage._conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="test write fault"):
            memory.add_experience(**episode(experience_id="new"))
        assert storage.load_component_state(memory.component) == state
        assert storage._conn.execute("SELECT COUNT(*) FROM posture_experiences").fetchone()[0] == 0
        assert storage._conn.execute("SELECT COUNT(*) FROM posture_journals").fetchone()[0] == 0
        assert memory.get_experience("old") == record
        storage._conn.execute("DROP TRIGGER reject_new_experience")
        storage._conn.commit()
        memory.add_experience(**episode(experience_id="new"))
        assert len(memory.list_experiences(subject_id="subject")) == 2
    finally:
        storage.close()


def test_distinct_connections_cannot_lose_a_revision_or_duplicate_an_import(tmp_path):
    path = tmp_path / "world.db"
    one = Storage(path)
    record = PostureMemory.validate_experience(**episode(experience_id="same"))
    first, _ = legacy(one, [record])
    two = Storage(path)
    second = PostureMemory(two, "world")
    barrier = Barrier(2)
    def edit(memory, title):
        barrier.wait(timeout=5)
        try:
            return memory.revise_experience("same", expected_revision=1, changes={"title": title})
        except ValueError as error:
            assert "revision conflict" in str(error)
            return None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(edit, first, "One"), pool.submit(edit, second, "Two")]
            results = [future.result(timeout=10) for future in futures]
        assert sum(result is not None for result in results) == 1
        assert first.get_experience("same") == second.get_experience("same")
        assert len(first.get_experience("same")["history"]) == 1
        assert one._conn.execute("SELECT COUNT(*) FROM posture_journals").fetchone()[0] == 1
    finally:
        one.close()
        two.close()


def test_v12_schema_upgrade_backs_up_original_components_before_any_import(tmp_path):
    path = tmp_path / "world.db"
    storage = Storage(path)
    record = PostureMemory.validate_experience(**episode(experience_id="old-schema"))
    memory, state = legacy(storage, [record])
    original = storage._conn.execute("SELECT * FROM component_state ORDER BY component").fetchall()
    storage.close()
    with sqlite3.connect(path) as old:
        for table in ("posture_experience_feedback", "posture_experience_revisions", "posture_experiences", "posture_journals"):
            old.execute(f"DROP TABLE {table}")
        old.execute("DELETE FROM schema_migrations WHERE version=13")
        old.execute("PRAGMA user_version=12")
    storage = Storage(path)
    try:
        receipt = storage.schema_migration_result
        assert (receipt.from_version, receipt.to_version, receipt.applied_versions) == (12, 13, (13,))
        with sqlite3.connect(receipt.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 12
            assert backup.execute("SELECT * FROM component_state ORDER BY component").fetchall() == original
        assert PostureMemory(storage, "world").get_experience(record["id"]) == record
        assert storage.load_component_state(memory.component) == state
        assert storage._conn.execute("SELECT COUNT(*) FROM posture_journals").fetchone()[0] == 0
    finally:
        storage.close()


def test_bounded_model_materials_match_full_packet_without_decoding_old_bodies(tmp_path):
    """A growing World still offers the same bounded prompt without loading every body.

    The omitted refs remain exact for revision checks; inaccessible material
    must not appear even in IDs. The user-facing native list stays unbounded.
    """
    from pulse_harness.plugins.posture import pack_posture_materials

    path = tmp_path / "many.db"
    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    try:
        for index in range(180):
            memory.add_experience(**episode(
                experience_id=f"experience-{index:03d}", scope="subject",
                lesson=f"Use a concrete next action for case {index}.",
            ))
        memory.add_experience(**episode(
            experience_id="other-subject", subject_id="stranger", scope="subject"))
        memory.add_experience(**episode(
            experience_id="other-context", context_id="private", scope="context"))
        hidden = memory.add_experience(**episode(
            experience_id="inactive", scope="subject"))
        memory.record_feedback(hidden["id"], expected_revision=1,
                               verdict="withdrawn", evidence="No longer applicable.")
        first = memory.get_experience("experience-000")
        memory.record_feedback(first["id"], expected_revision=1,
                               verdict="not_useful", evidence="A later report without new content.")

        full = memory.materials(subject_id="subject", context_id="origin", prioritize_content=True)
        expected = pack_posture_materials(full)
        assert len(full["episodes"]) == 180
        trace = []
        storage._conn.set_trace_callback(trace.append)
        try:
            bounded = memory.materials(
                subject_id="subject", context_id="origin",
                prioritize_content=True, max_records=128)
        finally:
            storage._conn.set_trace_callback(None)
        packet = pack_posture_materials(bounded)
        assert len(bounded["episodes"]) == 128
        assert len(bounded["omitted_refs"]) == 52
        assert packet == expected
        assert packet["eligible_count"] == 180
        assert set(item["id"] for item in bounded["omitted_refs"]).isdisjoint(
            {"other-subject", "other-context", "inactive"})
        assert all("PRIVATE_" not in json.dumps(ref) for ref in bounded["omitted_refs"])
        queries = [sql for sql in trace if sql.lstrip().upper().startswith("SELECT")]
        assert any("ORDER BY e.content_updated_at DESC" in sql and "LIMIT 128" in sql for sql in queries)
        assert any("json_extract(e.record, '$.revision')" in sql for sql in queries)
        assert not any("posture_experience_revisions" in sql for sql in queries)
        assert len(memory.list_experiences(subject_id="subject")) == 181
        storage.close()

        storage = Storage(path)
        memory = PostureMemory(storage, "world")
        after_restart = memory.materials(
            subject_id="subject", context_id="origin",
            prioritize_content=True, max_records=128)
        assert pack_posture_materials(after_restart) == expected
    finally:
        storage.close()


def test_bounded_legacy_materials_do_not_migrate_on_read():
    """A pre-journal World can read a bounded candidate view without a write."""
    from pulse_harness.plugins.posture import pack_posture_materials

    storage = Storage()
    records = [PostureMemory.validate_experience(**episode(
        experience_id=f"legacy-{index:03d}", scope="subject"))
        for index in range(145)]
    memory, original = legacy(storage, records)
    try:
        before = storage.load_component_state(memory.component)
        full = pack_posture_materials(memory.materials(
            subject_id="subject", context_id="origin", prioritize_content=True))
        bounded = pack_posture_materials(memory.materials(
            subject_id="subject", context_id="origin",
            prioritize_content=True, max_records=128))
        assert bounded == full
        assert storage.load_component_state(memory.component) == before == original
        assert storage._conn.execute("SELECT COUNT(*) FROM posture_journals").fetchone()[0] == 0
    finally:
        storage.close()



def test_compact_historical_experience_can_fill_remaining_model_packet(tmp_path):
    """A long-lived World should not starve every older short lesson.

    The native read still decodes a fixed maximum of 144 full bodies; the
    other references survive for stale-decision checks. This is a fit-based
    reserve, not a relevance assertion or model effectiveness result.
    """
    from pulse_harness.plugins.posture import pack_posture_materials

    path = tmp_path / "compact-backfill.db"
    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    try:
        for index in range(10):
            memory.add_experience(**episode(
                experience_id=f"compact-{index:02d}", scope="subject",
                lesson=f"An older compact but actionable lesson {index}."))
        oversized = {key: char * 2600
                     for key, char in (("situation", "S"), ("action", "A"), ("outcome", "O"))}
        for index in range(140):
            memory.add_experience(**episode(
                experience_id=f"large-new-{index:03d}", scope="subject",
                encounter=oversized))
        memory.add_experience(**episode(
            experience_id="other-subject", subject_id="unrelated", scope="subject"))
        memory.add_experience(**episode(
            experience_id="private-context", context_id="private", scope="context"))
        withdrawn = memory.add_experience(**episode(
            experience_id="withdrawn", scope="subject"))
        memory.record_feedback(withdrawn["id"], expected_revision=1,
                               verdict="withdrawn", evidence="No longer applicable.")

        ordinary = memory.materials(
            subject_id="subject", context_id="origin",
            prioritize_content=True, max_records=128)
        backfilled = memory.materials(
            subject_id="subject", context_id="origin",
            prioritize_content=True, max_records=128, compact_backfill=16)
        full = memory.materials(
            subject_id="subject", context_id="origin", prioritize_content=True)
        assert len(ordinary["episodes"]) == 128
        assert len(backfilled["episodes"]) == 138
        assert len(backfilled["omitted_refs"]) == 12
        assert all(not item["id"].startswith("compact-") for item in ordinary["episodes"])
        assert all(any(item["id"] == f"compact-{i:02d}" for item in backfilled["episodes"])
                   for i in range(10))
        assert all(ref["id"] not in {"other-subject", "private-context", "withdrawn"}
                   for ref in backfilled["omitted_refs"])
        offered = lambda materials: [
            ref["id"] for ref in pack_posture_materials(materials)["offered_posture_revisions"]]
        assert any(identity.startswith("compact-") for identity in offered(backfilled))
        assert offered(backfilled) == offered(full)
        assert len(pack_posture_materials(backfilled)["omitted_posture_revisions"]) == (
            len(full["episodes"]) - len(offered(backfilled)))
        storage.close()

        storage = Storage(path)
        memory = PostureMemory(storage, "world")
        reopened = memory.materials(
            subject_id="subject", context_id="origin",
            prioritize_content=True, max_records=128, compact_backfill=16)
        assert offered(reopened) == offered(full)
        with pytest.raises(ValueError, match="compact backfill"):
            memory.materials(subject_id="subject", context_id="origin", compact_backfill=17)
    finally:
        storage.close()
