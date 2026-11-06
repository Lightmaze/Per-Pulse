"""Native Habitat region is shared by separate Home/Work histories in one World.

The model replies are a declared synthetic HTTP fixture. World, Kernel,
ManagedHabitat journal and filesystem are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from per_pulse.service import PageService, PulseSettings
from pulse_system.service.native_habitat import OBSERVE, WRITE, validate
from test_internal_work_organization import WorkFixture, run_event


@pytest.mark.parametrize("name,values", [
    (OBSERVE, {"op": "list", "region": "../home"}),
    (OBSERVE, {"op": "read", "region": "lab", "file": "../../secrets"}),
    (OBSERVE, {"op": "read", "region": "lab", "file": ".managed-state"}),
    (WRITE, {"op": "write", "region": "lab/../other", "file": "note.txt", "content": "x"}),
    (WRITE, {"op": "append", "region": "lab", "file": "a.txt", "content": "x\x00"}),
])
def test_region_cannot_escape_its_world_directory(name, values):
    with pytest.raises(ValueError):
        validate(name, values)


def test_two_distinct_works_revisit_one_persistent_region(tmp_path, monkeypatch):
    fixture = WorkFixture(monkeypatch.setenv)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        adaptive_connections=False, spectrum_enabled=False,
    )

    def open_service():
        return PageService(
            tmp_path, model_factory=fixture.factory,
            allow_workspace_actions=True, auto_apply_edits=True,
            posture=False, pulse=settings,
        )

    pages = open_service()
    a = b = None
    try:
        first = pages.create_page("Visit a place", "Make one real local observation.")
        second = pages.create_page("Return to the place", "Read what is already there.")
        a, b = first["page"]["id"], second["page"]["id"]
        fixture.plan = [(WRITE, {
            "op": "write", "region": "garden", "file": "notes.txt",
            "content": "An actual first observation.\n",
        })]
        run_event(pages.runtime, pages.runtime.causal_ledger.get_event(first["initialMessageId"]))
        result = fixture.outcomes[0]
        assert result["status"] == "succeeded", result
        receipt = result["output"]["effectReceipt"]
        assert receipt["terminal_state"] == "committed"
        assert receipt["before_digest"] is None
        assert receipt["after_digest"] == hashlib.sha256(b"An actual first observation.\n").hexdigest()
        assert receipt["correlation_id"]
        assert (pages.runtime.habitat.root / "regions/garden/notes.txt").read_text() == "An actual first observation.\n"

        fixture.plan = [(OBSERVE, {"op": "read", "region": "garden", "file": "notes.txt"})]
        run_event(pages.runtime, pages.runtime.causal_ledger.get_event(second["initialMessageId"]))
        assert fixture.outcomes[0]["status"] == "succeeded"
        assert fixture.outcomes[0]["output"]["current"] == "An actual first observation.\n"
        assert fixture.outcomes[0]["output"]["source"]["contextId"].startswith("work:")
        assert len(pages.runtime.storage.list_engrams()) == 1
    finally:
        pages.runtime.close()

    # A different local Work continues the same place after SQLite restart.
    resumed = open_service()
    try:
        fixture.plan = [(WRITE, {
            "op": "append", "region": "garden", "file": "notes.txt",
            "content": "A second observation.\n",
        })]
        instruction = resumed.send(b, "Continue the shared regional observation.")
        run_event(resumed.runtime, resumed.runtime.causal_ledger.get_event(instruction["messageId"]))
        answer = fixture.outcomes[0]
        assert answer["status"] == "succeeded", answer
        after = "An actual first observation.\nA second observation.\n"
        assert (resumed.runtime.habitat.root / "regions/garden/notes.txt").read_text() == after
        assert answer["output"]["effectReceipt"]["before_digest"] == receipt["after_digest"]
        assert answer["output"]["effectReceipt"]["after_digest"] == hashlib.sha256(after.encode()).hexdigest()
        assert len(resumed.runtime.storage.list_engrams()) == 1
    finally:
        resumed.runtime.close()
