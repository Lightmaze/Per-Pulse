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
