"""One explicit enable reaches a sourced Work and persisted file in one World.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient
import pytest

from autonomous_practice_fixture import AutonomousPracticeFixture, PRACTICE_TEXT, PRACTICE_TITLE
from per_pulse.app import create_app
from per_pulse.service import PulseSettings


def wait_for(read, ready):
    deadline = time.monotonic() + 20
    value = None
    while time.monotonic() < deadline:
        value = read()
        if ready(value):
            return value
        time.sleep(.03)
    raise AssertionError(f"The native autonomous flow did not settle: {value}")


def test_one_enable_creates_native_self_work_file_and_report_then_reopens_without_replay(tmp_path, monkeypatch):
    fixture = AutonomousPracticeFixture(monkeypatch.setenv)
    settings = PulseSettings(enabled=True, spontaneous_rate=0)
    def app():
        return create_app(tmp_path, pulse=settings, posture=True, model_factory=fixture.factory,
                          allow_workspace_actions=True, auto_apply_edits=True)

    with TestClient(app(), base_url="http://localhost") as client:
        assert client.get("/api/pages/home").json()["messages"] == []
        assert fixture.requests == []
        enabled = client.post("/api/pulse/practice", json={"weeklyLimit": 20})
        assert enabled.status_code == 200, enabled.text
        departure = enabled.json()["departureEventId"]
        fronts = wait_for(lambda: client.get("/api/bootstrap").json()["pages"],
                          lambda pages: any(page["title"] == PRACTICE_TITLE for page in pages))
        work = next(page["id"] for page in fronts if page["title"] == PRACTICE_TITLE)
        produced = wait_for(lambda: client.get(f"/api/pages/{work}").json(),
                            lambda view: bool(view["artifacts"]) and view["page"]["status"] == "idle")
        home = wait_for(lambda: client.get("/api/pages/home").json(),
                        lambda view: sum(message["role"] == "assistant" for message in view["messages"]) >= 2)
        usage = wait_for(lambda: client.get("/api/pulse/weekly-usage").json()["usage"],
                         lambda value: value["consumed"] == len(fixture.requests)
                         and value["states"]["completed"] == value["consumed"])
        assert usage["consumed"] >= 6 and usage["consumed"] < 20
        assert (tmp_path / "workspace" / "practice.txt").read_text() == PRACTICE_TEXT
        runtime = client.app.state.pages.runtime
        root = runtime.causal_ledger.get_event(departure)
        assert root.source.value == "self" and root.parent_event_id is None
        assert root.status.value == "settled"
        assert home["messages"][0]["role"] == "system"
        assert not [event for event in runtime.causal_ledger.list_events(world_id=runtime.world_id, limit=100)
                    if event.source.value == "user" and event.parent_event_id is None]
        artifact, = produced["artifacts"]
        assert produced["messages"][0]["role"] == "system"
        assert client.get(f"/api/pages/{work}/artifacts/{artifact['actionId']}/download").content.decode() == PRACTICE_TEXT
        before = len(fixture.requests)
        repeated = client.post("/api/pulse/practice", json={"weeklyLimit": 20})
        assert repeated.json()["departureEventId"] == departure
        assert len(fixture.requests) == before
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": False, "weeklyLimit": 20}).status_code == 200

    with TestClient(app(), base_url="http://localhost") as client:
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["enabled"] is False
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == usage["consumed"]
        assert client.get(f"/api/pages/{work}").json()["artifacts"] == produced["artifacts"]
        assert client.get("/api/pages/home").json()["messages"] == home["messages"]
        assert len(fixture.requests) == before


@pytest.mark.parametrize("mode", ["offline", "example", "profile-disabled"])
def test_unavailable_practice_does_not_enable_spending_or_create_a_self_origin(tmp_path, monkeypatch, mode):
    fixture = AutonomousPracticeFixture(monkeypatch.setenv)
    with TestClient(create_app(tmp_path, example=mode == "example",
                    model_factory=None if mode == "offline" else fixture.factory,
                    pulse=PulseSettings(enabled=mode != "profile-disabled")), base_url="http://localhost") as client:
        reply = client.post("/api/pulse/practice", json={"weeklyLimit": 20})
        assert reply.status_code == 409, reply.text
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["enabled"] is False
        runtime = client.app.state.pages.runtime
        assert not runtime.causal_ledger.list_events(world_id=runtime.world_id, kind="spontaneous", limit=10)


def test_a_single_request_allowance_can_expand_and_finish_the_original_practice(tmp_path, monkeypatch):
    fixture = AutonomousPracticeFixture(monkeypatch.setenv)
    with TestClient(create_app(tmp_path, pulse=PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0),
                    model_factory=fixture.factory, allow_workspace_actions=True, auto_apply_edits=True),
                    base_url="http://localhost") as client:
        enabled = client.post("/api/pulse/practice", json={"weeklyLimit": 1})
        departure = enabled.json()["departureEventId"]
        runtime = client.app.state.pages.runtime
        wait_for(lambda: runtime.causal_ledger.get_event(departure), lambda event: event.status.value == "uncertain")
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == 1
        assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 20}).status_code == 200
        fronts = wait_for(lambda: client.get("/api/bootstrap").json()["pages"],
                          lambda pages: any(page["title"] == PRACTICE_TITLE for page in pages))
        work = next(page["id"] for page in fronts if page["title"] == PRACTICE_TITLE)
        wait_for(lambda: client.get(f"/api/pages/{work}").json(),
                 lambda view: bool(view["artifacts"]) and view["page"]["status"] == "idle")
        wait_for(lambda: client.get("/api/pages/home").json(),
                 lambda view: sum(message["role"] == "assistant" for message in view["messages"]) >= 2
                 and view["page"]["status"] == "idle")
        wait_for(lambda: client.get("/api/pulse/weekly-usage").json()["usage"],
                 lambda value: value["states"]["completed"] == len(fixture.requests)
                 and value["consumed"] == len(fixture.requests))
        assert (tmp_path / "workspace" / "practice.txt").read_text() == PRACTICE_TEXT
        departures = [event for event in runtime.causal_ledger.list_events(world_id=runtime.world_id, limit=100)
                      if event.metadata.get("context_pulse") == "first_departure"]
        assert [event.id for event in departures] == [departure]
