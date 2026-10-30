"""Native command recovery with an explicitly synthetic Docker control port.

The Kernel, approvals, SQLite identities and workspace effects are real. These
tests do not claim a running daemon; the separate Docker integration does.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pulse_harness.adapters import command as commands
from pulse_harness.adapters.command import CommandWorkspaceToolProvider, recover_world_commands
from pulse_harness.adapters.resources import WorkspaceResourceUnavailable, workspace_write_resource
from pulse_harness.core import ActionStatus
from pulse_harness.core.errors import EffectUnknown, InvalidTransition, ProviderUnavailable
from test_workspace_concurrency import approve, kernel, propose


class LostCommandFixture:
    image_id = "sha256:" + "3" * 64

    def execute(self, arguments, cancellation, **kwargs):
        kwargs["on_created"]("a" * 64)
        raise EffectUnknown("explicit lost command fixture", code="command_effect_unknown")


class LostCommandProvider(CommandWorkspaceToolProvider):
    def __init__(self, *args):
        super().__init__(*args, command_executor=LostCommandFixture())


@pytest.fixture
def uncertain_command(tmp_path, monkeypatch, request):
    variant = getattr(request, "param", None)

    class VersionedFixtureProvider(LostCommandProvider):
        def action_authority_identity(self, scope, call, *, action_request_id):
            identity = super().action_authority_identity(scope, call, action_request_id=action_request_id)
            if call.name == "run_command":
                if variant == "legacy":
                    identity["adapter"] = "pulse_harness.command.v1"
                    for key in ("command_protocol", "container_name", "command_workspace"):
                        identity.pop(key)
                elif variant == "relocated":
                    identity["command_workspace"]["path"] = str(tmp_path / "former-workspace")
                elif variant == "wrong_name":
                    identity["container_name"] = "p-pulse-command-" + "9" * 32
            return identity

    service, workspace = kernel(tmp_path, monkeypatch, VersionedFixtureProvider)
    pending = propose(service, "orphan", tool_name="run_command", arguments={"argv": ["fixture"]})
    assert approve(service, pending)["action"]["status"] == "running"
    service.store.recover_inflight(reason="explicit-test-recovery")
    scope = pending[0].for_turn("turn-orphan")
    action = service.store.get_action(scope, pending[1])
    assert action.status is ActionStatus.UNCERTAIN
    authority = service.store.get_action_authority(scope, pending[1])
    resource = workspace_write_resource(workspace)
    try:
        yield service, workspace, action, authority
    finally:
        # Synthetic external resource: remove only this fixture's process-local
        # quarantine, never report a production command exit through this path.
        for identity in resource.unresolved_commands:
            resource.confirm_cleanup(identity)
        service.close()


class ControlFixture:
    def __init__(self, workspace, identity, *, present=True, failure=None, mismatch=None):
        self._config_dir = SimpleNamespace(cleanup=lambda: None)
        self.present, self.failure, self.calls = present, failure, []
        self.name = identity["container_name"]
        self.info = {"id": "a" * 64, "name": "/" + self.name, "image": identity["image_id"],
            "labels": {"io.ppulse.command": "1", "io.ppulse.command.protocol": "create-start-v2",
                       "io.ppulse.command.invocation": self.name},
            "mounts": [{"Type": "bind", "Source": str(workspace), "Destination": "/workspace", "RW": True}]}
        if mismatch == "image":
            self.info["image"] = "sha256:" + "7" * 64
        elif mismatch == "workspace":
            self.info["mounts"][0]["Source"] = str(workspace.parent / "other")
        elif mismatch == "protocol":
            self.info["labels"]["io.ppulse.command.protocol"] = "legacy"
        elif mismatch == "extra_mount":
            self.info["mounts"].append({"Source": "/other", "Destination": "/extra"})
        elif mismatch == "invalid_labels":
            self.info["labels"] = ["malformed inspection fixture"]
        elif mismatch == "invalid_mount":
            self.info["mounts"] = [None]

    def _control(self, *args):
        self.calls.append(args)
        if self.failure == "unavailable":
            raise ProviderUnavailable("explicit unavailable daemon fixture")
        if args[0] == "ps":
            return self.info["id"] if self.present else ""
        if args[0] == "inspect":
            return json.dumps(self.info)
        raise AssertionError(args)

    def _remove(self, name, *, container_id=None):
        self.calls.append(("remove", name, container_id))
        assert name == self.name and container_id == self.info["id"]
        if self.failure == "removal":
            raise ProviderUnavailable("explicit unconfirmed removal fixture")
        self.present = False


@pytest.mark.parametrize("present", [True, False])
def test_confirmed_exit_retains_uncertain_action_and_survives_daemon_removal(uncertain_command, monkeypatch, present):
    service, workspace, action, authority = uncertain_command
    control = ControlFixture(workspace, authority["identity"], present=present)
    monkeypatch.setattr(commands, "_DockerControl", lambda: control)
    report = recover_world_commands(service.store, "world", workspace, guard=lambda: None)
    assert report == {"confirmed": 1, "unresolved": 0}
    assert service.store.get_action(action.scope, action.action_request_id) == action
    recovery = service.store.recovery_records_for_session(action.scope)[0]
    assert recovery.confirmed_exit and recovery.state == "uncertain"
    with workspace_write_resource(workspace).write(timeout=.05):
        pass
    later = propose(service, "later", "original\n", "after actual cleanup\n")
    assert approve(service, later)["action"]["status"] == "succeeded"
    assert (workspace / "shared.txt").read_text() == "after actual cleanup\n"
    before = tuple(control.calls)
    monkeypatch.setattr(commands, "_DockerControl", lambda: pytest.fail("confirmed exit must survive removal of Docker"))
    assert recover_world_commands(service.store, "world", workspace, guard=lambda: None) == {"confirmed": 0, "unresolved": 0}
    assert tuple(control.calls) == before
    assert service.store.action_terminal_count(action.scope, action.action_request_id) == 1


@pytest.mark.parametrize("failure,mismatch", [
    ("unavailable", None), ("removal", None), (None, "image"), (None, "workspace"),
    (None, "protocol"), (None, "extra_mount"), (None, "invalid_labels"), (None, "invalid_mount"),
])
def test_unconfirmed_or_foreign_container_blocks_writes_and_preserves_reads(uncertain_command, monkeypatch, failure, mismatch):
    service, workspace, action, authority = uncertain_command
    control = ControlFixture(workspace, authority["identity"], failure=failure, mismatch=mismatch)
    monkeypatch.setattr(commands, "_DockerControl", lambda: control)
    assert recover_world_commands(service.store, "world", workspace, guard=lambda: None) == {"confirmed": 0, "unresolved": 1}
    with pytest.raises(WorkspaceResourceUnavailable):
        with workspace_write_resource(workspace).write(timeout=.05):
            pytest.fail("unconfirmed external writer admitted")
    assert (workspace / "shared.txt").read_text() == "original\n"
    assert not service.store.recovery_records_for_session(action.scope)[0].confirmed_exit
    if mismatch:
        assert not any(call[0] == "remove" for call in control.calls), "a foreign container was targeted"
    other = workspace.parent / "unrelated"
    other.mkdir()
    with workspace_write_resource(other).write(timeout=.05):
        pass


def test_failed_cleanup_can_be_retried_without_replaying_action(uncertain_command, monkeypatch):
    service, workspace, action, authority = uncertain_command
    control = ControlFixture(workspace, authority["identity"], failure="unavailable")
    monkeypatch.setattr(commands, "_DockerControl", lambda: control)
    assert recover_world_commands(service.store, "world", workspace, guard=lambda: None)["unresolved"] == 1
    control.failure = None
    assert recover_world_commands(service.store, "world", workspace, guard=lambda: None)["confirmed"] == 1
    assert service.store.get_action(action.scope, action.action_request_id) == action
    assert service.store.action_terminal_count(action.scope, action.action_request_id) == 1


def test_clean_command_cannot_be_relabelled_by_exit_confirmation(tmp_path, monkeypatch):
    service, _ = kernel(tmp_path, monkeypatch)
    try:
        pending = propose(service, "ordinary", "original\n", "ordinary write\n")
        assert approve(service, pending)["action"]["status"] == "succeeded"
        with pytest.raises(InvalidTransition):
            service.store.confirm_recovery_exit(pending[0].for_turn("turn-ordinary"), pending[1])
    finally:
        service.close()


@pytest.mark.parametrize("uncertain_command", ["legacy", "relocated", "wrong_name"], indirect=True)
def test_unverifiable_old_authority_never_contacts_or_removes_container(uncertain_command, monkeypatch):
    service, workspace, action, _ = uncertain_command
    monkeypatch.setattr(commands, "_DockerControl", lambda: pytest.fail("invalid authority contacted Docker"))
    assert recover_world_commands(service.store, "world", workspace, guard=lambda: None) == {"confirmed": 0, "unresolved": 1}
    with pytest.raises(WorkspaceResourceUnavailable):
        with workspace_write_resource(workspace).write(timeout=.05):
            pytest.fail("unverified old authority admitted a writer")
    assert not service.store.recovery_records_for_session(action.scope)[0].confirmed_exit
