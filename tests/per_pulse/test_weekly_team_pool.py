"""Native consultation and its return retain the caller's actual request pool.

Synthetic HTTP replies; production World, TUNNEL, Kernel and request ledger.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import importlib.util
from pathlib import Path
import random
import time

from fastapi.testclient import TestClient
import pytest
from types import SimpleNamespace

from per_pulse.app import create_app
from test_rim_native_integration import _model_factory, START
from pulse_system.core.types import CausalEventDomain, CausalEventKind, CausalEventSource
from pulse_system.core.pulse.engine import PulseEvent


@pytest.mark.parametrize("autonomous", [False, True], ids=["user-request", "self-request"])
def test_native_rim_consultation_and_return_inherit_the_original_pool(tmp_path, monkeypatch, autonomous):
    monkeypatch.setenv("P_PULSE_RIM_POLICY", "1")
    monkeypatch.setenv("PER_PULSE_RIM_TEST_KEY", "EXPLICIT_TEST_ONLY")
    monkeypatch.setattr(random.SystemRandom, "random", lambda _self: .5)
    spec = importlib.util.spec_from_file_location("rim_weekly_fixture", Path(__file__).parents[1] / "browser" / "rim_feedback_server.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    state, seen = {}, []
    factory = _model_factory(state, seen)
    metadata = fixture.seed(tmp_path, factory, state)
    state["bad"], state["good"] = metadata["bad"], metadata["good"]
    with TestClient(create_app(tmp_path, allow_workspace_actions=True, model_factory=factory), base_url="http://localhost") as client:
        before = len(seen)
        runtime = client.app.state.pages.runtime
        work = metadata["baseline"]
        if autonomous:
            assert client.patch("/api/pulse/weekly-usage", json={"enabled": True, "weeklyLimit": 20}).status_code == 200
            center = runtime.world.get_task_front(work).center_id
            runtime.causal_ledger.enqueue(world_id=runtime.world_id, flow=None, domain=CausalEventDomain.PULSE,
                kind=CausalEventKind.SPONTANEOUS, source=CausalEventSource.SELF, content=START,
                engram_id=runtime.continuity_engram_id, center_id=center,
                idempotency_key="fixture-self-consultation", runtime_fence=runtime._current_runtime_fence())
        else:
            assert client.post(f"/api/pages/{work}/messages", json={"content": START}).status_code == 202
        end = time.monotonic() + 20
        while time.monotonic() < end:
            view = client.get(f"/api/pages/{work}").json()
            if sum(item["role"] == "assistant" for item in view["messages"]) >= 2 and view["page"]["status"] == "idle":
                break
            time.sleep(.03)
        assert sum(item["role"] == "assistant" for item in view["messages"]) >= 2, view["execution"]
        choices = client.get(f"/api/pages/{work}/team/choices").json()["choices"]
        assert len(choices) == 1 and choices[0]["resultMessageId"]
        usage = client.get("/api/pulse/weekly-usage").json()["usage"]
        assert len(seen) - before >= 4
        assert usage["consumed"] == (len(seen) - before if autonomous else 0)

        # The same trusted caller identity controls the execution reserve.
        # A user-directed consultation and its return can use it; a SELF
        # consultation cannot turn itself into foreground via a tool call.
        requests = runtime.causal_ledger.list_events(kind="delegation_request", limit=50)
        assert requests
        request = requests[-1]
        returns = runtime.causal_ledger.list_events(kind="delegation_result", limit=50)
        assert returns
        assert runtime._native_foreground_event(PulseEvent.from_causal_event(request)) is not autonomous
        assert runtime._native_foreground_event(PulseEvent.from_causal_event(returns[-1])) is not autonomous
        assert not runtime._native_foreground_event(SimpleNamespace(event_id="missing"))
