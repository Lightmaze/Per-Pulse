"""Approved concurrent Kernel effects share one physical workspace write lane.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All model responses use an explicit local HTTP transport fixture.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import gc
import hashlib
import json
import os
import threading
import time
from types import SimpleNamespace
import weakref

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.command import CommandWorkspaceToolProvider, DockerCommandExecutor
from pulse_harness.adapters import command as command_module
from pulse_harness.adapters import workspace as workspace_module
from pulse_harness.adapters.resources import WorkspaceResourceBusy, WorkspaceResourceUnavailable, workspace_write_resource
from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider
from pulse_harness.core import ApprovalDecision, HarnessScope, ToolCall
from pulse_harness.core.errors import EffectUnknown, ProviderUnavailable
from pulse_harness.core.provider import CancellationRequested, CancellationToken
from pulse_system.interaction.api.harness_kernel import HarnessKernelService


def kernel(tmp_path, monkeypatch, provider_type=StructuredWorkspaceToolProvider):
    monkeypatch.setenv("PER_PULSE_CONCURRENCY_TEST_KEY", "synthetic-test-credential")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "shared.txt").write_text("original\n")

    def factory(_scope):
        def respond(request):
            messages = json.loads(request.content)["messages"]
            if any(message["role"] == "tool" for message in messages):
                delta, reason = {"content": "Fixture continuation received the tool result."}, "stop"
            else:
                instruction = json.loads(next(message["content"] for message in messages if message["role"] == "user"))
                arguments = instruction.get("arguments") or {
                    "path": "shared.txt", "expected_digest": instruction["expected_digest"],
                    "content": instruction["content"],
                }
                delta = {"tool_calls": [{
                    "index": 0, "id": "call-" + instruction["name"], "type": "function",
                    "function": {"name": instruction.get("tool_name", "mutate_file"),
                                 "arguments": json.dumps(arguments)},
                }]}
                reason = "tool_calls"
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
            ]
            stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "concurrency-fixture",
                           credential_env="PER_PULSE_CONCURRENCY_TEST_KEY"),
            transport=httpx.MockTransport(respond),
        )

    service = HarnessKernelService(
        tmp_path / "kernel.db", workspace, tmp_path / "checkpoints",
        model_adapter_factory=factory,
        tool_provider_factory=lambda store, root, checkpoints: provider_type(store, root, checkpoints),
    )
    return service, workspace


def propose(service, name, expected="", content="", *, tool_name="mutate_file", arguments=None):
    scope = HarnessScope("world", "subject", "session-" + name, 0, "owner", context_id=name)
    service.create(scope)
    result = service.run_turn(scope.for_turn("turn-" + name), prompt=json.dumps({
        "name": name, "expected_digest": hashlib.sha256(expected.encode()).hexdigest(), "content": content,
        "tool_name": tool_name, "arguments": arguments,
    }))
    assert result["outcome"]["status"] == "waiting_approval", result
    action = result["session"]["turns"][-1]["actions"][0]
    assert action["status"] == "approval_pending"
    return scope, action["action_request_id"]


def approve(service, pending):
    scope, action_id = pending
    return service.resolve_approval(scope, action_id, ApprovalDecision.ALLOW_ONCE)


def test_concurrent_approved_sessions_do_not_overwrite_the_same_preimage(tmp_path, monkeypatch):
    first_at_effect = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    second_at_effect = threading.Event()

    class InterleavedProvider(StructuredWorkspaceToolProvider):
        def _mutate(self, scope, call, cancellation, **kwargs):
            if scope.context_id == "b":
                second_entered.set()
            return super()._mutate(scope, call, cancellation, **kwargs)

        def _before_atomic_replace(self, plan):
            if plan.after_data == b"writer-a\n":
                first_at_effect.set()
                assert release_first.wait(5)
            else:
                second_at_effect.set()

    service, workspace = kernel(tmp_path, monkeypatch, InterleavedProvider)
    try:
        first = propose(service, "a", "original\n", "writer-a\n")
        second = propose(service, "b", "original\n", "writer-b\n")
        providers = [service._runtime(item[0]).provider for item in (first, second)]
        assert providers[0] is not providers[1]
        assert providers[0]._write_resource is providers[1]._write_resource
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(approve, service, first)
            try:
                assert first_at_effect.wait(3)
                b = pool.submit(approve, service, second)
                assert second_entered.wait(3)
                assert not second_at_effect.wait(0.15)
                # Reading does not need the writer lane and remains available.
                read = providers[1].execute_for_scope(
                    second[0].for_turn("turn-b"), ToolCall("read", "read_file", {"path": "shared.txt"}),
                    CancellationToken(),
                )
                assert read["content"] == "original\n"
            finally:
                release_first.set()
            left, right = a.result(timeout=5), b.result(timeout=5)
        assert left["action"]["status"] == "succeeded"
        assert right["action"]["status"] == "failed"
        assert right["action"]["error_code"] == "workspace_digest_conflict"
        assert (workspace / "shared.txt").read_text() == "writer-a\n"
        assert not second_at_effect.is_set()
        assert len(left["session"]["turns"][-1]["changes"]) == 1
        assert len(right["session"]["turns"][-1]["changes"]) == 0
    finally:
        release_first.set()
        service.close()


def test_waiting_approved_writer_can_be_cancelled_without_waiting_for_holder(tmp_path, monkeypatch):
    first_at_effect = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    second_at_effect = threading.Event()

    class HeldProvider(StructuredWorkspaceToolProvider):
        def _mutate(self, scope, call, cancellation, **kwargs):
            if scope.context_id == "b":
                second_entered.set()
            return super()._mutate(scope, call, cancellation, **kwargs)

        def _before_atomic_replace(self, plan):
            if plan.after_data == b"writer-a\n":
                first_at_effect.set()
                assert release_first.wait(5)
            else:
                second_at_effect.set()

    service, workspace = kernel(tmp_path, monkeypatch, HeldProvider)
    try:
        first = propose(service, "a", "original\n", "writer-a\n")
        second = propose(service, "b", "original\n", "writer-b\n")
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(approve, service, first)
            try:
                assert first_at_effect.wait(3)
                b = pool.submit(approve, service, second)
                assert second_entered.wait(3)
                service.control(second[0].for_turn("turn-b"), operation="interrupt")
                result = b.result(timeout=2)
                assert result["action"]["status"] in {"cancelled", "uncertain"}
                assert not second_at_effect.is_set()
                assert (workspace / "shared.txt").read_text() == "original\n"
            finally:
                release_first.set()
            assert a.result(timeout=5)["action"]["status"] == "succeeded"
        # A cancelled waiter cannot poison the resource for later work.
        later = propose(service, "c", "writer-a\n", "writer-c\n")
        assert approve(service, later)["action"]["status"] == "succeeded"
    finally:
        release_first.set()
        service.close()


def test_restore_excludes_a_stale_approved_mutation(tmp_path, monkeypatch):
    restore_at_effect = threading.Event()
    release_restore = threading.Event()
    writer_entered = threading.Event()

    class ObservedProvider(StructuredWorkspaceToolProvider):
        def _mutate(self, scope, call, cancellation, **kwargs):
            if scope.context_id == "b":
                writer_entered.set()
            return super()._mutate(scope, call, cancellation, **kwargs)

    service, workspace = kernel(tmp_path, monkeypatch, ObservedProvider)
    try:
        first = propose(service, "a", "original\n", "writer-a\n")
        succeeded = approve(service, first)
        assert succeeded["action"]["status"] == "succeeded"
        checkpoint = succeeded["action"]["result"]["output"]["checkpoint_id"]
        second = propose(service, "b", "writer-a\n", "writer-b\n")
        original_write = workspace_module._atomic_write_bytes

        def held_restore(path, data):
            if path == workspace / "shared.txt":
                restore_at_effect.set()
                assert release_restore.wait(5)
            return original_write(path, data)

        monkeypatch.setattr(workspace_module, "_atomic_write_bytes", held_restore)
        provider = service._runtime(first[0]).provider
        with ThreadPoolExecutor(max_workers=2) as pool:
            restore = pool.submit(provider.restore, first[0].for_turn("turn-a"), checkpoint)
            try:
                assert restore_at_effect.wait(3)
                mutation = pool.submit(approve, service, second)
                assert writer_entered.wait(3)
                assert not mutation.done()
            finally:
                release_restore.set()
            assert restore.result(timeout=5)["status"] == "restored"
            result = mutation.result(timeout=5)
        assert result["action"]["status"] == "failed"
        assert result["action"]["error_code"] == "workspace_digest_conflict"
        assert (workspace / "shared.txt").read_text() == "original\n"
    finally:
        release_restore.set()
        service.close()


def test_approved_command_excludes_file_mutation_until_executor_receipt(tmp_path, monkeypatch):
    command_started = threading.Event()
    release_command = threading.Event()
    writer_entered = threading.Event()
    writer_at_effect = threading.Event()

    class FixtureCommandExecutor:
        """Explicit local command fixture; this test does not launch Docker."""

        image_id = "sha256:" + "0" * 64

        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, arguments, cancellation, *, guard, start_guard, output_limit):
            with start_guard():
                guard()
                cancellation.raise_if_cancelled()
            command_started.set()
            assert release_command.wait(5)
            cancellation.raise_if_cancelled()
            (self.workspace / "shared.txt").write_text("command-result\n")
            return {"execution_status": "exited", "exit_code": 0, "stdout": "fixture command completed"}

    class MixedProvider(CommandWorkspaceToolProvider):
        def __init__(self, store, root, checkpoints):
            super().__init__(store, root, checkpoints, command_executor=FixtureCommandExecutor(root))

        def _mutate(self, scope, call, cancellation, **kwargs):
            writer_entered.set()
            return super()._mutate(scope, call, cancellation, **kwargs)

        def _before_atomic_replace(self, plan):
            writer_at_effect.set()

    service, workspace = kernel(tmp_path, monkeypatch, MixedProvider)
    try:
        command = propose(service, "command", tool_name="run_command", arguments={"argv": ["fixture"]})
        mutation = propose(service, "mutation", "original\n", "file-result\n")
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(approve, service, command)
            try:
                assert command_started.wait(3)
                b = pool.submit(approve, service, mutation)
                assert writer_entered.wait(3)
                assert not writer_at_effect.wait(0.15)
                provider = service._runtime(mutation[0]).provider
                read = provider.execute_for_scope(
                    mutation[0].for_turn("turn-mutation"),
                    ToolCall("read-during-command", "read_file", {"path": "shared.txt"}), CancellationToken(),
                )
                assert read["content"] == "original\n"
            finally:
                release_command.set()
            assert a.result(timeout=5)["action"]["status"] == "succeeded"
            result = b.result(timeout=5)
        assert result["action"]["status"] == "failed"
        assert result["action"]["error_code"] == "workspace_digest_conflict"
        assert not writer_at_effect.is_set()
        assert (workspace / "shared.txt").read_text() == "command-result\n"
    finally:
        release_command.set()
        service.close()


def test_queued_approved_command_cancels_before_executor_start(tmp_path, monkeypatch):
    mutation_at_effect = threading.Event()
    release_mutation = threading.Event()
    command_entered = threading.Event()
    commands_started = []

    class FixtureCommandExecutor:
        image_id = "sha256:" + "1" * 64

        def execute(self, arguments, cancellation, *, guard, start_guard, output_limit):
            with start_guard():
                guard()
                cancellation.raise_if_cancelled()
            commands_started.append(arguments["argv"])
            return {"execution_status": "exited", "exit_code": 0, "stdout": "fixture command completed"}

    class MixedProvider(CommandWorkspaceToolProvider):
        def __init__(self, store, root, checkpoints):
            super().__init__(store, root, checkpoints, command_executor=FixtureCommandExecutor())

        def execute_for_scope(self, scope, call, cancellation, **kwargs):
            if call.name == "run_command":
                command_entered.set()
            return super().execute_for_scope(scope, call, cancellation, **kwargs)

        def _before_atomic_replace(self, plan):
            mutation_at_effect.set()
            assert release_mutation.wait(5)

    service, workspace = kernel(tmp_path, monkeypatch, MixedProvider)
    try:
        mutation = propose(service, "mutation", "original\n", "file-result\n")
        command = propose(service, "command", tool_name="run_command", arguments={"argv": ["cancelled-fixture"]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(approve, service, mutation)
            try:
                assert mutation_at_effect.wait(3)
                b = pool.submit(approve, service, command)
                assert command_entered.wait(3)
                service.control(command[0].for_turn("turn-command"), operation="interrupt")
                result = b.result(timeout=2)
                assert result["action"]["status"] in {"cancelled", "uncertain"}
                assert commands_started == []
            finally:
                release_mutation.set()
            assert a.result(timeout=5)["action"]["status"] == "succeeded"
        later = propose(service, "later-command", tool_name="run_command", arguments={"argv": ["later-fixture"]})
        assert approve(service, later)["action"]["status"] == "succeeded"
        assert commands_started == [["later-fixture"]]
        assert (workspace / "shared.txt").read_text() == "file-result\n"
    finally:
        release_mutation.set()
        service.close()


def test_running_command_cancellation_releases_lane_after_cleanup(tmp_path, monkeypatch):
    command_started = threading.Event()
    command_cleaned = threading.Event()
    writer_entered = threading.Event()

    class FixtureCommandExecutor:
        image_id = "sha256:" + "2" * 64

        def __init__(self, workspace):
            self.workspace = workspace

        def execute(self, arguments, cancellation, *, guard, start_guard, output_limit):
            with start_guard():
                guard()
                cancellation.raise_if_cancelled()
            (self.workspace / "shared.txt").write_text("partial-command-result\n")
            command_started.set()
            deadline = time.monotonic() + 5
            try:
                while not cancellation.cancelled and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert cancellation.cancelled, "fixture command was not cancelled before its deadline"
            finally:
                command_cleaned.set()
            raise EffectUnknown("fixture command stopped after a partial effect", code="command_effect_unknown")

    class MixedProvider(CommandWorkspaceToolProvider):
        def __init__(self, store, root, checkpoints):
            super().__init__(store, root, checkpoints, command_executor=FixtureCommandExecutor(root))

        def _mutate(self, scope, call, cancellation, **kwargs):
            writer_entered.set()
            return super()._mutate(scope, call, cancellation, **kwargs)

        def _before_atomic_replace(self, plan):
            assert command_cleaned.is_set(), "file effect overlapped command cleanup"

    service, workspace = kernel(tmp_path, monkeypatch, MixedProvider)
    try:
        command = propose(service, "command", tool_name="run_command", arguments={"argv": ["fixture"]})
        mutation = propose(service, "mutation", "partial-command-result\n", "recovered-result\n")
        # Hold the control path at its durable model/turn reconciliation
        # boundary. Before that is committed, the physical command must not
        # receive its cancellation token and return a stale RUNNING receipt.
        original_reconcile = service.store.reconcile_model_rounds
        cancel_commit_entered = threading.Event()
        release_cancel_commit = threading.Event()

        def observe_cancel_commit(*args, **kwargs):
            if kwargs.get("cancel_turn"):
                cancel_commit_entered.set()
                assert release_cancel_commit.wait(3), "fixture abort was not released"
            return original_reconcile(*args, **kwargs)

        monkeypatch.setattr(service.store, "reconcile_model_rounds", observe_cancel_commit)
        command_scope = command[0].for_turn("turn-command")
        runner = service._runtime(command[0]).runner
        with ThreadPoolExecutor(max_workers=3) as pool:
            a = pool.submit(approve, service, command)
            assert command_started.wait(3)
            b = pool.submit(approve, service, mutation)
            assert writer_entered.wait(3)
            cancelling = pool.submit(service.control, command_scope, operation="interrupt")
            try:
                assert cancel_commit_entered.wait(3)
                with runner._lock:
                    active = runner._active.get((command_scope.session_id, command_scope.turn_id))
                    assert active is not None and active.active_token is not None
                    assert not active.active_token.cancelled, (
                        "Physical cancellation preceded the durable action fence"
                    )
            finally:
                release_cancel_commit.set()
            cancelling.result(timeout=3)
            command_result = a.result(timeout=3)
            assert command_result["action"]["status"] == "uncertain"
            assert command_result["action"]["error_code"] in {"command_effect_unknown", "abort_effect_unknown"}
            assert b.result(timeout=3)["action"]["status"] == "succeeded"
        assert command_cleaned.is_set()
        assert (workspace / "shared.txt").read_text() == "recovered-result\n"
    finally:
        service.close()


def test_resource_wait_has_deadline_and_cancellation_and_releases_after_errors(tmp_path):
    resource = workspace_write_resource(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def hold():
        with resource.write():
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold)
        try:
            assert entered.wait(2)
            start = time.monotonic()
            with pytest.raises(WorkspaceResourceBusy):
                with resource.write(timeout=0.05):
                    pytest.fail("contended writer entered")
            assert time.monotonic() - start < 1
            cancellation = CancellationToken()
            cancellation.cancel("user stopped waiting")
            with pytest.raises(CancellationRequested):
                with resource.write(cancellation):
                    pytest.fail("cancelled writer entered")
        finally:
            release.set()
        holder.result(timeout=2)
    with pytest.raises(RuntimeError):
        with resource.write():
            raise RuntimeError("synthetic effect failure")
    with resource.write(timeout=0.05):
        pass


def test_unconfirmed_command_cleanup_blocks_new_approved_effects_until_confirmed(tmp_path, monkeypatch):
    class UnconfirmedCommandFixture:
        image_id = "sha256:" + "3" * 64

        def __init__(self, root):
            self.resource = workspace_write_resource(root)

        def execute(self, arguments, cancellation, **kwargs):
            self.resource.quarantine("fixture-unconfirmed-command")
            raise EffectUnknown("fixture command cleanup unconfirmed", code="command_effect_unknown")

        def confirm_stopped(self):
            self.resource.confirm_cleanup("fixture-unconfirmed-command")

    class MixedProvider(CommandWorkspaceToolProvider):
        def __init__(self, store, root, checkpoints):
            super().__init__(store, root, checkpoints, command_executor=UnconfirmedCommandFixture(root))

    service, workspace = kernel(tmp_path, monkeypatch, MixedProvider)
    executor = None
    try:
        command = propose(service, "command", tool_name="run_command", arguments={"argv": ["fixture"]})
        executor = service._runtime(command[0]).provider.command_executor
        # EffectUnknown intentionally leaves the Kernel action running for its
        # existing recovery path; the resource fence prevents further effects.
        assert approve(service, command)["action"]["status"] == "running"
        mutation = propose(service, "blocked", "original\n", "blocked-result\n")
        result = approve(service, mutation)
        assert result["action"]["status"] == "failed"
        assert result["action"]["error_code"] == "workspace_resource_unavailable"
        assert (workspace / "shared.txt").read_text() == "original\n"
        provider = service._runtime(mutation[0]).provider
        assert provider.preflight()["workspace_write_state"] == "quarantined"
        assert provider.read_file(mutation[0].for_turn("turn-blocked"), "shared.txt")["content"] == "original\n"
        executor.confirm_stopped()
        later = propose(service, "after-cleanup", "original\n", "recovered-result\n")
        assert approve(service, later)["action"]["status"] == "succeeded"
        # Cleanup does not replay or fabricate settlement of the original action.
        assert service.store.get_action(command[0].for_turn("turn-command"), command[1]).status.value == "running"
    finally:
        if executor:
            executor.confirm_stopped()
        service.close()


def test_quarantine_survives_provider_collection_without_blocking_other_workspaces(tmp_path):
    root, checkpoints = tmp_path / "workspace", tmp_path / "checkpoints"
    store = SimpleNamespace(max_payload_bytes=16384)
    provider = StructuredWorkspaceToolProvider(store, root, checkpoints)
    resource_ref = weakref.ref(provider._write_resource)
    with provider._write_resource.write():
        provider._write_resource.quarantine("unconfirmed-container")
    del provider
    gc.collect()
    replacement = StructuredWorkspaceToolProvider(store, root, checkpoints)
    try:
        assert replacement._write_resource is resource_ref()
        with pytest.raises(WorkspaceResourceUnavailable, match="unconfirmed command cleanup"):
            with replacement._write_resource.write():
                pytest.fail("replacement provider bypassed unresolved cleanup")
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        with workspace_write_resource(unrelated).write(timeout=0.05):
            (unrelated / "result.txt").write_text("independent work")
        assert (unrelated / "result.txt").read_text() == "independent work"
    finally:
        replacement._write_resource.confirm_cleanup("unconfirmed-container")
    with replacement._write_resource.write(timeout=0.05):
        pass
    del replacement
    gc.collect()
    assert resource_ref() is None, "confirmed cleanup retained an unused resource"


def test_docker_unconfirmed_cleanup_quarantines_until_close_verifies_removal(tmp_path, monkeypatch):
    """Exercise the real executor cleanup branch with synthetic process/control I/O."""
    executor = object.__new__(DockerCommandExecutor)
    executor.workspace = tmp_path
    executor._write_resource = workspace_write_resource(tmp_path)
    executor._lock = threading.Lock()
    executor._closed = False
    executor._active, executor._unresolved = set(), set()
    executor._env = {}
    executor._config_dir = SimpleNamespace(cleanup=lambda: None)
    executor.invocation = lambda *args: ["explicit-process-fixture"]
    removal_confirmed = False

    def control(*args):
        if not removal_confirmed:
            raise ProviderUnavailable("fixture daemon unavailable")
        return ""

    class ProcessFixture:
        def __init__(self):
            for label in ("stdout", "stderr"):
                read_fd, write_fd = os.pipe()
                os.close(write_fd)
                setattr(self, label, os.fdopen(read_fd, "rb"))

        def poll(self):
            return 125

        def wait(self, **kwargs):
            return 125

    executor._control = control
    monkeypatch.setattr(command_module.subprocess, "Popen", lambda *args, **kwargs: ProcessFixture())
    try:
        with executor._write_resource.write():
            with pytest.raises(EffectUnknown, match="cleanup unconfirmed"):
                executor.execute({"argv": ["fixture"]}, CancellationToken(), guard=lambda: None, start_guard=nullcontext)
        assert len(executor._write_resource.unresolved_commands) == 1
        with pytest.raises(ProviderUnavailable):
            executor.close()
        with pytest.raises(WorkspaceResourceUnavailable):
            with executor._write_resource.write():
                pytest.fail("unresolved Docker writer was admitted")
    finally:
        removal_confirmed = True
        executor.close()
    assert not executor._write_resource.unresolved_commands
    with executor._write_resource.write(timeout=0.05):
        pass
