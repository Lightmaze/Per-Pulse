"""Per-Work backlog must reflect the complete native queue, not the recent sample.

Native World, Kernel and SQLite are real. Model transport is explicitly
synthetic; this is not a useful-thinking or provider-cost measurement.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from per_pulse.service import PulseSettings
from test_pulse_integration import (
    TransportFixture, assistant_count, client_for, connect, eventually, page, view,
)


def test_held_receiver_retains_its_own_visible_backlog(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        refractory_seconds=0, max_background_pulses_per_hour=8,
    )
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        source = page(client, "Reading", "A_LOCAL_HISTORY")
        receiver = page(client, "Synthesis", "B_LOCAL_HISTORY")
        other = page(client, "Unrelated", "C_LOCAL_HISTORY")
        for p in (source, receiver, other):
            eventually(lambda p=p: view(client, p), lambda v: assistant_count(v) == 1)
        connect(client, source, receiver)
        held = client.patch(f"/api/pulse/contexts/{receiver}", json={"paused": True})
        assert held.status_code == 200, held.text
        message = client.post(f"/api/pages/{source}/messages", json={"content": "NEW_SOURCE_FACT"})
        assert message.status_code == 202, message.text
        eventually(lambda: view(client, source), lambda v: assistant_count(v) == 2)
        def pending():
            return client.get("/api/pulse/observation").json()
        observation = eventually(pending, lambda v: any(
            entry["pageId"] == receiver and entry["queued"] == 1
            for entry in v["pulse"]["queueByContext"]
        ))
        state = observation["pulse"]
        entries = state["queueByContext"]
        assert sum(entry["queued"] for entry in entries) == state["queue"]["queued"]
        assert sum(entry["running"] for entry in entries) == state["queue"]["running"]
        receiver_entry = next(entry for entry in entries if entry["pageId"] == receiver)
        assert receiver_entry["running"] == 0
        assert receiver_entry["oldestQueuedAt"]
        assert all(entry["pageId"] != other for entry in entries)
        assert all(entry["center_id"] is not None for entry in entries)
        assert state["queue"]["oldestQueuedAt"] == min(
            entry["oldestQueuedAt"] for entry in entries if entry["oldestQueuedAt"]
        )
        assert observation["workspaceId"]
        assert observation["serviceInstanceId"]
        # Operator release is not a synthetic new user turn. The existing
        # queued delivery proceeds once in its original Work.
        released = client.patch(f"/api/pulse/contexts/{receiver}", json={"paused": False})
        assert released.status_code == 200
        eventually(lambda: view(client, receiver), lambda v: assistant_count(v) == 2)
        eventually(pending, lambda v: not any(
            entry["pageId"] == receiver and entry["queued"] > 0
            for entry in v["pulse"]["queueByContext"]
        ))
