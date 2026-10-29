"""Offline checks for the read-only live Pulse observation instrument.

Synthetic HTTP statuses check evidence handling, not model quality or a
multi-hour run. Copyright 2026 Lightmaze Contributors. Apache-2.0.
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from per_pulse.pulse_observer import observe


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def snapshot(events, *, connected=True):
    return {
        "enabled": True, "paused": False, "running": True,
        "providerConfigured": connected,
        "queue": {"queued": 0, "running": 0, "oldestQueuedAt": None},
        "budget": {"limit": 4, "used": len(events), "remaining": 0},
        "providerTokenBudget": None,
        "recentActivations": events,
    }


def activation(event_id, status, page):
    return {
        "eventId": event_id, "status": status, "kind": "spontaneous",
        "pageId": page, "sourceEventId": "source-" + event_id,
        "sourcePageId": page,
    }


def mock_client(states, *, worlds=None, instances=None, error_sample=None):
    """One service-bound status response per observation, never split reads."""
    readings = {"observation": 0}
    worlds = worlds or ["world-A"] * len(states)
    instances = instances or ["service-A"] * len(states)

    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/api/pulse/observation"
        pos = readings["observation"]
        readings["observation"] += 1
        if error_sample is not None and pos == error_sample:
            return httpx.Response(503)
        return httpx.Response(200, json={
            "workspaceId": worlds[min(pos, len(worlds) - 1)],
            "serviceInstanceId": instances[min(pos, len(instances) - 1)],
            "pulse": states[min(pos, len(states) - 1)],
        })

    return httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(respond),
    )


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_native_local_provenance_is_deduplicated_over_elapsed_samples(tmp_path):
    clock = Clock()
    phases = [
        snapshot([activation("a", "queued", "work-A")]),
        snapshot([activation("a", "settled", "work-A")]),
        snapshot([activation("b", "settled", "work-B"),
                  activation("a", "settled", "work-A")]),
    ]
    output = tmp_path / "private" / "observation.jsonl"
    with mock_client(phases) as client:
        report = observe("http://127.0.0.1:8000", output,
                         duration_seconds=2, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    rows = records(output)
    assert [row["type"] for row in rows] == [
        "header", "sample", "sample", "sample", "summary"
    ]
    assert [len(row["event_updates"]) for row in rows[1:4]] == [1, 1, 1]
    assert report["workspaceId"] == "world-A"
    assert report["observed_events"] == 2
    assert report["observed_settled_background_by_page"] == {
        "work-A": 1, "work-B": 1
    }
    assert report["eligible_for_uninterrupted_observation"]
    assert report["provider_quality_or_cost_verified"] is False
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0
    assert "source-a" in output.read_text()
    assert "PRIVATE_TRANSCRIPT" not in output.read_text()



def test_observer_counts_only_source_checked_navigable_work_results(tmp_path):
    """A settled event without an exact receiving message is not a returned artifact."""
    clock = Clock()
    visible = activation("visible", "settled", "work-A")
    visible["messageId"] = "visible"
    propagation = activation("prop", "settled", "work-B")
    propagation["kind"] = "propagation"
    propagation["messageId"] = "prop"
    invisible = activation("not-a-message", "settled", "work-C")
    home = activation("home-event", "settled", "home")
    home["messageId"] = "home-event"
    unfinished = activation("still-running", "running", "work-D")
    unfinished["messageId"] = "still-running"
    phases = [
        snapshot([activation("visible", "queued", "work-A")]),
        snapshot([visible, propagation, invisible, home, unfinished]),
    ]
    phases[1]["paused"] = True
    phases[1]["running"] = False
    output = tmp_path / "watch.jsonl"
    with mock_client(phases) as client:
        result = observe("http://127.0.0.1:8000", output,
                         duration_seconds=1, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)

    # These are lower-bound observations, not verified model usefulness.
    assert result["observed_settled_background_by_page"] == {
        "home": 1, "work-A": 1, "work-B": 1, "work-C": 1,
    }
    assert result["observed_navigable_work_results_by_page"] == {
        "work-A": 1, "work-B": 1,
    }
    assert result["observed_navigable_work_results"] == 2
    assert result["pulse_running_samples"] == 1
    assert result["pulse_paused_samples"] == 1
    assert result["eligible_for_uninterrupted_observation"]
    rows = records(output)
    assert rows[-1] == result
    updates = [event for row in rows if row["type"] == "sample"
               for event in row["event_updates"]]
    # The queued observation predates the verified receiving message; its
    # earlier update must remain visible rather than being retroactively filled.
    visible_updates = [event for event in updates if event["eventId"] == "visible"]
    assert [event["messageId"] for event in visible_updates] == [None, "visible"]
    assert next(event for event in updates if event["eventId"] == "not-a-message")["messageId"] is None


def test_observer_rejects_unverified_receiving_message_identity(tmp_path):
    clock = Clock()
    genuine = activation("valid", "settled", "work-B")
    genuine["messageId"] = "valid"
    spoofed = activation("event-1", "settled", "work-A")
    spoofed["messageId"] = "different-message"
    output = tmp_path / "watch.jsonl"
    # A good item before a malformed item cannot be partially admitted.
    with mock_client([snapshot([genuine, spoofed])]) as client:
        result = observe("http://127.0.0.1:8000", output,
                         duration_seconds=0.1, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    assert result["poll_errors"] == 2
    assert result["successful_samples"] == 0
    assert result["observed_events"] == 0
    assert result["observed_navigable_work_results"] == 0
    assert not result["eligible_for_uninterrupted_observation"]
    assert [row["type"] for row in records(output)] == [
        "header", "poll_error", "poll_error", "summary",
    ]


def test_invalid_full_window_does_not_claim_saturation_or_any_events(tmp_path):
    """Coverage flags also commit atomically with validated native observations."""
    clock = Clock()
    events = [activation(f"valid-{i}", "settled", "work-A") for i in range(19)]
    malformed = activation("broken", "settled", "work-B")
    malformed["messageId"] = "unrelated-message"
    output = tmp_path / "watch.jsonl"
    with mock_client([snapshot(events + [malformed])]) as client:
        report = observe("http://127.0.0.1:8000", output,
                         duration_seconds=0.1, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    assert report["successful_samples"] == 0
    assert report["observed_events"] == 0
    assert report["observed_settled_background_by_page"] == {}
    assert report["observed_navigable_work_results"] == 0
    assert report["recent_window_saturated"] is False
    assert report["poll_errors"] == 2

def test_world_switch_stops_without_cross_body_merging(tmp_path):
    clock = Clock()
    output = tmp_path / "watch.jsonl"
    with mock_client(
        [snapshot([activation("one", "settled", "first")])] * 2,
        worlds=["world-A", "world-B"],
    ) as client:
        result = observe("http://127.0.0.1:8000", output,
                         duration_seconds=2, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    assert result["reason"] == "world_changed"
    assert result["observed_events"] == 1
    assert not result["eligible_for_uninterrupted_observation"]
    assert records(output)[-2]["type"] == "world_change"


def test_same_world_new_service_instance_stops_live_continuity_claim(tmp_path):
    clock = Clock()
    output = tmp_path / "watch.jsonl"
    with mock_client(
        [snapshot([activation("one", "settled", "work-A")])] * 2,
        worlds=["world-A", "world-A"],
        instances=["service-1", "service-2"],
    ) as client:
        result = observe("http://127.0.0.1:8000", output,
                         duration_seconds=2, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    assert result["reason"] == "service_restarted"
    assert result["workspaceId"] == "world-A"
    assert result["serviceInstanceId"] == "service-1"
    assert result["observed_events"] == 1
    assert not result["eligible_for_uninterrupted_observation"]
    assert records(output)[-2]["type"] == "service_restart"


def test_missing_service_instance_identity_is_a_poll_error(tmp_path):
    clock = Clock()
    output = tmp_path / "watch.jsonl"
    with mock_client([snapshot([])], instances=[""]) as client:
        result = observe("http://127.0.0.1:8000", output,
                         duration_seconds=0.1, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    assert result["successful_samples"] == 0
    assert result["poll_errors"] >= 1
    assert [row["type"] for row in records(output)].count("poll_error") == result["poll_errors"]
    assert not result["eligible_for_uninterrupted_observation"]


def test_failed_poll_is_reported_not_erased(tmp_path):
    clock = Clock()
    output = tmp_path / "watch.jsonl"
    with mock_client([snapshot([]), snapshot([]), snapshot([])],
                     error_sample=1) as client:
        result = observe("http://127.0.0.1:8000", output,
                         duration_seconds=2, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    assert result["poll_errors"] == 1
    assert not result["eligible_for_uninterrupted_observation"]
    assert "poll_error" in [row["type"] for row in records(output)]


def test_full_recent_window_cannot_pass_as_exhaustive_evidence(tmp_path):
    clock = Clock()
    twenty = [activation(str(i), "settled", "work") for i in range(20)]
    output = tmp_path / "watch.jsonl"
    with mock_client([snapshot(twenty)]) as client:
        result = observe("http://127.0.0.1:8000", output,
                         duration_seconds=0.1, interval_seconds=1,
                         client=client, clock=clock, sleep=clock.sleep)
    assert result["recent_window_saturated"]
    assert not result["eligible_for_uninterrupted_observation"]
    assert result["observed_events"] == 20


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8000", "http://example.com:8000",
    "http://user:secret@localhost:8000", "http://localhost",
    "http://localhost:8000/secret", "http://localhost:8000/?key=secret",
])
def test_rejects_non_loopback_or_credentialed_endpoints_before_file_creation(tmp_path, url):
    with pytest.raises(ValueError, match="loopback"):
        observe(url, tmp_path / "watch.jsonl", duration_seconds=1,
                interval_seconds=1)
    assert not (tmp_path / "watch.jsonl").exists()


def test_existing_report_and_invalid_duration_are_never_replaced(tmp_path):
    output = tmp_path / "watch.jsonl"
    output.write_text("KEEP")
    with pytest.raises(ValueError):
        observe("http://127.0.0.1:8000", output, duration_seconds=1,
                interval_seconds=0)
    assert output.read_text() == "KEEP"
    with pytest.raises(FileExistsError):
        observe("http://127.0.0.1:8000", output, duration_seconds=1,
                interval_seconds=1)
    assert output.read_text() == "KEEP"


def test_read_only_observation_uses_actual_world_kernel_http_without_side_effects(tmp_path):
    """The observer consumes the real product read API, not only a mock response."""
    from per_pulse.service import PulseSettings
    from test_pulse_integration import client_for, page

    data = tmp_path / "native"
    pulse = PulseSettings(enabled=True, spontaneous_rate=0,
                          check_interval_seconds=0.01)
    with client_for(data, pulse=pulse, model_factory=None) as service:
        first = page(service, "Reading", "READING_LOCAL_SOURCE")
        second = page(service, "Writing", "WRITING_LOCAL_SOURCE")
        before = service.get("/api/bootstrap").json()
        bound = service.get("/api/pulse/observation").json()
        assert bound["workspaceId"] == before["workspaceId"]
        assert bound["serviceInstanceId"] == before["serviceInstanceId"]
        assert isinstance(bound["pulse"]["recentActivations"], list)
        runtime = service.app.state.pages.runtime
        original = [(event.id, event.status.value) for event in
                    runtime.causal_ledger.list_events(limit=200)]
        clock = Clock()
        output = tmp_path / "native-observation.jsonl"
        report = observe("http://127.0.0.1:8000", output,
                         duration_seconds=0.1, interval_seconds=0.1,
                         client=service, clock=clock, sleep=clock.sleep)
        after = [(event.id, event.status.value) for event in
                 runtime.causal_ledger.list_events(limit=200)]
        assert report["workspaceId"] == before["workspaceId"]
        assert report["serviceInstanceId"] == before["serviceInstanceId"]
        assert report["provider_configured_samples"] == 0
        assert report["observed_events"] == 0
        assert original == after, "Reading native status must not enqueue model work"
        assert {p["id"] for p in service.get("/api/bootstrap").json()["pages"]} >= {
            "home", first, second
        }


def test_native_observer_counts_verified_cross_work_delivery(tmp_path, monkeypatch):
    """A real local World and page lineage anchor the observer's Work result."""
    from per_pulse.service import PulseSettings
    from test_pulse_integration import (
        TransportFixture, assistant_count, client_for, connect, eventually, page, view,
    )

    fixture = TransportFixture(monkeypatch)
    pulse = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        refractory_seconds=0, max_propagation_depth=1,
        max_background_pulses_per_hour=1,
    )
    with client_for(tmp_path / "native-world", pulse=pulse,
                    model_factory=fixture.factory) as service:
        source = page(service, "Source Work", "A_LOCAL_HISTORY")
        receiver = page(service, "Receiving Work", "B_LOCAL_HISTORY")
        for work in (source, receiver):
            eventually(lambda work=work: view(service, work),
                       lambda value: assistant_count(value) == 1)
        connect(service, source, receiver)
        sent = service.post(f"/api/pages/{source}/messages",
                            json={"content": "A_NEW_INPUT"})
        assert sent.status_code == 202
        eventually(lambda: view(service, receiver),
                   lambda value: assistant_count(value) == 2)

        def native_delivery():
            response = service.get("/api/pulse/observation")
            assert response.status_code == 200
            return [event for event in response.json()["pulse"]["recentActivations"]
                    if event["kind"] == "propagation" and event["pageId"] == receiver]

        delivery = eventually(native_delivery, lambda events: len(events) == 1
                              and events[0]["status"] == "settled")
        event = delivery[0]
        # The native service checked page/engram lineage before exposing this ID.
        assert event["messageId"] == event["eventId"]
        receiving_messages = view(service, receiver)["messages"]
        assert any(message["id"] == event["messageId"] for message in receiving_messages)

        clock = Clock()
        output = tmp_path / "native-delivery.jsonl"
        report = observe("http://127.0.0.1:8000", output,
                         duration_seconds=0.1, interval_seconds=0.1,
                         client=service, clock=clock, sleep=clock.sleep)
        assert report["observed_navigable_work_results_by_page"][receiver] == 1
        assert report["observed_navigable_work_results"] == 1
        assert report["observed_settled_background_by_page"][receiver] == 1
        assert report["eligible_for_uninterrupted_observation"]
        assert report["provider_quality_or_cost_verified"] is False
        assert records(output)[-1] == report



def test_native_service_restart_changes_instance_not_persistent_world(tmp_path):
    from per_pulse.service import PulseSettings
    from test_pulse_integration import client_for

    data = tmp_path / "native"
    pulse = PulseSettings(enabled=True, spontaneous_rate=0)
    with client_for(data, pulse=pulse, model_factory=None) as first:
        initial = first.get("/api/bootstrap").json()
    with client_for(data, pulse=pulse, model_factory=None) as second:
        resumed = second.get("/api/bootstrap").json()
        bound = second.get("/api/pulse/observation").json()
    assert resumed["workspaceId"] == initial["workspaceId"]
    assert resumed["serviceInstanceId"] != initial["serviceInstanceId"]
    assert bound["workspaceId"] == resumed["workspaceId"]
    assert bound["serviceInstanceId"] == resumed["serviceInstanceId"]
