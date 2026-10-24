"""Opt-in wall-clock soak of the actual Runtime with a synthetic HTTP provider.

Run PER_PULSE_SOAK_SECONDS=720 python -m pytest -s -q this_file.py.
No external endpoint or model is contacted. The provider fixture repeatedly
uses the real attention tools so that the bounded grant can be exhausted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from test_core_acceptance import _client, _factory, _page
from test_native_pulse_bridge import stream_response


@pytest.mark.skipif(not os.environ.get("PER_PULSE_SOAK_SECONDS"),
                    reason="Opt-in real-time soak; set PER_PULSE_SOAK_SECONDS.")
def test_runtime_wall_clock_soak_respects_shared_activation_cap(tmp_path, monkeypatch):
    duration = float(os.environ["PER_PULSE_SOAK_SECONDS"])
    assert duration >= 300, "Use at least five minutes to exercise default attention dynamics."
    cap = 30
    markers = ("SOAK_CONTEXT_ALPHA", "SOAK_CONTEXT_BETA", "SOAK_CONTEXT_GAMMA")
    lock = threading.Lock()
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        encoded = json.dumps(messages)
        contained = [marker for marker in markers if marker in encoded]
        assert len(contained) == 1, "Concurrent local contexts mixed their histories."
        with lock:
            requests.append({"elapsed": round(time.monotonic() - started, 3),
                             "context": contained[0], "last_role": messages[-1]["role"]})
        if messages[-1]["role"] == "tool":
            if '"staged"' in messages[-1]["content"]:
                return stream_response(text="Synthetic soak result; the retained thought remains active.")
            return stream_response(tool="attention", arguments={
                "op": "hold", "note": "A synthetic retained thought for the wall-clock soak.", "drive": 1.0,
            }, call_id="soak-hold-" + uuid4().hex)
        return stream_response(tool="attention", arguments={"op": "inspect"},
                               call_id="soak-inspect-" + uuid4().hex)

    started = time.monotonic()
    snapshots = []
    quiet_since = None
    quiet_requests = None
    with _client(tmp_path, _factory(monkeypatch, handler)) as client:
        pages = [_page(client, marker, marker + " retains its own conversation.") for marker in markers]
        runtime = client.app.state.pages.runtime
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = client.get("/api/pulse").json()
            contexts = [item for item in state["contexts"] if item["pageId"] in pages]
            if len(contexts) == 3 and all(item["resultCount"] == 1 and item["drive"] == 1 for item in contexts):
                break
            time.sleep(0.05)
        else:
            pytest.fail("Initial native attention tool turns did not settle: " + json.dumps(state))
        assert len(requests) == 9
        response = client.post("/api/pulse/run", json={"maxPulses": cap})
        assert response.status_code == 200, response.text
        previous_tick = runtime.tick_count
        next_report = time.monotonic()
        end = started + duration
        while time.monotonic() < end:
            time.sleep(min(2, max(0, end - time.monotonic())))
            state = client.get("/api/pulse").json()
            fields = [item for item in state["contexts"] if item["pageId"] in pages]
            count = sum(item["pulseCount"] for item in fields)
            completed = sum(item["resultCount"] for item in fields)
            assert 0 <= count <= cap
            assert runtime.running
            assert runtime.tick_count > previous_tick
            previous_tick = runtime.tick_count
            with lock:
                request_count = len(requests)
            assert request_count <= 3 * (cap + 3)
            if count == cap and completed == cap + 3 and runtime.engine.capacity_snapshot()["worker_running"] == 0:
                if quiet_since is None:
                    quiet_since = time.monotonic()
                    quiet_requests = request_count
                assert request_count == quiet_requests
                assert state["remaining"] == 0 and state["enabled"] is False
            if time.monotonic() >= next_report:
                snapshot = {"elapsed": round(time.monotonic() - started, 1),
                            "ticks": runtime.tick_count, "requests": request_count,
                            "pulses": count, "results": completed, "remaining": state["remaining"],
                            "threads": threading.active_count()}
                snapshots.append(snapshot)
                print("PULSE_SOAK " + json.dumps(snapshot), flush=True)
                next_report = time.monotonic() + 30
        assert sum(item["pulseCount"] for item in fields) == cap
        assert sum(item["resultCount"] for item in fields) == cap + 3
        assert quiet_since is not None
        assert quiet_requests == 99
        summary = {
            "durationSeconds": round(time.monotonic() - started, 2),
            "worldTicks": runtime.tick_count,
            "localActivations": cap,
            "settledResults": cap + 3,
            "httpFixtureRequests": len(requests),
            "quietSecondsAfterExhaustion": round(time.monotonic() - quiet_since, 2),
            "contexts": len(pages),
            "transport": "httpx.MockTransport; no external model",
            "snapshots": snapshots,
        }
        report_path = os.environ.get("PER_PULSE_SOAK_REPORT")
        if report_path:
            Path(report_path).write_text(json.dumps(summary, indent=2) + "\n")
        print("PULSE_SOAK_RESULT " + json.dumps(summary), flush=True)
