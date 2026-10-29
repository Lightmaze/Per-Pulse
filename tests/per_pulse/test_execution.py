"""Page controls exercise the native World, Kernel, HTTP and real file effects.

The model choices below are explicitly scripted fixtures. No external model or
paid provider is contacted by these integration checks.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from per_pulse.execution import ExecutionError, PageExecution
from per_pulse.service import PageService
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core import ApprovalDecision, TurnRunner, TurnStatus
from pulse_harness.core.contracts import ActionStatus


def _client(path: Path, **kwargs):
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def _new_page(client, instruction="WRITE_ONE"):
    response = client.post("/api/pages", json={"title": instruction, "instruction": instruction})
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def _send(client, page_id, content):
    response = client.post(f"/api/pages/{page_id}/messages", json={"content": content})
    assert response.status_code == 202, response.text
    return response.json()


def _wait(execution, page_id, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    result = None
    while time.monotonic() < deadline:
        result = execution.snapshot(page_id)
        if predicate(result):
            return result
        time.sleep(0.025)
    pytest.fail(f"Page execution did not reach the expected state: {result}")


def _factory(monkeypatch, requests, *, entered=None, release=None, tool=None, respond_only=False):
    monkeypatch.setenv("PER_PULSE_EXECUTION_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]
        text = json.dumps(messages)
        if entered is not None and (
            "HOLD_PROVIDER" in text or
            ("HOLD_AFTER_APPROVAL" in text and any(m.get("role") == "tool" for m in messages))
        ):
            entered.set()
            assert release.wait(8), "The test must release its synthetic provider."
        if not respond_only and not any(m.get("role") == "tool" for m in messages) and ("WRITE_" in text or tool is not None):
            path = "two.txt" if "WRITE_TWO" in text else "one.txt"
            name, arguments = tool if tool is not None else ("mutate_file", {
                "path": path, "expected_digest": None, "content": f"Written by {path}.\n",
            })
            chunk = {
                "tool_calls": [{"index": 0, "id": "fixture-file", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments),
                }}],
            }
            finish = "tool_calls"
        else:
            chunk = {"content": "Fixture response from the original page."}
            finish = "stop"
        chunks = [
            {"choices": [{"index": 0, "delta": chunk, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "execution-fixture", credential_env="PER_PULSE_EXECUTION_TEST_KEY"),
            transport=httpx.MockTransport(handler),
        )

    return factory


def test_observation_does_not_create_a_session_or_a_runner(tmp_path):
    with _client(tmp_path, model_factory=None) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        page_id = _new_page(client, "A queued page.")
        before = runtime.kernel_service.store.sessions_for_world(runtime.world_id)
        for target in ("home", page_id):
            view = execution.snapshot(target)
            assert view["pageId"] == target
            assert view["sessionId"] is None
            assert view["turnId"] is None
            assert view["pendingActions"] == []
            assert view["uncertainActions"] == []
            assert view["uncertainActionsTruncated"] is False
            assert view["canCancel"] is False
        assert runtime.kernel_service.store.sessions_for_world(runtime.world_id) == before
        assert not runtime.kernel_service._runtimes
        assert runtime.tick_count == 0
        with pytest.raises(ExecutionError) as missing:
            execution.snapshot("missing")
        assert missing.value.status == 404


def test_exact_approval_keeps_home_running_and_retries_do_not_reexecute(tmp_path, monkeypatch):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action = waiting["pendingActions"][0]
        assert waiting["status"] == "needs_attention"
        assert action["toolName"] == "mutate_file"
        assert action["arguments"]["path"] == "one.txt"
        assert not (tmp_path / "workspace" / "one.txt").exists()

        _send(client, "home", "Home can answer while the page needs approval.")
        _wait(execution, "home", lambda p: p["turnId"] is not None and p["status"] == "idle")
        assert execution.snapshot(page_id)["turnId"] == waiting["turnId"]

        receipt = execution.resolve_approval(page_id, action["actionId"], "allow_once")
        assert receipt["accepted"] and not receipt["repeated"]
        _wait(execution, page_id, lambda p: p["status"] == "idle")
        assert (tmp_path / "workspace" / "one.txt").read_text() == "Written by one.txt.\n"
        round_count = len(requests)
        with ThreadPoolExecutor(max_workers=2) as pool:
            duplicate = list(pool.map(lambda _: execution.resolve_approval(page_id, action["actionId"], "allow_once"), range(2)))
        assert all(r["accepted"] and r["repeated"] for r in duplicate)
        assert len(requests) == round_count
        scope = runtime.kernel_service.store.action_scope_for_session(execution._scope(page_id), action["actionId"])
        assert runtime.kernel_service.store.action_terminal_count(scope, action["actionId"]) == 1
        with pytest.raises(ExecutionError) as conflict:
            execution.resolve_approval(page_id, action["actionId"], "deny")
        assert conflict.value.code == "approval_already_resolved"


def test_cross_page_action_and_turn_ids_cannot_control_another_page(tmp_path, monkeypatch):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        one = _new_page(client, "WRITE_ONE")
        two = _new_page(client, "WRITE_TWO")
        first = _wait(execution, one, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        second = _wait(execution, two, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        for wrong_page in (two, "home"):
            with pytest.raises(ExecutionError):
                execution.resolve_approval(wrong_page, first["pendingActions"][0]["actionId"], "allow_once")
            with pytest.raises(ExecutionError):
                execution.cancel(wrong_page, first["turnId"])
        assert execution.snapshot(one)["pendingActions"]
        assert execution.snapshot(two)["pendingActions"]
        stopped = execution.cancel(one, first["turnId"])
        assert stopped["accepted"] and not stopped["repeated"]
        assert execution.cancel(one, first["turnId"])["repeated"]
        assert execution.snapshot(two)["turnId"] == second["turnId"]
        assert execution.snapshot(two)["pendingActions"]
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert not (tmp_path / "workspace" / "two.txt").exists()
        execution.cancel(two, second["turnId"])


@pytest.mark.parametrize("decision", ["deny", "cancel"])
def test_rejected_action_never_writes_and_same_decision_is_observation(tmp_path, monkeypatch, decision):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action = waiting["pendingActions"][0]
        receipt = execution.resolve_approval(page_id, action["actionId"], decision)
        assert receipt["accepted"]
        _wait(execution, page_id, lambda p: p["status"] in {"idle", "error", "stopped"})
        round_count = len(requests)
        assert execution.resolve_approval(page_id, action["actionId"], decision)["repeated"]
        assert len(requests) == round_count
        assert not (tmp_path / "workspace" / "one.txt").exists()


def test_cancel_streaming_page_does_not_stop_home_and_late_reply_stays_cancelled(tmp_path, monkeypatch):
    requests, entered, release = [], threading.Event(), threading.Event()
    factory = _factory(monkeypatch, requests, entered=entered, release=release)
    with _client(tmp_path, model_factory=factory) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client, "HOLD_PROVIDER")
        try:
            assert entered.wait(5)
            working = _wait(execution, page_id, lambda p: p["status"] == "working")
            stopped = execution.cancel(page_id, working["turnId"])
            assert stopped["accepted"]
            assert stopped["execution"]["status"] == "stopped"
            _send(client, "home", "The Home request is independent.")
            _wait(execution, "home", lambda p: p["turnId"] is not None and p["status"] == "idle")
        finally:
            release.set()
        _wait(execution, page_id, lambda p: p["status"] == "stopped")
        assert execution.cancel(page_id, working["turnId"])["repeated"]
        assert execution.snapshot("home")["status"] == "idle"


def test_finished_turn_cancel_does_not_retarget_newer_work(tmp_path, monkeypatch):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client, "An ordinary response.")
        finished = _wait(execution, page_id, lambda p: p["turnId"] is not None and p["status"] == "idle")
        _send(client, page_id, "WRITE_ONE")
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        assert waiting["turnId"] != finished["turnId"]
        with pytest.raises(ExecutionError) as stale:
            execution.cancel(page_id, finished["turnId"])
        assert stale.value.code == "turn_already_finished"
        assert execution.snapshot(page_id)["pendingActions"]
        execution.cancel(page_id, waiting["turnId"])


def test_stop_can_interrupt_an_approval_continuation_without_waiting_for_it(tmp_path, monkeypatch):
    requests, entered, release = [], threading.Event(), threading.Event()
    factory = _factory(monkeypatch, requests, entered=entered, release=release)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client, "WRITE_ONE HOLD_AFTER_APPROVAL")
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            approval = pool.submit(execution.resolve_approval, page_id, action_id, "allow_once")
            try:
                assert entered.wait(5)
                stopped = pool.submit(execution.cancel, page_id, waiting["turnId"]).result(timeout=2)
                assert stopped["accepted"]
                assert stopped["execution"]["status"] == "stopped"
                # Cancellation does not erase a file effect that already ran.
                assert (tmp_path / "workspace" / "one.txt").read_text() == "Written by one.txt.\n"
            finally:
                release.set()
            assert approval.result(timeout=5)["accepted"]
        assert not execution._control_locks
        assert execution.snapshot(page_id)["status"] == "stopped"


def test_restart_keeps_control_receipts_without_replaying_effects(tmp_path, monkeypatch):
    requests = []
    factory = _factory(monkeypatch, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        execution.resolve_approval(page_id, action_id, "allow_once")
        _wait(execution, page_id, lambda p: p["status"] == "idle")
    rounds = len(requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        assert execution.resolve_approval(page_id, action_id, "allow_once")["repeated"]
        assert len(requests) == rounds
        assert (tmp_path / "workspace" / "one.txt").read_text() == "Written by one.txt.\n"


def test_shutdown_with_pending_approval_cannot_replay_it_after_restart(tmp_path, monkeypatch):
    requests = []
    factory = _factory(monkeypatch, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        assert not (tmp_path / "workspace" / "one.txt").exists()
    rounds = len(requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        snapshot = execution.snapshot(page_id)
        assert all(not a["canResolve"] for a in snapshot["pendingActions"])
        with pytest.raises(ExecutionError):
            execution.resolve_approval(page_id, action_id, "allow_once")
        assert len(requests) == rounds
        assert not (tmp_path / "workspace" / "one.txt").exists()


def test_stop_preserves_next_queued_input_without_old_recovery_blocking_new_turn(tmp_path, monkeypatch):
    requests, entered, release = [], threading.Event(), threading.Event()
    factory = _factory(monkeypatch, requests, entered=entered, release=release)
    with _client(tmp_path, model_factory=factory) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client, "HOLD_PROVIDER")
        try:
            assert entered.wait(5)
            first = _wait(execution, page_id, lambda p: p["status"] == "working")
            queued = _send(client, page_id, "FOLLOW_UP_MARKER: use this next instruction.")
            assert any(m["id"] == queued["messageId"] for m in queued["view"]["messages"])
            assert execution.cancel(page_id, first["turnId"])["accepted"]
        finally:
            release.set()
        latest = _wait(execution, page_id, lambda p: p["turnId"] != first["turnId"] and p["status"] == "idle")
        assert latest["historicalUncertainty"] is True
        assert latest["recoveryRequired"] is False
        assert "FOLLOW_UP_MARKER" in json.dumps(requests[-1]["messages"])
        # A retried old stop observes its own cancelled receipt. It cannot stop
        # the later turn or change the surviving queued message identity.
        replay = execution.cancel(page_id, first["turnId"])
        assert replay["repeated"] is True
        assert replay["execution"]["turnId"] == latest["turnId"]
        assert replay["execution"]["status"] == "idle"


def test_native_turn_deadline_cancels_pending_action_without_file_effect(tmp_path, monkeypatch):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        runtime._mgr._harness_turn_timeout_sec = 0.75
        execution = PageExecution(runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        stopped = _wait(execution, page_id, lambda p: p["status"] == "stopped" and not p["pendingActions"])
        assert not stopped["pendingActions"]
        with pytest.raises(ExecutionError):
            execution.resolve_approval(page_id, action_id, "allow_once")
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert len(requests) == 1


@pytest.mark.parametrize("name,arguments,in_work", [
    ("work", {"op": "create", "title": "Unapproved work", "instruction": "Do not schedule this."}, False),
    ("team", {"op": "establish", "name": "Unapproved collaborator", "instruction": "Do not create this participant."}, True),
    ("local", {"op": "set_excitability", "value": 1.0}, False),
])
def test_readonly_profile_rejects_inherited_internal_mutations(tmp_path, monkeypatch, name, arguments, in_work):
    requests = []
    factory = _factory(monkeypatch, requests, tool=(name, arguments))
    with _client(tmp_path, model_factory=factory) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        initial_subjects = {e.id for e in runtime.storage.list_engrams()}
        if in_work:
            page_id = _new_page(client, "Read-only workspace.")
        else:
            page_id = "home"
            _send(client, page_id, "Read-only workspace.")
        _wait(execution, page_id, lambda p: p["turnId"] is not None and p["status"] in {"idle", "error", "needs_attention"})
        assert {e.id for e in runtime.storage.list_engrams()} == initial_subjects
        assert len(runtime.list_task_fronts()) == int(in_work)
        catalog = {t["function"]["name"]: t["function"] for t in requests[0]["tools"]}
        assert "mutate_file" not in catalog
        assert "run_command" not in catalog
        assert catalog["local"]["parameters"]["properties"]["op"]["enum"] == ["inspect"]
        actions = runtime.kernel_service.store.actions_for_session(execution._scope(page_id), limit=8)
        assert len(actions) == 1
        assert actions[0].status is ActionStatus.FAILED
        assert actions[0].error_code == "scope_conflict"


def test_pending_approval_without_original_attachment_is_visible_but_cannot_execute(tmp_path, monkeypatch):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        # Simulate the exact missing physical attachment seen after recovery,
        # keeping all durable state unchanged. Native guard remains decisive.
        scope = execution._scope(page_id).for_turn(waiting["turnId"])
        key = (scope.engram_id, scope.context_id)
        with runtime.harness._lock:
            attached = runtime.harness._active.pop(key)
        try:
            state = execution.snapshot(page_id)
            assert state["pendingActions"][0]["canResolve"] is False
            assert state["canCancel"] is True
            with pytest.raises(ExecutionError):
                execution.resolve_approval(page_id, action_id, "allow_once")
            assert not (tmp_path / "workspace" / "one.txt").exists()
        finally:
            with runtime.harness._lock:
                runtime.harness._active[key] = attached
        execution.cancel(page_id, waiting["turnId"])


def test_invalid_decision_never_changes_pending_authority(tmp_path, monkeypatch):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        with pytest.raises(ExecutionError) as error:
            execution.resolve_approval(page_id, action_id, "allow_always")
        assert error.value.code == "approval_decision_invalid"
        scope = execution._scope(page_id).for_turn(waiting["turnId"])
        assert runtime.kernel_service.store.get_turn(scope).status is TurnStatus.WAITING_APPROVAL
        assert runtime.kernel_service.store.get_action(scope, action_id).status is ActionStatus.APPROVAL_PENDING
        execution.cancel(page_id, waiting["turnId"])


def _persist_unattached_turn(tmp_path, factory, *, effect_starting=False):
    """Persist a crash-window fixture through real Kernel transitions.

    There is deliberately no live World worker for this authored old turn.
    Stopping the source closes its stores while retaining the unresolved
    Kernel action, as a process loss between admission and settlement can do.
    """
    pages = PageService(tmp_path, model_factory=factory, allow_workspace_actions=True)
    try:
        page_id = pages.create_page("Recovered page", "Continue the queued page after recovery.")["page"]["id"]
        scope = pages.execution._scope(page_id).for_turn("persisted-unattached-turn")
        kernel = pages.runtime.kernel_service
        kernel.store.create_session(scope.session_scope())
        native = kernel._runtime(scope)
        outcome = native.runner.run(scope, prompt="WRITE_ONE")
        assert outcome.status is TurnStatus.WAITING_APPROVAL
        action_id = outcome.pending_action_request_id
        assert action_id is not None
        if effect_starting:
            # This is exactly the durable boundary after accepting the action
            # and before its outcome has been recorded. Recovery must retain
            # uncertainty, not claim that cancellation undid a physical effect.
            kernel.store.resolve_approval(scope, action_id, ApprovalDecision.ALLOW_ONCE)
            assert kernel.store.get_action(scope, action_id).status is ActionStatus.STARTING
        return page_id, scope.turn_id, action_id
    finally:
        asyncio.run(pages.close())


@pytest.mark.parametrize("effect_starting", [False, True])
def test_recovered_turn_can_stop_offline_without_constructing_model_or_tools(tmp_path, monkeypatch, effect_starting):
    requests = []
    factory = _factory(monkeypatch, requests)
    page_id, turn_id, action_id = _persist_unattached_turn(tmp_path, factory, effect_starting=effect_starting)
    rounds_before = len(requests)
    with _client(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        execution, kernel = pages.execution, pages.runtime.kernel_service
        assert not pages.configured
        assert not kernel._runtimes
        before = execution.snapshot(page_id)
        assert before["canCancel"] and before["recoveryRequired"]
        assert len(before["uncertainActions"]) == int(effect_starting)
        if effect_starting:
            assert before["uncertainActions"][0]["actionId"] == action_id
            assert before["uncertainActions"][0]["status"] == "uncertain"
        assert all(not a["canResolve"] for a in before["pendingActions"])
        if not effect_starting:
            with pytest.raises(ExecutionError):
                execution.resolve_approval(page_id, action_id, "allow_once")

        def forbidden(*_args, **_kwargs):
            pytest.fail("Stopping recovered work must not construct a model or tool provider.")

        monkeypatch.setattr(kernel, "_model_adapter_factory", forbidden)
        monkeypatch.setattr(kernel, "_tool_provider_factory", forbidden)
        monkeypatch.setattr(kernel, "_response_reviewer_factory", forbidden)
        monkeypatch.setattr(kernel, "_turn_advisor_factory", forbidden)
        closed_control_runners = []
        original_close = TurnRunner.close

        def record_close(runner):
            if runner.model_adapter is None:
                closed_control_runners.append(id(runner))
            original_close(runner)

        monkeypatch.setattr(TurnRunner, "close", record_close)
        response = client.post(f"/api/pages/{page_id}/execution/stop", json={"turnId": turn_id})
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["accepted"]
        assert receipt["execution"]["status"] == "stopped"
        assert not receipt["execution"]["recoveryRequired"]
        scope = execution._scope(page_id).for_turn(turn_id)
        action = kernel.store.get_action(scope, action_id)
        expected = ActionStatus.UNCERTAIN if effect_starting else ActionStatus.CANCELLED
        assert action.status is expected
        assert receipt["execution"]["historicalUncertainty"] is effect_starting
        if effect_starting:
            assert receipt["execution"]["uncertainActions"][0]["status"] == "uncertain"
        assert not kernel._runtimes
        assert len(closed_control_runners) == 1
        assert len(requests) == rounds_before
        assert not (tmp_path / "workspace" / "one.txt").exists()
        assert execution.cancel(page_id, turn_id)["repeated"]

    # A control-only stop must not install a model-less working runner. After
    # explicit reconnection, the real queued input obtains its normal adapter.
    reconnected = _factory(monkeypatch, requests, respond_only=True)
    with _client(tmp_path, model_factory=reconnected) as client:
        execution = client.app.state.pages.execution
        latest = _wait(execution, page_id, lambda p: (
            p["turnId"] != turn_id and p["status"] == "idle"
            and any(m["role"] == "assistant" for m in client.get(f"/api/pages/{page_id}").json()["messages"])
        ))
        assert latest["recoveryRequired"] is False
        assert len(requests) > rounds_before
        assert any(m["role"] == "assistant" for m in client.get(f"/api/pages/{page_id}").json()["messages"])
        if effect_starting:
            scope = execution._scope(page_id).for_turn(turn_id)
            assert client.app.state.pages.runtime.kernel_service.store.get_action(scope, action_id).status is ActionStatus.UNCERTAIN
            assert latest["uncertainActions"][0]["actionId"] == action_id
            assert execution.snapshot("home")["uncertainActions"] == []


def test_uncertain_action_projection_is_bounded_redacted_and_observation_only(tmp_path, monkeypatch):
    """Use native action transitions, including retained old terminal records.

    These read-only fixture actions do not claim any real physical write. The
    separate process-kill test covers approval and an actual filesystem effect.
    """
    import per_pulse.execution as module
    from pulse_harness.core.contracts import ActionClassification, TerminalOutcome

    with _client(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        work = _new_page(client, "Keep private action history here.")
        scope = pages.execution._scope(work).for_turn("fixture-unconfirmed-history")
        store = pages.runtime.kernel_service.store
        store.create_session(scope.session_scope())
        store.create_turn(scope, "Synthetic interrupted actions")
        for index in range(3):
            action_id = f"fixture-uncertain-{index}"
            store.create_action(scope, action_request_id=action_id, tool_name="fixture_read",
                                arguments={"path": f"private-{index}.txt", "api_key": "SYNTHETIC_SECRET_VALUE"},
                                requires_approval=False, classification=ActionClassification.READ_ONLY_NOT_REQUIRED)
            store.claim_action_start(scope, action_id)
            store.mark_action_running(scope, action_id)
            store.terminal_action(scope, action_id, TerminalOutcome.UNCERTAIN)
        # Bound the read to two instead of manufacturing dozens of identical
        # records. The third record must make incompleteness explicit.
        monkeypatch.setattr(module, "_MAX_ACTIONS", 2)
        state = client.get(f"/api/pages/{work}/execution").json()
        assert [a["actionId"] for a in state["uncertainActions"]] == ["fixture-uncertain-2", "fixture-uncertain-1"]
        assert state["uncertainActionsTruncated"] is True
        assert all(a["argumentsRedacted"] for a in state["uncertainActions"])
        assert "SYNTHETIC_SECRET_VALUE" not in json.dumps(state)
        assert not pages.runtime.kernel_service._runtimes
        assert pages.execution.snapshot("home")["uncertainActions"] == []
        assert all(store.action_terminal_count(scope, f"fixture-uncertain-{i}") == 1 for i in range(3))


def test_completed_effect_is_not_reported_as_rejected_when_continuation_is_redacted(tmp_path, monkeypatch):
    before = "Draft heading.\npassword=abc\nOrdinary report content.\n"
    after = before.replace("Draft heading.", "Final heading.")
    requests = []
    factory = _factory(monkeypatch, requests, tool=("mutate_file", {
        "path": "report.txt", "expected_digest": hashlib.sha256(before.encode()).hexdigest(),
        "patch": {"edits": [{"old": "Draft heading.", "new": "Final heading."}]},
    }))
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        path = tmp_path / "workspace" / "report.txt"
        path.write_text(before)
        pages = client.app.state.pages
        page_id = _new_page(client, "Update the report heading.")
        waiting = _wait(pages.execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        response = client.post(f"/api/pages/{page_id}/execution/actions/{action_id}", json={"decision": "allow_once"})
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["accepted"] and receipt["actionCompleted"]
        assert receipt["actionStatus"] == "succeeded"
        assert receipt["continuationError"]["code"] == "redaction_required"
        assert "action completed" in receipt["continuationError"]["message"].lower()
        # Check the actual private value, not a three-letter substring that
        # can occur by chance inside unrelated generated UUIDs and IDs.
        assert "password=abc" not in json.dumps(receipt)
        assert path.read_text() == after
        assert pages.artifacts.read(page_id, action_id)["state"] == "current"
        round_count = len(requests)
        duplicate = pages.execution.resolve_approval(page_id, action_id, "allow_once")
        assert duplicate["accepted"] and duplicate["repeated"] and duplicate["actionCompleted"]
        assert len(requests) == round_count
        scope = pages.execution._scope(page_id).for_turn(waiting["turnId"])
        assert pages.runtime.kernel_service.store.action_terminal_count(scope, action_id) == 1
        pages.execution.cancel(page_id, waiting["turnId"])


def test_artifact_reads_and_stop_stay_available_while_another_page_waits_for_workspace(tmp_path, monkeypatch):
    from pulse_harness.adapters.resources import workspace_write_resource

    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        pages = client.app.state.pages
        execution = pages.execution
        first = _new_page(client, "WRITE_ONE")
        waiting = _wait(execution, first, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        first_action = waiting["pendingActions"][0]["actionId"]
        assert execution.resolve_approval(first, first_action, "allow_once")["actionCompleted"]
        second = _new_page(client, "WRITE_TWO")
        waiting = _wait(execution, second, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        second_action = waiting["pendingActions"][0]["actionId"]
        scope = execution._scope(second).for_turn(waiting["turnId"])
        resource = workspace_write_resource(tmp_path / "workspace")
        with ThreadPoolExecutor(max_workers=3) as pool:
            with resource.write():
                approval = pool.submit(execution.resolve_approval, second, second_action, "allow_once")
                deadline = time.monotonic() + 3
                while pages.runtime.kernel_service.store.get_action(scope, second_action).status is not ActionStatus.RUNNING:
                    assert time.monotonic() < deadline, "The approved writer must reach the resource wait."
                    time.sleep(0.01)
                observed = pool.submit(client.get, f"/api/pages/{first}/artifacts/{first_action}").result(timeout=2)
                assert observed.status_code == 200, observed.text
                assert observed.json()["content"] == "Written by one.txt.\n"
                home = pool.submit(client.get, "/api/pages/home").result(timeout=2)
                assert home.status_code == 200, home.text
                stopped = pool.submit(execution.cancel, second, waiting["turnId"]).result(timeout=2)
                assert stopped["accepted"]
                receipt = approval.result(timeout=2)
                assert receipt["accepted"] and not receipt["actionCompleted"]
                assert not (tmp_path / "workspace" / "two.txt").exists()
        assert pages.artifacts.read(first, first_action)["state"] == "current"
