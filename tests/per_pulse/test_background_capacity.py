"""A backlog of unattended turns leaves room for the person's next message.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The HTTP replies are synthetic; World, Kernel, SQLite and admission are real.
"""
from collections import Counter
import json
import threading

import httpx
import pytest

import per_pulse.service as service_module
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from test_pulse_integration import assistant_count, client_for, eventually, page, view


@pytest.mark.parametrize("capacity", (1, 2, 4))
def test_background_backlog_preserves_foreground_capacity_without_stopping_background(
    tmp_path, monkeypatch, capacity,
):
    base_config = service_module.RuntimeServiceConfig
    monkeypatch.setattr(service_module, "RuntimeServiceConfig",
                        lambda **kwargs: base_config(**kwargs, pulse_worker_capacity=capacity))
    monkeypatch.setenv("PER_PULSE_CAPACITY_TEST_KEY", "synthetic-test-credential")
    releases = [threading.Event() for _ in range(capacity)]
    entered = [threading.Event() for _ in range(capacity)]
    foreground_entered = threading.Event()
    foreground_release = threading.Event()

    def factory(scope):
        def respond(request):
            messages = json.loads(request.content)["messages"]
            text = json.dumps(messages)
            if "FOREGROUND_NOW" in text:
                foreground_entered.set()
                foreground_release.wait(20)
            else:
                for index in range(capacity):
                    if f"BACKGROUND_{index}" in text and any(
                        message.get("role") == "assistant" for message in messages
                    ):
                        entered[index].set()
                        releases[index].wait(20)
                        break
            chunks = [{"choices": [{"index": 0, "delta": {"content": "Ready."},
                                    "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
            data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=data + "data: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "capacity-fixture",
            credential_env="PER_PULSE_CAPACITY_TEST_KEY", max_retries=0,
        ), transport=httpx.MockTransport(respond))

    settings = PulseSettings(enabled=True, spontaneous_rate=0,
                             check_interval_seconds=0.02, refractory_seconds=0, max_propagation_depth=0)
    with client_for(tmp_path, pulse=settings, model_factory=factory) as client:
        try:
            runtime = client.app.state.pages.runtime
            backgrounds = []
            for index in range(capacity):
                work = page(client, f"Background {index}", f"BACKGROUND_{index}")
                backgrounds.append(work)
                eventually(lambda work=work: view(client, work),
                           lambda state: assistant_count(state) == 1)
            for index, work in enumerate(backgrounds):
                response = client.post(f"/api/pages/{work}/tick", json={"count": 1})
                assert response.status_code == 201, response.text
                if index < capacity - 1:
                    assert entered[index].wait(5), "the admitted background did not reach HTTP"
            last_center = runtime.world.get_task_front(backgrounds[-1]).center_id
            last_tick = eventually(
                lambda: runtime.causal_ledger.list_events(kind="spontaneous", center_id=last_center, limit=10),
                lambda events: len(events) == 1,
            )[0]
            # Wait for an actual admission decision for the final background;
            # sending the user message earlier would only test queue priority.
            eventually(
                lambda: runtime.storage.get_center_schedule_state(last_center),
                lambda state: state is not None
                and state.last_decision_at >= last_tick.created_at
                and state.decision.value in {"admitted", "waiting"},
            )
            foreground = page(client, "Foreground", "FOREGROUND_NOW")
            if capacity == 1:
                # Serial execution cannot preempt a blocked provider, but must
                # still allow unattended work to run and then release its slot.
                assert entered[0].wait(2)
                assert not foreground_entered.is_set()
                releases[0].set()
                assert foreground_entered.wait(3)
            else:
                started = foreground_entered.wait(2)
                snapshot = runtime.scheduling_snapshot()["capacity"]
                assert started, (
                    "Foreground HTTP did not start while background HTTP remained blocked",
                    snapshot, [event.is_set() for event in entered],
                )
                assert not entered[-1].is_set(), "background consumed the foreground reserve"
                assert snapshot["worker_limit"] == capacity
                assert snapshot["worker_running"] == capacity
                assert not any(event.is_set() for event in releases)

                # The background queue still advances as its own occupied slot
                # frees, even while the foreground request remains in flight.
                releases[0].set()
                assert entered[-1].wait(5), "the next background did not reuse a freed slot"
                assert not foreground_release.is_set()
            foreground_release.set()
            for release in releases:
                release.set()
            eventually(lambda: view(client, foreground), lambda state: assistant_count(state) == 1)
            for work in backgrounds:
                eventually(lambda work=work: view(client, work),
                           lambda state: state["tick"]["status"] == "complete")
        finally:
            foreground_release.set()
            for release in releases:
                release.set()

    metrics = [json.loads(row) for row in (tmp_path / "metrics.jsonl").read_text().splitlines()]
    settled = Counter(row["reservation"] for row in metrics
                      if row.get("type") == "center_reservation_settled")
    assert settled and all(count == 1 for count in settled.values()), settled
