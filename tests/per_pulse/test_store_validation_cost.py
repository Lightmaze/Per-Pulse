"""HarnessStore keeps its own validated writes validated; outside changes are rechecked.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import sqlite3

import pytest

from pulse_harness.core import HarnessScope, HarnessStore
from pulse_harness.core.errors import StoreUnavailable


def _scope(turn: str) -> HarnessScope:
    return HarnessScope("world", "subject", "session", 0, "owner", context_id="work", turn_id=turn)


def _count_full_validations(store, monkeypatch) -> list[int]:
    calls = [0]
    original = store._validate_database_content_locked

    def counted(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_validate_database_content_locked", counted)
    return calls


def test_own_writes_do_not_force_a_full_revalidation_on_each_read(tmp_path, monkeypatch):
    store = HarnessStore(tmp_path / "kernel.db")
    try:
        store.create_session(_scope("t0"))
        store.get_session(_scope("t0"))  # one full validation establishes the baseline
        calls = _count_full_validations(store, monkeypatch)
        for index in range(20):
            scope = _scope(f"t{index + 1}")
            store.create_turn(scope, f"Prompt {index}")
            assert store.get_turn(scope).prompt == f"Prompt {index}"
        assert calls[0] == 0, "the store's own writes stay validated"
    finally:
        store.close()


def test_an_outside_change_is_still_fully_validated(tmp_path, monkeypatch):
    path = tmp_path / "kernel.db"
    store = HarnessStore(path)
    try:
        scope = _scope("t1")
        store.create_session(scope)
        store.create_turn(scope, "Original prompt")
        store.get_turn(scope)
        calls = _count_full_validations(store, monkeypatch)
        other = sqlite3.connect(path)
        try:
            other.execute("PRAGMA user_version = 999")
            other.commit()
        finally:
            other.close()
        with pytest.raises(StoreUnavailable):
            store.get_turn(scope)
        assert calls[0] >= 1, "an external commit forces full validation"
    finally:
        store.close()


def test_event_index_upgrades_existing_store_and_retention_preserves_transcript(tmp_path):
    path = tmp_path / "kernel.db"
    store = HarnessStore(path, max_events_per_session=16)
    try:
        scope = _scope("t1")
        store.create_session(scope)
        store.create_turn(scope, "Original prompt")
        for index in range(12):
            store.append_transcript(scope, role="user", content=f"Message {index}",
                                    item_id=f"message-{index}")
        page = store.replay(scope, limit=16)
        assert not page.gap
        assert len(page.events) == 12
    finally:
        store.close()

    # Emulate a database created before the additive performance index.
    with sqlite3.connect(path) as old:
        old.execute("DROP INDEX events_session_item_progress")

    store = HarnessStore(path, max_events_per_session=8)
    try:
        store.append_transcript(scope, role="user", content="Message 12",
                                item_id="message-12")
        page = store.replay(scope, limit=8)
        assert page.gap
        assert [event.seq for event in page.events] == list(range(6, 14))
        assert page.retained_from == 6
        for index in range(13, 16):
            store.append_transcript(scope, role="user", content=f"Message {index}",
                                    item_id=f"message-{index}")
        assert [row["content"] for row in store.transcript_rows(scope)] == [
            f"Message {index}" for index in range(16)
        ]
    finally:
        store.close()

    store = HarnessStore(path, max_events_per_session=8)
    try:
        page = store.replay(scope, limit=8)
        assert page.gap and page.retained_from == 9
        assert [event.seq for event in page.events] == list(range(9, 17))
        assert len(store.transcript_rows(scope)) == 16
        with sqlite3.connect(path) as db:
            columns = [row[2] for row in db.execute(
                "PRAGMA index_info(events_session_item_progress)"
            )]
        assert columns == ["session_id", "item_id", "delta_seq", "revision"]
    finally:
        store.close()
