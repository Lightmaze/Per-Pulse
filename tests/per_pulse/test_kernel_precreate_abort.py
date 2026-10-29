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


def test_native_world_precreate_shutdown_drains_and_immediately_reopens_same_work(tmp_path, monkeypatch):
    """Exercise the complete PageService -> Runtime -> Pulse -> Kernel close.

    A clean abort must release the actual World lease, not merely return from
    Kernel.close. No model request, file effect or late answer may escape the
    original pre-create turn; a new human input can use that same Work later.
    """
    from fastapi.testclient import TestClient
    from per_pulse.app import create_app
    from per_pulse.service import PulseSettings
    from test_execution import _factory

    requests = []
    factory = _factory(monkeypatch, requests, respond_only=True)
    profile = PulseSettings(enabled=True, spontaneous_rate=0, max_background_pulses_per_hour=0)
    client = TestClient(create_app(tmp_path, model_factory=factory, pulse=profile), base_url="http://localhost")
    entered, continue_create, abort_entered = threading.Event(), threading.Event(), threading.Event()
    closed = False
    client.__enter__()
    runtime = client.app.state.pages.runtime
    backend = runtime.harness
    original_create = backend.kernel.store.create_turn
    scopes = []

    def delayed_create(scope, prompt):
        scopes.append(scope)
        entered.set()
        if not continue_create.wait(timeout=10):
            raise TimeoutError("Full-World pre-create barrier was not released")
        return original_create(scope, prompt)

    monkeypatch.setattr(backend.kernel.store, "create_turn", delayed_create)
    original_abort = backend.abort

    def observe_abort(engram_id):
        original_abort(engram_id)
        abort_entered.set()

    monkeypatch.setattr(backend, "abort", observe_abort)
    world = client.get("/api/bootstrap").json()["workspaceId"]
    try:
        created = client.post("/api/pages", json={"title": "Pre-create recovery", "instruction": "Keep this original Work."})
        assert created.status_code == 201, created.text
        work = created.json()["page"]["id"]
        assert entered.wait(timeout=5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(client.__exit__, None, None, None)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with backend._lock:
                        if scopes[0].turn_id in backend._abort_before_create:
                            break
                    time.sleep(0.01)
                else:
                    pytest.fail("Native shutdown must reach the claimed, not-yet-created turn")
                assert abort_entered.wait(timeout=5), "The complete Pulse abort path must run before admission"
            finally:
                continue_create.set()
            closing.result(timeout=10)
            closed = True
        report = runtime.close()
        assert report.physical_exit_proven, [
            (part.component, part.owner.value, part.process_tree.value, part.error_code)
            for part in report.components
        ]
        assert report.escaped_count == 0, report.to_dict()
        assert report.owner_lease.value == "released", report.to_dict()
        assert report.clean, report.to_dict()
        assert requests == [], "The cancelled original turn must never reach a provider"

        # No lease-expiry wait or retry: cooperative shutdown must allow the
        # immediate successor, unlike the separate SIGKILL acceptance.
        with TestClient(create_app(tmp_path, model_factory=factory, pulse=profile), base_url="http://localhost") as reopened:
            assert reopened.get("/api/bootstrap").json()["workspaceId"] == world
            state = reopened.get(f"/api/pages/{work}").json()
            assert not any(m["role"] == "assistant" for m in state["messages"])
            store = reopened.app.state.pages.runtime.kernel_service.store
            assert store.get_turn(scopes[0]).status is TurnStatus.CANCELLED
            assert store.actions_for_turn(scopes[0]) == ()
            sent = reopened.post(f"/api/pages/{work}/messages", json={"content": "CONTINUE_AFTER_PRECREATE"})
            assert sent.status_code == 202, sent.text
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                state = reopened.get(f"/api/pages/{work}").json()
                if any(m["role"] == "assistant" for m in state["messages"]):
                    break
                time.sleep(0.025)
            else:
                pytest.fail("The same Work must complete an explicitly new foreground turn")
            assert len(requests) == 1
            assert "CONTINUE_AFTER_PRECREATE" in str(requests[0]["messages"])
    finally:
        continue_create.set()
        if not closed:
            client.__exit__(None, None, None)


def test_slow_abort_callback_retains_world_lease_until_it_physically_leaves(tmp_path, monkeypatch):
    """A stopped model worker does not prove its cancellation caller left."""
    from fastapi.testclient import TestClient
    from per_pulse.app import create_app
    from per_pulse.service import PageService, PulseSettings
    from pulse_system.service.runtime import ServiceError
    from pulse_system.core.types import RuntimeLeaseState
    from pulse_system.substrate.storage import Storage
    from test_execution import _factory

    requests, provider_entered, provider_release = [], threading.Event(), threading.Event()
    abort_entered, abort_release = threading.Event(), threading.Event()
    factory = _factory(monkeypatch, requests, entered=provider_entered, release=provider_release, respond_only=True)
    profile = PulseSettings(enabled=True, spontaneous_rate=0, max_background_pulses_per_hour=0)
    client = TestClient(create_app(tmp_path, model_factory=factory, pulse=profile), base_url="http://localhost")
    client.__enter__()
    runtime = client.app.state.pages.runtime
    backend = runtime.harness
    original_abort = backend.abort
    closed = False
    store = None

    def slow_abort(engram_id):
        original_abort(engram_id)
        abort_entered.set()
        if not abort_release.wait(timeout=15):
            raise TimeoutError("Slow abort callback barrier was not released")

    monkeypatch.setattr(backend, "abort", slow_abort)
    try:
        response = client.post("/api/pages", json={"title": "Cancellation caller", "instruction": "HOLD_PROVIDER"})
        assert response.status_code == 201, response.text
        work = response.json()["page"]["id"]
        assert provider_entered.wait(timeout=5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(client.__exit__, None, None, None)
            try:
                assert abort_entered.wait(timeout=5), "The engine must own an abort callback"
            finally:
                provider_release.set()
            closing.result(timeout=10)
            closed = True
        report = runtime.close()
        # Engine.close returns at its settle deadline. The coordinator may
        # already have frozen an escaped pulse_engine report by that instant;
        # inspect the engine's own completed child census deterministically.
        assert runtime._engine._shutdown_finished.wait(timeout=2)
        callback = next(part for part in runtime._engine._shutdown_reports if part.component == "harness_abort")
        assert callback.owner.value == "escaped"
        assert not runtime._engine.wait_for_shutdown_drain(timeout=0)
        assert callback.process_tree.value == "not_applicable"
        assert report.owner_lease.value == "release_pending", report.to_dict()
        store = Storage(tmp_path / "world.db")
        original_lease = store.get_runtime_lease()
        assert original_lease.state is RuntimeLeaseState.ACTIVE
        with pytest.raises(ServiceError) as conflict:
            PageService(tmp_path, model_factory=None)
        assert conflict.value.error == "runtime_lease_conflict"
        assert store.get_runtime_lease().epoch == original_lease.epoch

        abort_release.set()
        assert runtime._engine.wait_for_shutdown_drain(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.get_runtime_lease().state is RuntimeLeaseState.RELEASED:
                break
            time.sleep(0.01)
        else:
            pytest.fail("The deferred native finalizer must release the lease after callback exit")
        with TestClient(create_app(tmp_path, model_factory=None), base_url="http://localhost") as reopened:
            messages = reopened.get(f"/api/pages/{work}").json()["messages"]
            assert not any(m["role"] == "assistant" for m in messages), "No late answer may publish"
        assert len(requests) == 1, "Cancellation and recovery cannot repeat the provider request"
    finally:
        provider_release.set()
        abort_release.set()
        if not closed:
            client.__exit__(None, None, None)
        if store is not None:
            store.close()
