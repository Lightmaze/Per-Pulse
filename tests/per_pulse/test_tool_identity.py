"""Editing a tool's description does not invalidate an action awaiting approval.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Model decisions come from the scripted HTTP fixture of test_execution; the
Kernel, World and workspace are real.
"""
from __future__ import annotations

from dataclasses import replace

from per_pulse.execution import PageExecution
from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider
from pulse_harness.core.replay import digest_json
from pulse_harness.core.contracts import ToolCall, ToolDescriptor
from pulse_harness.core.scope import HarnessScope
from pulse_harness.core.turn_runner import TurnRunner
from test_execution import _client, _factory, _new_page, _wait


def test_an_approved_edit_still_runs_after_its_tool_description_changes(tmp_path, monkeypatch):
    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests), allow_workspace_actions=True) as client:
        execution = PageExecution(client.app.state.pages.runtime)
        page_id = _new_page(client)
        waiting = _wait(execution, page_id, lambda p: bool(p["pendingActions"]) and p["pendingActions"][0]["canResolve"])
        action = waiting["pendingActions"][0]

        # An update rewords mutate_file while the action waits for approval.
        original = StructuredWorkspaceToolProvider.descriptors

        def reworded(self):
            return tuple(replace(d, description=d.description + " Reworded in a later version.")
                         if d.name == "mutate_file" else d for d in original(self))

        monkeypatch.setattr(StructuredWorkspaceToolProvider, "descriptors", reworded)
        receipt = execution.resolve_approval(page_id, action["actionId"], "allow_once")
        assert receipt["accepted"], receipt
        _wait(execution, page_id, lambda p: p["status"] == "idle")
        assert (tmp_path / "workspace" / "one.txt").read_text() == "Written by one.txt.\n"


def test_an_action_recorded_with_the_older_digest_still_matches_while_its_text_is_unchanged():
    class Store:
        def __init__(self, stored):
            self.stored = stored

        def get_action_authority(self, scope, action_request_id):
            return self.stored

    tool = ToolDescriptor("mutate_file", "Apply one guarded file change.", mutable=True,
                          requires_approval=True, capability="workspace.write", source="test")
    identity = {"name": tool.name, "mutable": True, "requires_approval": True,
                "capability": "workspace.write", "source": "test"}
    legacy = digest_json({**identity, "description": tool.description})
    scope = HarnessScope("world", "engram", "session", 0, "owner", turn_id="turn")
    call = ToolCall("call-1", "mutate_file", {})

    runner = TurnRunner.__new__(TurnRunner)
    runner.tool_provider = object()
    runner.store = Store({"descriptor_digest": legacy})
    assert runner._action_authority(scope, call, tool)["descriptor_digest"] == legacy
    runner.store = Store(None)
    assert runner._action_authority(scope, call, tool)["descriptor_digest"] == digest_json(identity)
