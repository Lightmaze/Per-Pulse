"""Version-seven bodies retain causal evidence during the index-only upgrade.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import sqlite3

from pulse_system.core.causality.ledger import CausalLedger
from pulse_system.substrate.storage.store import Storage


NEW_INDEXES = (
    "idx_harness_turns_result_event", "idx_causal_events_world_kind_status_seq",
    "idx_context_pulse_budget", "idx_context_pulse_recent",
)


def test_version_seven_upgrade_preserves_history_and_uses_bounded_query_paths(tmp_path):
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
    # Version eight is index-only. Removing precisely that delta reconstructs
    # a populated version-seven body without changing its native causal rows.
    with sqlite3.connect(path) as previous:
        for name in NEW_INDEXES:
            previous.execute(f"DROP INDEX {name}")
        previous.execute("DELETE FROM schema_migrations WHERE version = 8")
        previous.execute("PRAGMA user_version = 7")
    reopened = Storage(path)
    try:
        migration = reopened.schema_migration_result
        assert migration.from_version == 7
        assert migration.to_version == 8
        assert migration.applied_versions == (8,)
        assert migration.backup_path is not None
        with sqlite3.connect(migration.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 7
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
        assert CausalLedger(reopened).get_event(background.id).status.value == "queued"
    finally:
        reopened.close()
    final = Storage(path)
    try:
        assert final.schema_migration_result.applied_versions == ()
        assert final.schema_migration_result.backup_path is None
    finally:
        final.close()
