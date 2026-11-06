"""Actual command observations enter the existing experience and Pulse path.

The HTTP model and Docker control ports are explicit fixtures; native history,
subprocess pipes, approvals, persistence and experience admission are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from command_example_fixture import CommandExampleFixture
from command_output_fixture import LocalPipeCommandExecutor
from pulse_harness.core.contracts import ActionStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_system.service import kernel_runtime
from pulse_system.service.command_experience import capture_command_example
from pulse_system.service.native_posture import validate
from test_pages import client_for, send, wait_for
from test_reading_continuity import work


def choose(client, page, fixture, **command):
    count = len(fixture.outcomes)
    sent = send(client, page, "COMMAND_EXAMPLE_FIXTURE:" + json.dumps(command))
    wait_for(client, page, lambda view: any(message["id"] == sent["messageId"]
             and message["status"] == "settled" for message in view["messages"]))
    assert len(fixture.outcomes) == count + 1
    result = fixture.outcomes[-1]
    if result[-1].get("output", {}).get("status") == "prepared":
        identity = result[-1]["output"]["experience_id"]
        wait_for(client, page, lambda _: any(item["id"] == identity for item in client.get(
            f"/api/pages/{page}/posture").json()["experiences"]))
    return result


def run(client, page):
    sent = send(client, page, 'COMMAND_EXAMPLE_FIXTURE:{"op":"run"}')
    waiting = wait_for(client, page, lambda view: bool(view["execution"]["pendingActions"]))
    action = waiting["execution"]["pendingActions"][0]["actionId"]
    approved = client.post(f"/api/pages/{page}/execution/actions/{action}", json={"decision": "allow_once"})
    assert approved.status_code == 200, approved.text
    wait_for(client, page, lambda view: any(message["id"] == sent["messageId"]
             and message["status"] == "settled" for message in view["messages"]))
    return action


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("P_PULSE_COMMAND_IMAGE", "explicit-pipe-fixture")
    monkeypatch.setattr(kernel_runtime, "DockerCommandExecutor", LocalPipeCommandExecutor)
    return CommandExampleFixture(monkeypatch.setenv)


def read_and_keep(client, page, fixture, action, **retain):
    return choose(client, page, fixture, op="read", action_id=action, stream="stderr", offset=4000, limit=8000,
                  retain={"title": "A command observation", "excerpt": "STDERR_FINAL_DIAGNOSIS", **retain})


def test_command_example_preserves_delivered_range_exit_and_source_after_restart(tmp_path, setup, monkeypatch):
    fixture = setup
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        results = read_and_keep(client, page, fixture, action, note="Keep the failure diagnosis available.")
        assert [item["status"] for item in results] == ["succeeded", "succeeded"]
        read = results[0]
        saved, = client.get(f"/api/pages/{page}/posture").json()["experiences"]
        assert saved["sourceKind"] == "command" and saved["record_type"] == "constructed_fixture"
        assert saved["source"]["selector_evidence_class"] == "TEST_DOUBLE"
        receipt = read["output"]["receipt_id"]
        assert saved["source"]["command_receipt_id"] == receipt
        assert any(receipt in item["content"] for request in fixture.requests
                   for item in request["messages"] if item["role"] == "tool")
        assert saved["source"]["command_action_id"] == action
        assert saved["commandSource"] == {"stream": "stderr", "exitCode": 7, "captureComplete": True}
        assert saved["source"]["capture_sha256"] == read["output"]["sha256"]
        assert saved["source"]["delivered_range"]["sha256"] == hashlib.sha256(read["output"]["content"].encode()).hexdigest()
        selected = saved["source"]["selected_excerpt"]
        relative = selected["start"] - read["output"]["offset"]
        assert read["output"]["content"][relative:relative + len(saved["excerpt"])] == saved["excerpt"]
        assert "outcome" not in saved["encounter"] and saved["feedback"] == []
        assert "diagnostic progress" not in json.dumps(saved)
        runtime = client.app.state.pages.runtime
        selecting_scope = runtime.kernel_service.store.action_scope_for_session(
            runtime.harness.scope_for(runtime.continuity_engram_id, saved["source"]["context_id"]),
            saved["source"]["source_action_id"])
        selecting_action = runtime.kernel_service.store.get_action(selecting_scope, saved["source"]["source_action_id"])
        assert selecting_action.arguments["receipt_id"] == receipt
        calls = runtime.harness._command_executor.calls
        command = client.app.state.pages.execution._command_action(page, action)
        capture = command.result["output"]["output_capture"]["stderr"]
        (runtime.kernel_service.checkpoint_root / "command-output" / capture["file"]).unlink()
        # The already delivered observation survives loss of its capture file.
        again = choose(client, page, fixture, op="remember_command", receipt_id=receipt,
                       title="The same observed diagnosis", excerpt=saved["excerpt"])
        assert again[0]["status"] == "succeeded" and runtime.harness._command_executor.calls == calls == 1
        receiver = work(client)
        shared = next(item for item in client.get(f"/api/pages/{receiver}/posture").json()["experiences"]
                      if item["id"] == saved["id"])
        assert shared["commandSource"] == saved["commandSource"] and shared["sourceKind"] == "command"
        assert not {"source", "encounter", "feedback"} & set(shared)
        assert action not in json.dumps(shared) and receipt not in json.dumps(shared)
        packet = runtime.harness.posture_memory.materials(subject_id=runtime.continuity_engram_id,
                                                         context_id=client.app.state.pages.learning._context(receiver))
        offered = next(item for item in packet["episodes"] if item["id"] == saved["id"])
        assert offered["source"]["kind"] == "native_command_example"
        assert offered["source"]["command_observation"]["exit_code"] == 7
        source = saved["source"]
        assert source["observed_at"] and source["command_observed_at"]
        assert saved["sourceAnchor"]["resultMessageId"]
    monkeypatch.delenv("P_PULSE_COMMAND_IMAGE")
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        restored = next(item for item in client.get(f"/api/pages/{page}/posture").json()["experiences"]
                        if item["id"] == saved["id"])
        assert restored == saved
        before = len(fixture.before_action)
        choose(client, receiver, fixture, op="get", experience_id=saved["id"])
        first = fixture.before_action[before]
        assert first["native_model_round"] == 0
        packet = first["posture_experiences"]
        assert {"id": saved["id"], "revision": saved["revision"]} in packet["offered_posture_revisions"]
        assert next(item for item in packet["episodes"] if item["id"] == saved["id"])["excerpt"] == saved["excerpt"]


def test_only_exact_delivered_reads_from_this_work_can_be_kept(tmp_path, setup):
    fixture = setup
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page, other = work(client), work(client)
        action = run(client, page)
        read = choose(client, page, fixture, op="read", action_id=action, stream="stderr", offset=4000, limit=8000)[0]
        for target, receipt, excerpt in [
            (other, read["output"]["receipt_id"], "STDERR_FINAL_DIAGNOSIS"),
            (page, action, "STDERR_FINAL_DIAGNOSIS"),
            (page, "invented-read", "STDERR_FINAL_DIAGNOSIS"),
            (page, read["output"]["receipt_id"], "STDOUT_FINAL_DIAGNOSIS"),
            (page, read["output"]["receipt_id"], "stderr_final_diagnosis"),
        ]:
            result = choose(client, target, fixture, op="remember_command", receipt_id=receipt,
                            title="Unavailable observation", excerpt=excerpt)[0]
            assert result["status"] == "failed", result
        assert client.get(f"/api/pages/{page}/posture").json()["experiences"] == []


def test_same_response_read_is_unseen_but_a_later_quiet_round_can_keep_it(tmp_path, setup):
    fixture = setup
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        results = choose(client, page, fixture, op="same_batch", read={
            "action_id": action, "stream": "stderr", "offset": 4000, "limit": 8000})
        assert [item["status"] for item in results] == ["succeeded", "failed"]
        assert client.get(f"/api/pages/{page}/posture").json()["experiences"] == []
        fixture.quiet = {"op": "remember_command", "receipt_id": results[0]["output"]["receipt_id"],
                         "title": "An observation kept during quiet work", "excerpt": "STDERR_FINAL_DIAGNOSIS"}
        response = client.post(f"/api/pages/{page}/tick", json={"count": 1})
        assert response.status_code == 201, response.text
        wait_for(client, page, lambda view: view["tick"]["status"] == "complete")
        # The durable turn settles before the existing posture outbox is
        # admitted later in the runtime tick. Observe that separate result.
        wait_for(client, page, lambda _: len(client.get(
            f"/api/pages/{page}/posture").json()["experiences"]) == 1)
        saved, = client.get(f"/api/pages/{page}/posture").json()["experiences"]
        assert saved["excerpt"] == "STDERR_FINAL_DIAGNOSIS" and saved["feedback"] == []
        assert client.app.state.pages.runtime.harness._command_executor.calls == 1


def test_receipt_remains_available_across_committed_same_work_succession(tmp_path, setup):
    fixture = setup
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        read = choose(client, page, fixture, op="read", action_id=action, stream="stderr", offset=4000, limit=8000)[0]["output"]
        runtime = client.app.state.pages.runtime
        original = runtime.continuity_engram_id
        fronts = runtime.list_task_fronts()
        center = next(front["center_id"] for front in fronts if front["id"] == page)
        source = runtime.causal_ledger.get_turn_for_world(read["source"]["turn_id"], runtime.world_id).result_event_id
        prepared = runtime._mgr.prepare_succession(original, parent_event_id=source,
                                                  runtime_fence=runtime._current_runtime_fence())
        successor = runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence()).new_id
        result = choose(client, page, fixture, op="remember_command", receipt_id=read["receipt_id"],
                        title="The original diagnosis", excerpt="STDERR_FINAL_DIAGNOSIS")[0]
        assert result["status"] == "succeeded", result
        saved, = client.get(f"/api/pages/{page}/posture").json()["experiences"]
        assert saved["source"]["subject_id"] == successor
        assert saved["source"]["source_subject_id"] == saved["source"]["command_subject_id"] == original
        assert saved["source"]["context_id"] == "work:" + center
        assert saved["source"]["original_turn_id"] != saved["source"]["learning_turn_id"]


def test_safe_selection_from_redacted_incomplete_capture_keeps_the_facts(tmp_path, setup, monkeypatch):
    fixture = setup
    script = "import sys;sys.stderr.write('password=synthetic-sensitive\\nSAFE_SELECTED_LINE\\n'+'x'*1100000);sys.exit(7)"
    monkeypatch.setattr(kernel_runtime, "DockerCommandExecutor", lambda root, image:
                        LocalPipeCommandExecutor(root, script=script))
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        results = choose(client, page, fixture, op="read", action_id=action, stream="stderr", offset=0, limit=100,
                         retain={"title": "A bounded observation", "excerpt": "SAFE_SELECTED_LINE"})
        assert results[-1]["status"] == "succeeded", results
        saved, = client.get(f"/api/pages/{page}/posture").json()["experiences"]
        assert saved["commandSource"] == {"stream": "stderr", "exitCode": 7, "captureComplete": False}
        assert saved["source"]["command_observation"]["redacted"] is True
        assert saved["source"]["saved_bytes"] < saved["source"]["observed_bytes"]
        assert "synthetic-sensitive" not in json.dumps(saved)


@pytest.mark.parametrize("change", ["pending", "failed", "undelivered", "wrong_receipt", "wrong_digest"])
def test_unavailable_or_mismatched_receipt_cannot_be_reclassified(tmp_path, setup, change):
    fixture = setup
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        read_and_keep(client, page, fixture, action)
        pages = client.app.state.pages
        runtime, store = pages.runtime, pages.runtime.kernel_service.store
        saved, = runtime.harness.posture_memory.list_experiences(subject_id=runtime.continuity_engram_id)
        scope = store.action_scope_for_session(runtime.harness.scope_for(runtime.continuity_engram_id,
            pages.learning._context(page)), saved["source"]["source_action_id"])
        original_get = store.get_action

        def get(candidate, identity):
            record = original_get(candidate, identity)
            if identity != saved["source"]["command_receipt_id"]:
                return record
            if change == "pending":
                return replace(record, status=ActionStatus.RUNNING)
            if change == "failed":
                return replace(record, status=ActionStatus.FAILED)
            if change == "undelivered":
                return replace(record, result_delivered=False)
            result = deepcopy(record.result)
            result["output"]["receipt_id" if change == "wrong_receipt" else "sha256"] = "different"
            return replace(record, result=result)

        public_runtime = SimpleNamespace(world_id=runtime.world_id, harness=runtime.harness,
            causal_ledger=runtime.causal_ledger, native_kernel_context_for_turn=runtime.native_kernel_context_for_turn,
            kernel_service=SimpleNamespace(store=SimpleNamespace(get_action=get,
                action_scope_for_session=store.action_scope_for_session,
                read_transcript_message=store.read_transcript_message, transcript_rows=store.transcript_rows)))
        with pytest.raises(ScopeConflict):
            capture_command_example(public_runtime, scope, saved["source"]["command_receipt_id"], saved["excerpt"],
                                    selecting_action_id=saved["source"]["source_action_id"])


def test_later_read_cannot_be_used_by_an_earlier_originating_call(tmp_path, setup):
    fixture = setup
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        read_and_keep(client, page, fixture, action)
        runtime = client.app.state.pages.runtime
        saved, = runtime.harness.posture_memory.list_experiences(subject_id=runtime.continuity_engram_id)
        later = choose(client, page, fixture, op="read", action_id=action, stream="stderr", offset=4000, limit=8000)[0]["output"]
        store = runtime.kernel_service.store
        session = runtime.harness.scope_for(runtime.continuity_engram_id, saved["context_id"])
        original = store.action_scope_for_session(session, saved["source"]["source_action_id"])
        with pytest.raises(ScopeConflict, match="before the originating model call"):
            capture_command_example(runtime, original, later["receipt_id"], saved["excerpt"],
                                    selecting_action_id=saved["source"]["source_action_id"])


def test_legacy_delivered_receipt_remains_usable_without_reopening_capture(tmp_path, setup, monkeypatch):
    fixture = setup
    execute = kernel_runtime._WorldWorkspace.execute_for_scope

    def legacy_read(provider, scope, call, cancellation, **kwargs):
        result = execute(provider, scope, call, cancellation, **kwargs)
        if call.name == "read_command_output":
            result.pop("receipt_id", None)
        return result

    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        with monkeypatch.context() as patch:
            patch.setattr(kernel_runtime._WorldWorkspace, "execute_for_scope", legacy_read)
            read = choose(client, page, fixture, op="read", action_id=action, stream="stderr", offset=4000, limit=8000)[0]
        assert "receipt_id" not in read["output"]
        pages = client.app.state.pages
        runtime, store = pages.runtime, pages.runtime.kernel_service.store
        scope = runtime.harness.scope_for(runtime.continuity_engram_id, pages.learning._context(page))
        receipt, = [record for record in store.actions_for_session(scope)
                    if record.tool_name == "read_command_output"]
        command = store.get_action(store.action_scope_for_session(scope, action), action)
        capture = command.result["output"]["output_capture"]["stderr"]
        (runtime.kernel_service.checkpoint_root / "command-output" / capture["file"]).unlink()
        result = choose(client, page, fixture, op="remember_command", receipt_id=receipt.action_request_id,
                        title="An earlier observed diagnosis", excerpt="STDERR_FINAL_DIAGNOSIS")[0]
        assert result["status"] == "succeeded", result
        assert runtime.harness._command_executor.calls == 1


def test_failed_selecting_turn_and_outbox_receipt_retry_preserve_admission_semantics(tmp_path, setup, monkeypatch):
    fixture = setup
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        fixture.fail_after_prepare = True
        sent = send(client, page, "COMMAND_EXAMPLE_FIXTURE:" + json.dumps({
            "op": "read", "action_id": action, "stream": "stderr", "offset": 4000, "limit": 8000,
            "retain": {"title": "A failed selector", "excerpt": "STDERR_FINAL_DIAGNOSIS"}}))
        wait_for(client, page, lambda view: any(message["id"] == sent["messageId"] and message["status"] in {
            "failed", "uncertain"} for message in view["messages"]))
        runtime = client.app.state.pages.runtime
        runtime._native_posture.drain()
        assert client.get(f"/api/pages/{page}/posture").json()["experiences"] == []
        fixture.fail_after_prepare = False
        # Hold receipt finishing after the real journal commit, then retry the
        # original outbox drain: the same action cannot create two examples.
        finish = runtime._native_posture._finish
        with monkeypatch.context() as patch:
            patch.setattr(runtime._native_posture, "_finish", lambda *args, **kwargs: None)
            read_and_keep(client, page, fixture, action)
        runtime._native_posture.drain()
        runtime._native_posture.drain()
        assert len(client.get(f"/api/pages/{page}/posture").json()["experiences"]) == 1


@pytest.mark.parametrize("extra", [{"feedback_message_id": "invented"}, {"scope": "subject"},
                                   {"exit_code": 0}, {"lesson": "Promote a rule"}])
def test_command_capture_cannot_supply_authority_or_outcome(extra):
    with pytest.raises(ValueError):
        validate({"op": "remember_command", "receipt_id": "real-read", "title": "Example",
                  "excerpt": "Exact observed passage", **extra})
