"""Native completion racing shutdown; provider replies are explicit fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import threading

import pytest

from pulse_system.agent.harness.base import HarnessError
from test_pages import client_for, http_factory, send


@pytest.mark.parametrize("case", ["completed", "abort_failed", "worker_still_running"])
def test_shutdown_after_native_completion_retains_worker_and_abort_evidence(tmp_path, monkeypatch, case):
    finished, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    requests = []
    with client_for(tmp_path, model_factory=http_factory(monkeypatch, requests)) as client:
        runtime = client.app.state.pages.runtime
        original_run, original_abort = runtime.engine._run_one, runtime.harness.abort

        def hold_completed_worker(*args):
            result = original_run(*args)
            assert result[3] is None
            finished.set()
            assert release.wait(15), "The test must release the completed worker."
            return result

        def observe_abort(engram_id):
            cancelled.set()
            try:
                if case == "abort_failed":
                    raise HarnessError("explicit_abort_failure", "Synthetic cancellation failure.",
                                       "Retain the failure.", phase="abort")
                return original_abort(engram_id)
            finally:
                if case != "worker_still_running":
                    release.set()

        monkeypatch.setattr(runtime.engine, "_run_one", hold_completed_worker)
        monkeypatch.setattr(runtime.harness, "abort", observe_abort)
        send(client, "home", "One actual native turn before shutdown.")
        assert finished.wait(10)
        assert len(requests) == 1
        # The native turn is already detached, while the engine still owns its
        # completed worker. This is the shutdown race, not a mocked close result.
        assert not runtime.harness._active
        try:
            client.portal.call(runtime.stop)
            report = runtime.close(timeout=0.3 if case == "worker_still_running" else 10)
            (tmp_path / "shutdown-report.json").write_text(json.dumps(report.to_dict(), indent=2))
            assert cancelled.is_set()
            components = {row.component: row for row in report.components}
            if case == "completed":
                assert report.clean, report.to_dict()
                assert report.owner_lease.value == "released"
                assert report.physical_exit_proven
            elif case == "abort_failed":
                assert not report.clean
                assert components["harness_abort"].error_code == "abort_callback_failed"
            else:
                assert not report.clean
                assert not report.physical_exit_proven
                assert components["pulse_workers"].unresolved > 0
        finally:
            release.set()
            assert runtime.engine.wait_for_shutdown_drain(10)
    if case == "completed":
        with client_for(tmp_path, model_factory=None) as reopened:
            messages = reopened.get("/api/pages/home").json()["messages"]
            assert [m["role"] for m in messages] == ["user", "assistant"]
            assert messages[-1]["content"] == "Transport fixture response."
        assert len(requests) == 1
