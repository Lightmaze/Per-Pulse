"""Causal-loop acceptance: real local runtime with a synthetic HTTP provider."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time

import httpx
import pytest

from per_pulse.service import PageService
from pulse_system.core.types import CausalEventStatus

from test_core_acceptance import _assistant_count, _client, _factory, _page, _response


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def step(self, seconds=5):
        self.now += timedelta(seconds=seconds)
        return self.now


def _field(body, page):
    return next(item for item in body.view()["contexts"] if item["pageId"] == page)


def _pump(pages, clock, predicate, *, timeout=5):
    """Drive actual engine workers without starting a competing wall-clock loop."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pages.context_body.advance(clock.now)
        pages.runtime.engine.tick()
        pages.runtime._native_work.drain()
        if predicate():
            pages.context_body.advance(clock.now)
            return
        time.sleep(0.005)
    pytest.fail("The native runtime did not settle: " + json.dumps(pages.context_body.view()))


def test_settled_output_changes_which_local_context_activates(tmp_path, monkeypatch):
    """Matched target drives diverge because a real settled result excites/inhibits."""
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _response("Causal-loop fixture finding.")

    pages = PageService(tmp_path, model_factory=_factory(monkeypatch, handler))
    clock = Clock()
    body = pages.context_body
    body.clock = lambda: clock.now
    try:
        source = pages.create_page("Source", "SOURCE_PRIVATE_HISTORY")["page"]["id"]
        excited = pages.create_page("Excited target", "EXCITED_PRIVATE_HISTORY")["page"]["id"]
        inhibited = pages.create_page("Inhibited target", "INHIBITED_PRIVATE_HISTORY")["page"]["id"]
        _pump(pages, clock, lambda: all(_assistant_count(pages.view(page)) == 1
                                       for page in (source, excited, inhibited)))
        assert len(requests) == 3
        body.hold(source, "Return to the source finding.", 1.0)
        body.hold(excited, "Consider linked evidence when useful.", 0.4)
        body.hold(inhibited, "Consider linked evidence when useful.", 0.4)
        body.connect(source, excited, 1.0)
        body.connect(source, inhibited, -1.0)
        body.run(2)
        for _ in range(8):
            body.advance(clock.step())
            _pump(pages, clock, lambda: pages.runtime.engine.capacity_snapshot()["worker_running"] == 0)
            if body.view()["remaining"] == 0:
                break
        _pump(pages, clock, lambda: _assistant_count(pages.view(source)) == 2
              and _assistant_count(pages.view(excited)) == 2)
        source_field = _field(body, source)
        excited_field = _field(body, excited)
        inhibited_field = _field(body, inhibited)
        assert source_field["pulseCount"] == excited_field["pulseCount"] == 1
        assert inhibited_field["pulseCount"] == 0
        assert inhibited_field["inhibition"] > 0
        assert _assistant_count(pages.view(inhibited)) == 1
        assert {item["eventId"] for item in excited_field["evidence"]} == {source_field["lastResultId"]}
        assert len(requests) == 5
        spontaneous_requests = requests[3:]
        assert all(any(message["role"] == "system" and "internal continuation" in str(message["content"])
                       for message in request["messages"]) for request in spontaneous_requests)
        for request in requests:
            text = json.dumps(request["messages"])
            assert not ("SOURCE_PRIVATE_HISTORY" in text and "EXCITED_PRIVATE_HISTORY" in text)
            assert not ("SOURCE_PRIVATE_HISTORY" in text and "INHIBITED_PRIVATE_HISTORY" in text)

        # Raise another context's drive after exhaustion: no implicit budget refill.
        body.hold(inhibited, "Still an unresolved thought.", 1.0)
        for _ in range(60):
            assert body.advance(clock.step()) is None
        pages.runtime.engine.tick()
        assert len(requests) == 5
        assert body.view()["enabled"] is False
        assert body.view()["remaining"] == 0
    finally:
        asyncio.run(pages.close())


def test_pause_cancels_queued_internal_pulse_but_keeps_user_input(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _response()

    pages = PageService(tmp_path, model_factory=_factory(monkeypatch, handler))
    clock = Clock()
    body = pages.context_body
    body.clock = lambda: clock.now
    try:
        body.hold("home", "A retained internal thought.", 1.0)
        body.run(1)
        internal = None
        for _ in range(12):
            internal = body.advance(clock.step())
            if internal:
                break
        assert internal is not None
        assert pages.runtime.causal_ledger.get_event(internal).status is CausalEventStatus.QUEUED
        user = pages.send("home", "USER_INPUT_AFTER_INTERNAL_ADMISSION")["messageId"]
        body.pause()
        assert pages.runtime.causal_ledger.get_event(internal).status is CausalEventStatus.CANCELLED
        assert pages.runtime.causal_ledger.get_event(user).status is CausalEventStatus.QUEUED
        _pump(pages, clock, lambda: _assistant_count(pages.view("home")) == 1)
        assert len(requests) == 1
        assert "USER_INPUT_AFTER_INTERNAL_ADMISSION" in json.dumps(requests[0]["messages"])
        assert "internal continuation" not in json.dumps(requests[0]["messages"])
        assert body.view()["enabled"] is False
    finally:
        asyncio.run(pages.close())


def test_reopen_preserves_field_and_evidence_without_reapplying_result(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _response()

    factory = _factory(monkeypatch, handler)
    pages = PageService(tmp_path, model_factory=factory)
    clock = Clock()
    pages.context_body.clock = lambda: clock.now
    try:
        target = pages.create_page("Retained target", "TARGET_REOPEN_HISTORY")["page"]["id"]
        _pump(pages, clock, lambda: _assistant_count(pages.view(target)) == 1)
        pages.context_body.hold(target, "Preserve this unfinished thought.", 0.4)
        pages.context_body.connect("home", target, 0.75)
        pages.send("home", "SOURCE_REOPEN_HISTORY")
        _pump(pages, clock, lambda: _assistant_count(pages.view("home")) == 1)
        before = _field(pages.context_body, target)
        assert len(before["evidence"]) == 1
        pages.context_body.run(3)
    finally:
        asyncio.run(pages.close())

    reopened = PageService(tmp_path, model_factory=factory)
    reopened.context_body.clock = lambda: clock.now
    try:
        after = _field(reopened.context_body, target)
        for key in ("note", "drive", "excitation", "inhibition", "pulseCount", "resultCount", "evidence"):
            assert after[key] == before[key], key
        assert reopened.context_body.view()["enabled"] is False
        assert reopened.context_body.view()["remaining"] == 0
        for _ in range(3):
            reopened.context_body.advance(clock.now)
            reopened.runtime.engine.tick()
        assert _field(reopened.context_body, target)["evidence"] == before["evidence"]
        assert _field(reopened.context_body, target)["resultCount"] == before["resultCount"]
        assert len(requests) == 2
    finally:
        asyncio.run(reopened.close())


@pytest.mark.parametrize("payload", [
    {"maxPulses": 0}, {"maxPulses": 101}, {"maxPulses": True}, {"maxPulses": 1.5},
])
def test_invalid_activation_grants_do_not_change_state(tmp_path, payload):
    with _client(tmp_path) as client:
        before = client.get("/api/pulse").json()
        response = client.post("/api/pulse/run", json=payload)
        assert response.status_code == 422
        assert client.get("/api/pulse").json() == before


def test_disconnected_attention_is_retained_without_starting_a_model(tmp_path):
    with _client(tmp_path) as client:
        target = _page(client, "Offline thought", "An ordinary queued instruction.")
        response = client.patch(f"/api/pages/{target}/attention", json={"note": "A retained thought.", "drive": 0.7})
        assert response.status_code == 200
        before = client.get("/api/pulse").json()
        unavailable = client.post("/api/pulse/run", json={"maxPulses": 2})
        assert unavailable.status_code == 409
        assert unavailable.json()["error"]["code"] == "provider_unconfigured"
        assert client.get("/api/pulse").json() == before
        assert client.app.state.pages.runtime.tick_count == 0


@pytest.mark.parametrize("endpoint,payload", [
    ("/api/pages/home/attention", {"note": "A thought.", "drive": True}),
    ("/api/pages/home/attention", {"note": "A thought.", "drive": 1.01}),
    ("/api/pages/home/attention", {"note": "", "drive": 0.5}),
    ("/api/pulse/connections", {"sourcePageId": "home", "targetPageId": "missing", "weight": True}),
])
def test_invalid_attention_values_leave_the_field_unchanged(tmp_path, endpoint, payload):
    with _client(tmp_path) as client:
        before = client.get("/api/pulse").json()
        method = client.patch if endpoint.endswith("/attention") else client.post
        response = method(endpoint, json=payload)
        assert response.status_code == 422
        assert client.get("/api/pulse").json() == before


def test_admission_failure_rolls_back_budget_and_event_together(tmp_path, monkeypatch):
    """A failed event insert cannot spend the grant or leave a phantom pulse."""
    pages = PageService(tmp_path, model_factory=_factory(monkeypatch, lambda _request: _response()))
    clock = Clock()
    body = pages.context_body
    body.clock = lambda: clock.now
    ledger = pages.runtime.causal_ledger
    insert = ledger._insert_event_uncommitted

    def reject_attention(conn, event):
        if event.metadata.get("context_body") == body.key:
            raise sqlite3.OperationalError("Synthetic failure after reserving the activation.")
        return insert(conn, event)

    try:
        body.hold("home", "A thought ready for a local activation.", 1.0)
        body.run(1)
        monkeypatch.setattr(ledger, "_insert_event_uncommitted", reject_attention)
        with pytest.raises(sqlite3.OperationalError, match="Synthetic failure"):
            for _ in range(12):
                body.advance(clock.step())
        state = body.view()
        assert state["remaining"] == 1
        assert state["sequence"] == 0
        assert _field(body, "home")["pulseCount"] == 0
        assert _field(body, "home")["pendingEventId"] is None
        assert ledger.find_causal_event_by_idempotency(pages.runtime.world_id, body.key + ":pulse:1") is None

        monkeypatch.setattr(ledger, "_insert_event_uncommitted", insert)
        event_id = body.advance(clock.now)
        assert event_id is not None
        assert body.view()["remaining"] == 0
        assert body.view()["sequence"] == 1
        assert _field(body, "home")["pulseCount"] == 1
        assert ledger.find_causal_event_by_idempotency(pages.runtime.world_id, body.key + ":pulse:1").id == event_id
        assert body.advance(clock.now) is None
        body.pause()
    finally:
        asyncio.run(pages.close())


def test_feedback_failure_retries_the_result_without_double_propagation(tmp_path, monkeypatch):
    """The cursor and numeric effects commit as one outcome transaction."""
    pages = PageService(tmp_path, model_factory=_factory(monkeypatch, lambda _request: _response()))
    clock = Clock()
    body = pages.context_body
    body.clock = lambda: clock.now
    try:
        body.advance(clock.now)
        source = pages.create_page("Source of evidence", "FEEDBACK_FAILURE_SOURCE")["page"]["id"]
        body.hold("home", "A retained receiving context.", 0.4)
        body.connect(source, "home", 0.75)
        # Settle the actual provider turn without projecting feedback yet.
        deadline = time.monotonic() + 5
        while _assistant_count(pages.view(source)) != 1 and time.monotonic() < deadline:
            pages.runtime.engine.tick()
            time.sleep(0.005)
        assert _assistant_count(pages.view(source)) == 1
        write = body._write

        def fail_feedback(conn, state):
            if state["contexts"].get(source, {}).get("resultCount", 0):
                raise sqlite3.OperationalError("Synthetic feedback commit failure.")
            write(conn, state)

        monkeypatch.setattr(body, "_write", fail_feedback)
        with pytest.raises(sqlite3.OperationalError, match="feedback commit"):
            body.advance(clock.now)
        assert _field(body, source)["resultCount"] == 0
        assert _field(body, "home")["evidence"] == []
        assert _field(body, "home")["excitation"] == 0.0

        monkeypatch.setattr(body, "_write", write)
        body.advance(clock.now)
        assert _field(body, source)["resultCount"] == 1
        assert _field(body, "home")["excitation"] == pytest.approx(0.75)
        evidence = _field(body, "home")["evidence"]
        assert len(evidence) == 1
        body.advance(clock.now)
        assert _field(body, source)["resultCount"] == 1
        assert _field(body, "home")["excitation"] == pytest.approx(0.75)
        assert _field(body, "home")["evidence"] == evidence
    finally:
        asyncio.run(pages.close())


def test_a_long_clock_gap_does_not_manufacture_offline_activations(tmp_path, monkeypatch):
    """One delayed tick advances a bounded interval, without replaying lost time."""
    pages = PageService(tmp_path, model_factory=_factory(monkeypatch, lambda _request: _response()))
    clock = Clock()
    body = pages.context_body
    body.clock = lambda: clock.now
    try:
        body.hold("home", "Keep this thought available.", 1.0)
        body.run(3)
        assert body.advance(clock.step(24 * 3600)) is None
        state = _field(body, "home")
        assert 0 < state["excitation"] < 1
        assert state["pulseCount"] == 0
        assert body.view()["remaining"] == 3
        excitation = state["excitation"]
        assert body.advance(clock.now) is None
        assert _field(body, "home")["excitation"] == excitation
    finally:
        asyncio.run(pages.close())


def test_failed_internal_turn_damps_activity_and_does_not_create_success_feedback(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(503, text="Synthetic failed local continuation.")

    pages = PageService(tmp_path, model_factory=_factory(monkeypatch, handler))
    clock = Clock()
    body = pages.context_body
    body.clock = lambda: clock.now
    try:
        body.hold("home", "A retained thought with an unsuccessful local attempt.", 1)
        body.run(2)
        event_id = None
        for _ in range(12):
            event_id = body.advance(clock.step())
            if event_id:
                break
        assert event_id is not None
        _pump(pages, clock, lambda: pages.runtime.causal_ledger.get_event(event_id).status
              in {CausalEventStatus.FAILED, CausalEventStatus.UNCERTAIN})
        field = _field(body, "home")
        assert field["pulseCount"] == 1 and field["resultCount"] == 0
        assert field["lastResultId"] is None
        assert field["drive"] == pytest.approx(0.5)
        assert field["inhibition"] == pytest.approx(1)
        assert field["pendingEventId"] is None
        assert field["evidence"] == []
        assert len(requests) == 1
        assert pages.runtime.storage.load_component_state(body.key)["turnRevisions"] == {}
        for _ in range(60):
            assert body.advance(clock.step()) is None
            pages.runtime.engine.tick()
        assert body.view()["sequence"] == 1
        assert body.view()["remaining"] == 1
        assert len(requests) == 1
        assert _assistant_count(pages.view("home")) == 0
    finally:
        asyncio.run(pages.close())
