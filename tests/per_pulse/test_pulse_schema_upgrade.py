"""Earlier bodies retain causal evidence during the index-only upgrades.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import sqlite3

import pytest

from pulse_system.core.causality.ledger import CausalLedger
from pulse_system.substrate.storage.store import Storage


NEW_INDEXES = (
    "idx_harness_turns_result_event", "idx_causal_events_world_kind_status_seq",
    "idx_context_pulse_budget", "idx_context_pulse_recent",
)
WINDOW_INDEX = "idx_causal_events_world_subject_queue_time"
QUEUE_INDEX = "idx_causal_events_world_status_seq"
MESSAGE_INDEX = "idx_causal_events_world_center_seq"


@pytest.mark.parametrize("old_version", [7, 8, 9])
def test_upgrade_preserves_history_and_uses_bounded_query_paths(tmp_path, old_version):
    path = tmp_path / "world.db"
    original = Storage(path)
    original.create_engram(engram_id="subject")
    ledger = CausalLedger(original)
    root = ledger.enqueue("world", kind="stimulus", domain="pulse", source="user",
                          engram_id="subject", content="Synthetic retained experience.")
    turn = ledger.begin_turn(root.id, "subject", root.content)
    _, result = ledger.settle_turn(turn.id, "A retained result.")
    background = ledger.enqueue("world", kind="spontaneous", domain="pulse", source="self",
                                engram_id="subject", parent_event_id=result.id,
                                metadata={"context_pulse": "reentry"})
    before_events = original._conn.execute("SELECT * FROM causal_events ORDER BY seq").fetchall()
    before_turns = original._conn.execute("SELECT * FROM harness_turns ORDER BY id").fetchall()
    original.close()
    # These upgrades are index-only. Reconstruct the earlier populated body
    # without changing its native causal rows or turn receipts.
    with sqlite3.connect(path) as previous:
        obsolete = ((NEW_INDEXES if old_version == 7 else ())
                    + ((WINDOW_INDEX, QUEUE_INDEX) if old_version < 9 else ())
                    + (MESSAGE_INDEX,))
        for name in obsolete:
            previous.execute(f"DROP INDEX {name}")
        previous.execute("DELETE FROM schema_migrations WHERE version > ?", (old_version,))
        previous.execute(f"PRAGMA user_version = {old_version}")
    reopened = Storage(path)
    try:
        migration = reopened.schema_migration_result
        assert migration.from_version == old_version
        assert migration.to_version == 10
        assert migration.applied_versions == tuple(range(old_version + 1, 11))
        assert migration.backup_path is not None
        with sqlite3.connect(migration.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == old_version
            assert backup.execute("SELECT * FROM causal_events ORDER BY seq").fetchall() == before_events
        assert reopened._conn.execute("SELECT * FROM causal_events ORDER BY seq").fetchall() == before_events
        assert reopened._conn.execute("SELECT * FROM harness_turns ORDER BY id").fetchall() == before_turns
        assert reopened._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        quota = ("SELECT COUNT(*) FROM causal_events WHERE world_id = ? "
                 "AND kind IN ('spontaneous', 'propagation') "
                 "AND (json_extract(metadata, '$.context_pulse') = 'reentry' "
                 "OR json_extract(metadata, '$.context_connection_id') IS NOT NULL) "
                 "AND julianday(created_at) >= julianday(?)")
        parameters = ("world", "2000-01-01T00:00:00+00:00")
        assert reopened._conn.execute(quota, parameters).fetchone()[0] == 1
        plan = reopened._conn.execute("EXPLAIN QUERY PLAN " + quota, parameters).fetchall()
        assert any("idx_context_pulse_budget" in row[-1] for row in plan)
        proof_plan = reopened._conn.execute(
            "EXPLAIN QUERY PLAN SELECT event_id, engram_id, state, prompt_accepted "
            "FROM harness_turns WHERE result_event_id = ?", (result.id,),
        ).fetchall()
        assert any("idx_harness_turns_result_event" in row[-1] for row in proof_plan)
        window_plan = reopened._conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM causal_events "
            "WHERE world_id = ? AND engram_id = ? AND status = 'queued' "
            "AND created_at <= ? ORDER BY created_at, seq LIMIT 501",
            ("world", "subject", background.created_at.isoformat()),
        ).fetchall()
        assert any(WINDOW_INDEX in row[-1] for row in window_plan)
        queue_plan = reopened._conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM causal_events "
            "WHERE world_id = ? AND status = 'queued' AND seq > ? ORDER BY seq LIMIT 500",
            ("world", 0),
        ).fetchall()
        assert any(QUEUE_INDEX in row[-1] for row in queue_plan)
        for center in (None, "an-existing-work-center"):
            message_plan = reopened._conn.execute(
                "EXPLAIN QUERY PLAN SELECT id, content FROM causal_events "
                "WHERE world_id = ? AND center_id IS ? AND seq < ? "
                "AND content IS NOT NULL AND kind IN ('stimulus', 'spontaneous', "
                "'propagation', 'assistant_result', 'delegation_result') "
                "ORDER BY seq DESC LIMIT 101", ("world", center, 100000),
            ).fetchall()
            assert any(MESSAGE_INDEX in row[-1] for row in message_plan)
            assert not any("TEMP B-TREE" in row[-1] for row in message_plan)
        assert CausalLedger(reopened).get_event(background.id).status.value == "queued"
    finally:
        reopened.close()
    final = Storage(path)
    try:
        assert final.schema_migration_result.applied_versions == ()
        assert final.schema_migration_result.backup_path is None
    finally:
        final.close()
