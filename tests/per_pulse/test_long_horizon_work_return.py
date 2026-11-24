"""A held local activation remains the same unfinished work across time and restart.

Future budget windows are inspected without rewriting causal timestamps;
all model replies come from an explicitly synthetic HTTP transport. Real World,
Kernel, SQLite, page routing and foreground execution are exercised. No real
provider is billed or simulated two-hour wall-clock runtime claimed.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from per_pulse.service import PulseSettings
from test_pulse_integration import (
    TransportFixture, assistant_count, client_for, connect, eventually, page, view,
)


def test_held_cross_work_influence_resumes_after_long_backlog_without_refilling_budget(
    tmp_path, monkeypatch,
):
    fixture = TransportFixture(monkeypatch)
    profile = PulseSettings(
        enabled=True,
        spontaneous_rate=0,
        check_interval_seconds=0.01,
        refractory_seconds=0,
        max_propagation_depth=1,
        max_background_pulses_per_hour=2,
        max_background_provider_tokens_per_hour=60,
        background_provider_token_reservation=60,
    )

    with client_for(tmp_path, pulse=profile, model_factory=fixture.factory) as client:
        source = page(client, "Original evidence", "A_LOCAL_HISTORY")
        target = page(client, "Paused synthesis", "B_LOCAL_HISTORY")
        sibling = page(client, "Foreground writing", "C_LOCAL_HISTORY")
        for local in (source, target, sibling):
            eventually(lambda local=local: view(client, local),
                       lambda value: assistant_count(value) == 1)

        runtime = client.app.state.pages.runtime
        target_center = runtime.world.get_task_front(target).center_id
        # Native attention holds only this Work's new background admission.
        runtime.context_pulse.set_context_paused(target_center, True)
        connect(client, source, target, weight=0.9)
        sent = client.post(f"/api/pages/{source}/messages",
                           json={"content": "Evidence to influence the other Work."})
        assert sent.status_code == 202
        queued = eventually(
            lambda: runtime.causal_ledger.list_events(kind="propagation", limit=100),
            lambda rows: len(rows) == 1,
        )
        incoming_id = queued[0].id
        assert queued[0].center_id == target_center
        assert queued[0].status.value == "queued"
        assert assistant_count(view(client, target)) == 1

        # Inspect the budget as of two hours in the future, while preserving
        # the ledger's valid parent -> result -> propagation chronology. Moving
        # only a child's created_at before its parent would corrupt causal
        # evidence and is not a valid simulation of a delayed execution.
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        before = runtime.context_pulse.snapshot(now=future)
        live = client.get("/api/pulse").json()
        for projection in (before, live):
            budget = projection["providerTokenBudget"]
            assert budget["charged"] == 60
            assert budget["unaccountedReservations"] == 60
            assert budget["trackedAdmissions"] == 1
            assert budget["remaining"] == 0
            assert budget["hardProviderSpendingCap"] is False
        assert runtime.causal_ledger.get_event(incoming_id).status.value == "queued"

        # A human can still make progress in a sibling Work when the held
        # background token admission envelope is exhausted.
        human = client.post(f"/api/pages/{sibling}/messages",
                            json={"content": "Continue the foreground draft."})
        assert human.status_code == 202
        eventually(lambda: view(client, sibling),
                   lambda value: assistant_count(value) == 2)
        assert assistant_count(view(client, target)) == 1
        assert runtime.causal_ledger.get_event(incoming_id).status.value == "queued"

    with client_for(tmp_path, pulse=profile, model_factory=fixture.factory) as client:
        runtime = client.app.state.pages.runtime
        state = runtime.context_pulse.snapshot(now=datetime.now(timezone.utc) + timedelta(hours=2))
        assert {"center_id": target_center} in state["heldContexts"]
        assert state["providerTokenBudget"]["charged"] == 60
        assert state["providerTokenBudget"]["unaccountedReservations"] == 60
        assert state["providerTokenBudget"]["trackedAdmissions"] == 1
        assert runtime.causal_ledger.get_event(incoming_id).status.value == "queued"
        assert assistant_count(view(client, target)) == 1

        # Releasing attention doesn't recreate the Work, root, or model session.
        runtime.context_pulse.set_context_paused(target_center, False)
        eventually(lambda: view(client, target),
                   lambda value: assistant_count(value) == 2)
        eventually(lambda: runtime.causal_ledger.get_event(incoming_id),
                   lambda root: root.status.value == "settled")
        settled = eventually(
            lambda: client.get("/api/pulse").json()["providerTokenBudget"],
            lambda budget: budget["accountedTokens"] == 40,
        )
        assert settled["unaccountedReservations"] == 0
        assert settled["charged"] == 40
        assert settled["trackedAdmissions"] == 1
        assert settled["remaining"] == 20

        original = runtime.causal_ledger.get_event(incoming_id)
        parent = runtime.causal_ledger.get_event(original.parent_event_id)
        assert parent.kind.value == "assistant_result"
        assert original.content == parent.content == "A_FIXTURE_OUTPUT"
        messages = json.dumps(fixture.requests[-1]["messages"])
        assert "B_LOCAL_HISTORY" in messages
        assert "A_FIXTURE_OUTPUT" in messages
        assert "A_LOCAL_HISTORY" not in messages
        assert "Evidence to influence the other Work." not in messages
        assert assistant_count(view(client, sibling)) == 2
        assert len(fixture.requests) == 6
