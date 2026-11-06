"""Native origin lookups stay indexed as completed consultations accumulate.

World, Kernel, SQLite and cold Images are real; model replies are an explicit
synthetic HTTP fixture. Copyright 2026 Lightmaze Contributors. Apache-2.0.
"""
import os
import sqlite3

import pytest

from per_pulse.service import PageService, PulseSettings
from pulse_harness.core.errors import ProviderUnavailable
from pulse_system.product import image
from pulse_system.service.weekly_requests import world_source_for_event
from pulse_system.substrate.storage import Storage
from test_internal_work_organization import WorkFixture, run_event


INDEX = "idx_native_team_origin"


def exact_lookup(storage, *, subject="subject", event="wanted", pending=False, limit=1):
    return storage.native_team_requests(subject=subject, delegation_event_id=event,
                                        pending=pending, limit=limit)


def test_exact_origin_lookup_uses_index_without_scanning_completed_requests(tmp_path):
    storage = Storage(tmp_path / "world.db")
    try:
        storage.save_component_state("native-team-v1:wanted", {
            "subject": "subject", "delegation_event_id": "wanted", "status": "delivered"})
        queries = []
        storage._conn.set_trace_callback(queries.append)
        assert exact_lookup(storage)[0]["delegation_event_id"] == "wanted"
        storage._conn.set_trace_callback(None)
        query, = [sql for sql in queries if sql.startswith("SELECT state FROM component_state")]
        plan = storage._conn.execute("EXPLAIN QUERY PLAN " + query).fetchall()
        assert any(INDEX in row[-1] for row in plan), plan
        assert not any("TEMP B-TREE" in row[-1] for row in plan), plan

        def instructions():
            count = [0]
            def progress():
                count[0] += 1
                return 0
            storage._conn.set_progress_handler(progress, 1)
            try:
                assert exact_lookup(storage)[0]["delegation_event_id"] == "wanted"
            finally:
                storage._conn.set_progress_handler(None, 0)
            return count[0]

        small = instructions()
        for index in range(200):
            storage.save_component_state(f"native-team-v1:completed-{index}", {
                "subject": "subject", "delegation_event_id": f"completed-{index}",
                "status": "delivered"})
        # B-tree navigation may change; walking the 200 old JSON records may not.
        assert instructions() <= small + 32
    finally:
        storage.close()


def test_precise_origin_preserves_order_limits_and_generic_listing(tmp_path):
    storage = Storage(tmp_path / "world.db")
    try:
        for component, subject, event, status, updated in (
            ("native-team-v1:a", "subject", "wanted", "delivered", "2026-11-01"),
            ("native-team-v1:b", "subject", "wanted", "prepared", "2026-11-02"),
            ("native-team-v1:c", "subject", "wanted", "prepared", "2026-11-02"),
            ("native-team-v1:foreign", "other", "wanted", "prepared", "2026-11-03"),
        ):
            storage.save_component_state(component, {
                "component": component, "subject": subject,
                "delegation_event_id": event, "status": status})
            storage._conn.execute("UPDATE component_state SET updated_at=? WHERE component=?",
                                  (updated, component))
        storage._conn.commit()
        generic = storage.native_team_requests(subject="subject", limit=128)
        pending = storage.native_team_requests(subject="subject", pending=True, limit=128)
        assert exact_lookup(storage) == generic[:1]
        assert exact_lookup(storage, limit=128) == generic
        assert exact_lookup(storage, pending=True, limit=128) == pending
        assert exact_lookup(storage, subject=None, limit=128) == storage.native_team_requests(limit=128)
        assert exact_lookup(storage, subject="missing") == []
        assert exact_lookup(storage, event="missing") == []
    finally:
        storage.close()


def as_version_11(path):
    # Reconstruct v11's exact schema from the populated real World: v12 only
    # adds this index and migration receipt, so no business row is rewritten.
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP INDEX IF EXISTS {INDEX}")
        connection.execute("DELETE FROM schema_migrations WHERE version > 11")
        connection.execute("PRAGMA user_version = 11")


def test_v11_migration_keeps_invalid_json_and_valid_outbox_bytes(tmp_path):
    path = tmp_path / "world.db"
    original = Storage(path)
    original.save_component_state("native-team-v1:wanted", {
        "subject": "subject", "delegation_event_id": "wanted", "status": "delivered"})
    original._conn.executemany("INSERT INTO component_state VALUES (?,?,?)", [
        ("native-team-v1:invalid", "not-json", "2026-11-01"),
        ("native-team-v1:missing", "{}", "2026-11-01"),
        ("native-team-v1:scalar", '"not an origin"', "2026-11-01"),
        ("unrelated-invalid", "[broken", "2026-11-01"),
    ])
    original._conn.commit()
    before = original._conn.execute("SELECT * FROM component_state ORDER BY component").fetchall()
    original.close()
    as_version_11(path)
    migrated = Storage(path)
    try:
        receipt = migrated.schema_migration_result
        assert (receipt.from_version, receipt.to_version, receipt.applied_versions) == (11, 12, (12,))
        assert receipt.backup_path is not None
        with sqlite3.connect(receipt.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 11
            assert backup.execute("SELECT * FROM component_state ORDER BY component").fetchall() == before
            assert backup.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert migrated._conn.execute("SELECT * FROM component_state ORDER BY component").fetchall() == before
        assert exact_lookup(migrated)[0]["status"] == "delivered"
        assert exact_lookup(migrated, event="unverified") == []
        assert migrated._conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert migrated._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # Generic enumeration retains its existing malformed-JSON behavior;
        # the origin optimization does not silently hide an outbox corruption.
        with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
            migrated.native_team_requests(pending=True)
    finally:
        migrated.close()
    reopened = Storage(path)
    try:
        assert reopened.schema_migration_result.applied_versions == ()
        assert reopened.schema_migration_result.backup_path is None
        assert exact_lookup(reopened)[0]["status"] == "delivered"
    finally:
        reopened.close()


@pytest.mark.skipif(os.name != "posix", reason="cold Image requires POSIX")
@pytest.mark.parametrize("autonomous", [False, True], ids=["user", "self"])
def test_native_origin_survives_upgrade_image_restore_and_rejects_unverified_records(
    tmp_path, monkeypatch, autonomous,
):
    fixture = WorkFixture(monkeypatch.setenv)
    source = tmp_path / "source"
    settings = PulseSettings(enabled=True, spontaneous_rate=0,
                             adaptive_connections=False, spectrum_enabled=False)

    def open_pages(path):
        return PageService(path, model_factory=fixture.factory, posture=False,
                           allow_workspace_actions=True, pulse=settings)

    pages = open_pages(source)
    runtime = pages.runtime
    try:
        created = pages.create_page("Native origin", "Synthetic initial instruction.")
        run_event(runtime, runtime.causal_ledger.get_event(created["initialMessageId"]))
        work = created["page"]["id"]
        center = runtime.world.get_task_front(work).center_id
        expert = runtime.engrams.create(auto_name=False).id
        runtime.storage.add_center_membership(center, expert)
        fixture.plan = [("team", {"op": "consult", "expert_id": expert,
                                  "instruction": "Synthetic source-bound consultation."})]
        if autonomous:
            trigger = runtime.causal_ledger.enqueue(
                runtime.world_id, None, "pulse", "spontaneous", "self",
                "Synthetic unattended consultation.", engram_id=runtime.continuity_engram_id,
                center_id=center, runtime_fence=runtime._current_runtime_fence())
        else:
            sent = pages.send(work, "Synthetic user consultation.")
            trigger = runtime.causal_ledger.get_event(sent["messageId"])
        run_event(runtime, trigger)
        assert fixture.outcomes[-1]["status"] == "succeeded", fixture.outcomes
        key = fixture.outcomes[-1]["output"]["request_id"]
        runtime._native_team.drain()
        record = runtime.storage.load_component_state(key)
        request_id = record["delegation_event_id"]
        fixture.plan = []
        run_event(runtime, runtime.causal_ledger.get_event(request_id))
        returned = runtime.delegation_tunnel.reconcile(request_id).delivery_event
        run_event(runtime, returned)
        origin = trigger.id if autonomous else None
        retained_world = runtime.world_id
    finally:
        runtime.close()

    as_version_11(source / "world.db")
    before = sqlite3.connect(source / "world.db")
    causal_rows = before.execute("SELECT * FROM causal_events ORDER BY seq").fetchall()
    turn_rows = before.execute("SELECT * FROM harness_turns ORDER BY id").fetchall()
    before.close()
    archive = tmp_path / "native-origin.zip"
    image.save(source, archive)
    restored = tmp_path / "restored"
    image.restore(archive, restored)
    for home in (restored, source):
        reopened = open_pages(home)
        runtime = reopened.runtime
        try:
            assert runtime.world_id == retained_world
            migration = runtime.storage.schema_migration_result
            assert migration.applied_versions == (12,)
            assert runtime.storage._conn.execute("SELECT * FROM causal_events ORDER BY seq").fetchall() == causal_rows
            assert runtime.storage._conn.execute("SELECT * FROM harness_turns ORDER BY id").fetchall() == turn_rows
            assert runtime.storage.load_component_state(key) == record
            resolve = lambda: world_source_for_event(
                runtime.causal_ledger, runtime.world_id, returned.id,
                origin_for_event=lambda event: runtime.native_team_request_origin(event.id))
            assert resolve() == origin
            for invalid in (None, {**record, "world_id": "foreign"},
                            {**record, "source_turn_id": "missing"},
                            {k: v for k, v in record.items() if k != "delegation_event_id"}):
                if invalid is None:
                    runtime.storage.delete_component_state(key)
                else:
                    runtime.storage.save_component_state(key, invalid)
                try:
                    with pytest.raises(ProviderUnavailable, match="cannot be verified"):
                        resolve()
                finally:
                    runtime.storage.save_component_state(key, record)
            assert resolve() == origin
        finally:
            runtime.close()
