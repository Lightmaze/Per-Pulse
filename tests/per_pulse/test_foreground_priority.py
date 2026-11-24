"""Foreground response while another Work's native background pulse is blocked.

This runs the real World/Kernel/SQLite product and a controlled synthetic HTTP
model transport. It proves admission/liveness, not model quality or live cost.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import httpx

from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from test_pulse_integration import assistant_count, client_for, eventually, page, view


def test_foreground_work_continues_while_other_local_background_model_is_blocked(
    tmp_path, monkeypatch,
):
    """The user's separate Work must not wait for a hung background HTTP turn."""
    monkeypatch.setenv("PER_PULSE_PRIORITY_FIXTURE_KEY", "synthetic-fixture-credential")
    lock = threading.Lock()
    blocked_background = threading.Event()
    release_background = threading.Event()
    calls: list[tuple[int, str]] = []

    def model_factory(scope):
        def respond(request):
            body = json.loads(request.content)
            with lock:
                ordinal = len(calls) + 1
                calls.append((ordinal, scope.context_id))
            if ordinal == 3:
                # Initial Work A/B replies have settled. This is a real
                # autonomous request in A, held at the model transport.
                blocked_background.set()
                if not release_background.wait(timeout=12):
                    return httpx.Response(504, text="Synthetic background timeout")
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": f"FIXTURE_{ordinal}"},
                              "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 32, "completion_tokens": 8,
                                         "total_tokens": 40}},
            ]
            body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=body + "data: [DONE]\n\n",
            )

        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions",
                           "foreground-priority-fixture",
                           credential_env="PER_PULSE_PRIORITY_FIXTURE_KEY"),
            transport=httpx.MockTransport(respond),
        )

    pulse = PulseSettings(
        enabled=True, spontaneous_rate=1.0,
        check_interval_seconds=0.02, refractory_seconds=0.05,
        max_background_pulses_per_hour=1, max_propagation_depth=0,
    )
    with client_for(tmp_path, pulse=pulse, model_factory=model_factory) as client:
        runtime = client.app.state.pages.runtime
        controller = runtime.context_pulse
        controller.set_paused(True)
        reading = page(client, "Background reading", "READING_LOCAL_HISTORY")
        writing = page(client, "Foreground writing", "WRITING_LOCAL_HISTORY")
        for work in (reading, writing):
            eventually(lambda work=work: view(client, work),
                       lambda state: assistant_count(state) == 1)
        assert len(calls) == 2

        # Only Reading can acquire autonomous admission. A hold must not
        # prevent a human from talking to Writing in its original history.
        writing_center = runtime.world.get_task_front(writing).center_id
        controller.set_context_paused(writing_center, True)
        time.sleep(0.10)
        controller.set_paused(False)
        try:
            assert blocked_background.wait(timeout=10), (
                "Reading did not enter its actual background model turn"
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    client.post, f"/api/pages/{writing}/messages",
                    json={"content": "Please revise the current draft now."},
                )
                # This must complete before the background transport is
                # released. Otherwise foreground is serialized behind it.
                response = pending.result(timeout=4)
                assert response.status_code == 202, response.text
                eventually(lambda: view(client, writing),
                           lambda state: assistant_count(state) == 2, timeout=4)
                assert not release_background.is_set()
                assert len(calls) == 4
        finally:
            # Always release the synthetic provider, including on assertion
            # failure, so the actual runtime can shut down cleanly.
            release_background.set()

        eventually(lambda: view(client, reading),
                   lambda state: assistant_count(state) == 2)
        assert controller.snapshot()["budget"]["used"] == 1
        events = runtime.causal_ledger.list_events(kind="spontaneous", limit=20)
        assert len(events) == 1
        assert events[0].status.value == "settled"
        assert events[0].center_id == runtime.world.get_task_front(reading).center_id
