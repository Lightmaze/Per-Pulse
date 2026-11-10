"""Native Habitat region is shared by separate Home/Work histories in one World.

The model replies are a declared synthetic HTTP fixture. World, Kernel,
ManagedHabitat journal and filesystem are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib

import pytest

from per_pulse.service import PageService, PulseSettings
from per_pulse.execution import PageExecution
from test_execution import _wait
from test_pages import client_for, new_page, wait_for
from pulse_system.service.native_habitat import OBSERVE, WRITE, validate
from test_internal_work_organization import WorkFixture, run_event


@pytest.mark.parametrize("name,values", [
    (OBSERVE, {"op": "list", "region": "../home"}),
    (OBSERVE, {"op": "read", "region": "lab", "file": "../../secrets"}),
    (OBSERVE, {"op": "read", "region": "lab", "file": ".managed-state"}),
    (WRITE, {"op": "write", "region": "lab/../other", "file": "note.txt", "content": "x"}),
    (WRITE, {"op": "append", "region": "lab", "file": "a.txt", "content": "x\x00"}),
])
def test_region_cannot_escape_its_world_directory(name, values):
    with pytest.raises(ValueError):
        validate(name, values)


@pytest.mark.parametrize("auto_apply_edits", [False, True])
def test_two_distinct_works_revisit_one_persistent_region(tmp_path, monkeypatch, auto_apply_edits):
    """Both ordinary and auto-apply profiles retain the actual approval boundary.

    The action decisions are synthetic HTTP responses; permissions, physical
    files, World/Kernel state and reopened SQLite are real.
    """
    fixture = WorkFixture(monkeypatch.setenv)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        adaptive_connections=False, spectrum_enabled=False,
    )

    def open_client():
        return client_for(
            tmp_path, model_factory=fixture.factory,
            allow_workspace_actions=True, auto_apply_edits=auto_apply_edits,
            posture=False, pulse=settings,
        )

    def approve_once(client, page_id, target):
        execution = PageExecution(client.app.state.pages.runtime)
        waiting = _wait(execution, page_id, lambda view:
            bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action = waiting["pendingActions"][0]
        assert action["toolName"] == WRITE
        assert not target.exists() or target.read_text() == "An actual first observation.\n"
        receipt = execution.resolve_approval(page_id, action["actionId"], "allow_once")
        assert receipt["continuationError"] is None, receipt
        wait_for(client, page_id, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        return action["actionId"]

    with open_client() as client:
        pages = client.app.state.pages
        target = pages.runtime.habitat.root / "regions/garden/notes.txt"
        fixture.plan = [(WRITE, {
            "op": "write", "region": "garden", "file": "notes.txt",
            "content": "An actual first observation.\n",
        })]
        a = new_page(client, "Visit a place", "Make one real local observation.")["page"]["id"]
        action_a = approve_once(client, a, target)
        result = fixture.outcomes[0]
        assert result["status"] == "succeeded", result
        receipt = result["output"]["effectReceipt"]
        assert receipt["terminal_state"] == "committed"
        assert receipt["before_digest"] is None
        assert receipt["after_digest"] == hashlib.sha256(b"An actual first observation.\n").hexdigest()
        assert receipt["correlation_id"] == action_a
        assert target.read_text() == "An actual first observation.\n"

        fixture.plan = [(OBSERVE, {"op": "read", "region": "garden", "file": "notes.txt"})]
        b = new_page(client, "Return to the place", "Read what is already there.")["page"]["id"]
        wait_for(client, b, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        assert fixture.outcomes[0]["status"] == "succeeded"
        assert fixture.outcomes[0]["output"]["current"] == "An actual first observation.\n"
        assert fixture.outcomes[0]["output"]["lastVerifiedWrite"]["effectReceipt"] == receipt
        assert fixture.outcomes[0]["output"]["lastVerifiedWrite"]["source"]["contextId"].startswith("work:")
        assert fixture.outcomes[0]["output"]["lastVerifiedWrite"]["source"]["kernelActionId"] == action_a
        assert len(pages.runtime.storage.list_engrams()) == 1

    with open_client() as client:
        pages = client.app.state.pages
        target = pages.runtime.habitat.root / "regions/garden/notes.txt"
        fixture.plan = [(WRITE, {
            "op": "append", "region": "garden", "file": "notes.txt",
            "content": "A second observation.\n",
        })]
        response = client.post(f"/api/pages/{b}/messages", json={
            "content": "Continue the shared regional observation."})
        assert response.status_code == 202, response.text
        action_b = approve_once(client, b, target)
        answer = fixture.outcomes[0]
        assert answer["status"] == "succeeded", answer
        after = "An actual first observation.\nA second observation.\n"
        assert target.read_text() == after
        assert answer["output"]["effectReceipt"]["correlation_id"] == action_b
        assert answer["output"]["effectReceipt"]["before_digest"] == receipt["after_digest"]
        assert answer["output"]["effectReceipt"]["after_digest"] == hashlib.sha256(after.encode()).hexdigest()
        assert answer["output"]["writerIndex"] == "recorded"
        assert len(pages.runtime.storage.list_engrams()) == 1


def test_read_only_workspace_never_advertises_habitat_mutation(tmp_path):
    pages = PageService(tmp_path, model_factory=None, posture=False,
                        allow_workspace_actions=False)
    try:
        from pulse_system.service.native_habitat import descriptors
        assert any(d.name == WRITE and d.mutable and d.requires_approval
                   for d in descriptors())
        assert pages.runtime.harness._allow_mutations is False
        # A direct call cannot bypass the front-end's read-only configuration.
        from pulse_system.service.native_habitat import NativeHabitat
        from pulse_harness.core.contracts import ToolCall
        from pulse_harness.core.provider import CancellationToken
        from pulse_harness.core.errors import ScopeConflict
        from pulse_harness.core import HarnessScope
        scope = HarnessScope(pages.runtime.world_id,
                             pages.runtime.continuity_engram_id,
                             "untrusted-session", 0, "untrusted-owner",
                             context_id="", turn_id="untrusted-turn")
        with pytest.raises(ScopeConflict, match="disabled"):
            NativeHabitat(pages.runtime).execute(
                scope, ToolCall("x", WRITE, {"op":"write", "region":"garden",
                     "file":"notes.txt", "content":"not allowed"}),
                CancellationToken(), action_request_id="untrusted-action",
            )
        assert not (pages.runtime.habitat.root / "regions/garden/notes.txt").exists()
    finally:
        pages.runtime.close()


@pytest.mark.parametrize("decision", ["deny", "allow_once"])
@pytest.mark.parametrize("auto_apply_edits", [False, True])
def test_habitat_write_waits_for_real_native_approval(tmp_path, monkeypatch, decision, auto_apply_edits):
    """A model tool call cannot write before the user's decision is recorded.

    Only the HTTP model decision is synthetic. The page, original Kernel action,
    approval transaction, filesystem effect, and later model continuation are real.
    """
    fixture = WorkFixture(monkeypatch.setenv)
    fixture.plan = [(WRITE, {
        "op": "write", "region": "garden", "file": "approved.txt",
        "content": "Only if the owner allows once.\\n",
    })]
    with client_for(tmp_path, model_factory=fixture.factory,
                    allow_workspace_actions=True, auto_apply_edits=auto_apply_edits,
                    posture=False, pulse=PulseSettings(
                        enabled=True, spontaneous_rate=0,
                        adaptive_connections=False, spectrum_enabled=False)) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        page = new_page(client, "Ask before changing the garden",
                        "Write the new garden note only when approved.")["page"]["id"]
        target = runtime.habitat.root / "regions" / "garden" / "approved.txt"
        waiting = _wait(execution, page,
            lambda view: bool(view["pendingActions"])
            and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        assert waiting["pendingActions"][0]["toolName"] == WRITE
        assert not target.exists(), "approval pending must not write any file"
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(page), action_id)
        assert runtime.kernel_service.store.get_action(scope, action_id).approval_id
        receipt = execution.resolve_approval(page, action_id, decision)
        assert receipt["continuationError"] is None, receipt
        wait_for(client, page, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        assert target.exists() is (decision == "allow_once")
        if decision == "allow_once":
            assert target.read_text() == "Only if the owner allows once.\\n"
        assert execution.resolve_approval(page, action_id, decision)["repeated"]
        assert target.exists() is (decision == "allow_once")
