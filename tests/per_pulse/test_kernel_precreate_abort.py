"""Regression for abort-before-durable-turn admission in the native Kernel.

The HTTP adapter is deliberately synthetic and must never receive a request.
Kernel turn ownership and persistence use the real SQLite-backed runtime.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core import TurnStatus
from pulse_system.agent.harness.base import HarnessError
from pulse_system.service.kernel_runtime import KernelHarnessRuntime


@pytest.mark.parametrize("operation", ["abort", "shutdown"])
def test_native_kernel_cancels_scope_claimed_before_durable_turn(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("PER_PULSE_PRECREATE_RACE_KEY", "synthetic-only")
    requests = []

    def model_factory(_scope):
        def unexpected(request):
            requests.append(request)
            raise AssertionError("Cancellation must precede model admission")
        return ChatHTTPModelAdapter(
            ChatHTTPConfig(
                "https://fixture.invalid/chat/completions", "precreate-race-fixture",
                credential_env="PER_PULSE_PRECREATE_RACE_KEY",
            ),
            transport=httpx.MockTransport(unexpected),
        )

    backend = KernelHarnessRuntime(
        db_path=tmp_path / "kernel.db", workspace=tmp_path / "workspace",
        model_factory=model_factory, enable_posture=False,
    )
    backend.bind_world("world-precreate-race", lambda: None, publication=None)
    entered = threading.Event()
    continue_create = threading.Event()
    original_create = backend.kernel.store.create_turn

    def delayed_create(scope, prompt):
        entered.set()
        if not continue_create.wait(timeout=8):
            raise TimeoutError("Pre-create test barrier was never released")
        return original_create(scope, prompt)

    monkeypatch.setattr(backend.kernel.store, "create_turn", delayed_create)
    closed = False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            turn = pool.submit(backend.run_turn, "engram-for-race", "No model call is permitted")
            assert entered.wait(timeout=5), "The Kernel scope must be claimed first"
            with backend._lock:
                scope = next(iter(backend._active.values()))
                assert scope.turn_id in backend._starting_turn_ids
            if operation == "abort":
                backend.abort("engram-for-race")
                reason = "world_abort"
                closing = None
            else:
                closing = pool.submit(backend.close)
                reason = "world_shutdown"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with backend._lock:
                    if backend._abort_before_create.get(scope.turn_id) == reason:
                        break
                time.sleep(0.01)
            else:
                pytest.fail("Cancellation intent was not registered before create_turn")
            # Neither path can attempt TurnRunner.abort yet: no row exists.
            continue_create.set()
            with pytest.raises(HarnessError) as caught:
                turn.result(timeout=5)
            assert caught.value.code == "kernel_turn_cancelled_before_start"
            if closing is not None:
                closing.result(timeout=5)
                closed = True
            else:
                assert backend.kernel.store.get_turn(scope).status is TurnStatus.CANCELLED
                assert backend.kernel.store.actions_for_turn(scope) == ()
        assert not requests
    finally:
        continue_create.set()
        if not closed:
            backend.close()
