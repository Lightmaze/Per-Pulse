"""Real wall-clock multi-local Pulse continuation in a single Context Body.

This runs the actual local tick loop and native World/Kernel/SQLite with an
explicitly synthetic HTTP provider. It tests temporal liveness rather than
jumping a simulated clock; it does not establish multi-hour endurance,
model usefulness, or monetary cost limits.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import time

from per_pulse.service import PulseSettings
from test_pulse_integration import (
    TransportFixture, assistant_count, client_for, eventually, page, view,
)


def test_two_local_works_continue_over_real_wall_clock_with_foreground_and_restart(
    tmp_path, monkeypatch,
):
    fixture = TransportFixture(monkeypatch)
    pulse = PulseSettings(
        enabled=True, spontaneous_rate=1.0, check_interval_seconds=0.02,
        refractory_seconds=0.08, max_background_pulses_per_hour=2,
        max_propagation_depth=0,
    )
    with client_for(tmp_path, pulse=pulse, model_factory=fixture.factory) as client:
        runtime = client.app.state.pages.runtime
        controller = runtime.context_pulse
        # Build both local histories before allowing autonomous reentry.
        # Pausing does not stop the original human foreground turns.
        controller.set_paused(True)
        first = page(client, "Reading", "A_LOCAL_HISTORY")
        second = page(client, "Writing", "B_LOCAL_HISTORY")
        for work in (first, second):
            eventually(lambda work=work: view(client, work),
                       lambda snapshot: assistant_count(snapshot) == 1)
        assert len(fixture.requests) == 2
        assert controller.snapshot()["budget"]["used"] == 0

        # This is elapsed wall time, not a forged future checkpoint. Releasing
        # the original body admits one local opportunity in each Work.
        time.sleep(0.15)
        controller.set_paused(False)
        settled = eventually(
            lambda: runtime.causal_ledger.list_events(kind="spontaneous", limit=20),
            lambda events: len(events) == 2
            and all(event.status.value == "settled" for event in events),
            timeout=12,
        )
        centers = {runtime.world.get_task_front(work).center_id for work in (first, second)}
        assert {event.center_id for event in settled} == centers
        assert len({event.id for event in settled}) == 2
        assert {event.engram_id for event in settled} == {runtime.continuity_engram_id}
        for event in settled:
            parent = runtime.causal_ledger.get_event(event.parent_event_id)
            assert parent.kind.value == "assistant_result"
            assert parent.center_id == event.center_id
        for work in (first, second):
            eventually(lambda work=work: view(client, work),
                       lambda snapshot: assistant_count(snapshot) == 2)
        assert controller.snapshot()["budget"]["used"] == 2
        assert len(fixture.requests) == 4
        # The target's local model history is not replaced by the other Work.
        local_requests = {
            scope.context_id: json.dumps(request["messages"])
            for scope, request in zip(fixture.scopes, fixture.requests)
        }
        assert any("A_LOCAL_HISTORY" in messages for messages in local_requests.values())
        assert any("B_LOCAL_HISTORY" in messages for messages in local_requests.values())

        # Exhausted background admissions do not lock the current human Work.
        sent = client.post(f"/api/pages/{second}/messages",
                           json={"content": "Please improve my present draft."})
        assert sent.status_code == 202
        eventually(lambda: view(client, second),
                   lambda snapshot: assistant_count(snapshot) == 3)
        assert len(fixture.requests) == 5
        activation_ids = {event.id for event in settled}
        subject = runtime.continuity_engram_id
        before = {
            work: [item["id"] for item in view(client, work)["messages"]]
            for work in (first, second)
        }

    with client_for(tmp_path, pulse=pulse, model_factory=fixture.factory) as client:
        runtime = client.app.state.pages.runtime
        assert runtime.continuity_engram_id == subject
        assert {
            event.id for event in runtime.causal_ledger.list_events(
                kind="spontaneous", limit=20,
            )
        } == activation_ids
        assert client.get("/api/pulse").json()["budget"]["remaining"] == 0
        for work in (first, second):
            assert [item["id"] for item in view(client, work)["messages"]] == before[work]
        time.sleep(0.15)
        assert len(fixture.requests) == 5, "Restart must not replay old local activations."
