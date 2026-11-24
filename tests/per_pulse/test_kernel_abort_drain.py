"""A native cancellation callback must outlive neither its store nor owner.

Provider replies are explicit TEST_DOUBLEs. The World, Kernel, cancellation,
SQLite, shutdown deadline and successor lease acquisition are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from fastapi.testclient import TestClient
import pytest

from per_pulse.app import create_app
from per_pulse.service import PageService, PulseSettings
from pulse_harness.core import TurnStatus
from pulse_system.core.types import RuntimeLeaseState
from pulse_system.service.runtime import ServiceError
from pulse_system.substrate.storage import Storage
from test_execution import _factory


@pytest.mark.parametrize('abort_result_error', [False, True], ids=['returns', 'raises'])
def test_shutdown_retains_kernel_store_until_admitted_abort_returns(tmp_path, monkeypatch, caplog, abort_result_error):
    requests = []
    provider_entered, provider_release = threading.Event(), threading.Event()
    abort_entered, abort_release = threading.Event(), threading.Event()
    kernel_closed = threading.Event()
    factory = _factory(monkeypatch, requests, entered=provider_entered,
                       release=provider_release, respond_only=True)
    profile = PulseSettings(enabled=True, spontaneous_rate=0, max_background_pulses_per_hour=0)
    client = TestClient(create_app(tmp_path, model_factory=factory, pulse=profile), base_url='http://localhost')
    client.__enter__()
    runtime = client.app.state.pages.runtime
    backend = runtime.harness
    original_close = backend.close
    closed = False
    storage = None

    def close_after_abort_admission():
        assert abort_entered.wait(5), 'The cancellation caller must enter first.'
        return original_close()

    monkeypatch.setattr(backend, 'close', close_after_abort_admission)
    try:
        response = client.post('/api/pages', json={'title': 'Cancellation drain', 'instruction': 'HOLD_PROVIDER'})
        assert response.status_code == 201, response.text
        page = response.json()['page']['id']
        world = client.get('/api/bootstrap').json()['workspaceId']
        assert provider_entered.wait(5)
        with backend._lock:
            scope = next(iter(backend._active.values()))
            runner = backend.kernel._runtimes[scope.session_id].runner
        original_outcome = runner._outcome
        original_store_close = backend.kernel.store.close

        def hold_abort_before_its_last_read(*args, **kwargs):
            if threading.current_thread().name.startswith('pulse-abort-'):
                # Abort already recorded cancellation, but still needs the
                # store to construct its actual return value. A stopped model
                # worker alone cannot prove this separate caller has left.
                abort_entered.set()
                assert abort_release.wait(10), 'Release the native abort read boundary.'
                if abort_result_error:
                    raise RuntimeError('injected native abort outcome failure')
            return original_outcome(*args, **kwargs)

        def observe_store_close():
            original_store_close()
            kernel_closed.set()

        monkeypatch.setattr(runner, '_outcome', hold_abort_before_its_last_read)
        monkeypatch.setattr(backend.kernel.store, 'close', observe_store_close)
        client.portal.call(runtime.stop)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(runtime.close, timeout=.4)
            try:
                assert abort_entered.wait(5)
                provider_release.set()
                report = closing.result(timeout=5)
                (tmp_path / 'deadline-report.json').write_text(json.dumps(report.to_dict(), indent=2))
                deadline = time.monotonic() + 5
                with backend._lock:
                    while backend._active:
                        assert time.monotonic() < deadline
                        backend._lock.wait(.025)
                assert not kernel_closed.wait(.15), 'Kernel store closed while its native abort caller still needed it.'
                assert backend.kernel.store.get_turn(scope).status is TurnStatus.CANCELLED
                assert backend.kernel.store.actions_for_turn(scope) == ()
                assert not report.physical_exit_proven
                assert report.owner_lease.value == 'release_pending'
                storage = Storage(tmp_path / 'world.db')
                lease = storage.get_runtime_lease()
                assert lease.state is RuntimeLeaseState.ACTIVE
                with pytest.raises(ServiceError) as conflict:
                    PageService(tmp_path, model_factory=None)
                assert conflict.value.error == 'runtime_lease_conflict'
                assert storage.get_runtime_lease().epoch == lease.epoch
            finally:
                provider_release.set()
                abort_release.set()
            assert runtime.engine.wait_for_shutdown_drain(5)
            assert kernel_closed.wait(5)
        deadline = time.monotonic() + 5
        while storage.get_runtime_lease().state is not RuntimeLeaseState.RELEASED:
            assert time.monotonic() < deadline, 'Release only after every native caller leaves.'
            time.sleep(.01)
        assert runtime.close().to_dict() == report.to_dict(), 'Later drain cannot rewrite the original deadline report.'
        client.__exit__(None, None, None)
        closed = True
        # An engine callback arriving after shutdown owns cancellation must
        # leave without opening subordinate journals or replaying a request.
        backend.abort(scope.engram_id)
        failures = [record for record in caplog.records if 'could not abort in-flight pulse' in record.message]
        if abort_result_error:
            assert len(failures) == 1
            assert failures[0].exc_info is not None
            assert str(failures[0].exc_info[1]) == 'injected native abort outcome failure'
        else:
            assert failures == []
        with TestClient(create_app(tmp_path, model_factory=None), base_url='http://localhost') as reopened:
            assert reopened.get('/api/bootstrap').json()['workspaceId'] == world
            messages = reopened.get(f'/api/pages/{page}').json()['messages']
            assert not any(m['role'] == 'assistant' for m in messages)
        assert len(requests) == 1, 'Shutdown and restart cannot repeat the old request.'
    finally:
        provider_release.set()
        abort_release.set()
        if not closed:
            client.__exit__(None, None, None)
        if storage is not None:
            storage.close()
