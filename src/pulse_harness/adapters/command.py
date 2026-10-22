"""Approved command execution in disposable, network-disabled Docker containers.

The kernel remains the owner of admission, approval, transcript and settlement.
Only the workspace is mounted; no host environment, credentials, Docker socket,
or private kernel state is given to the command. The operator supplies a locally
installed Linux image with python3. It is resolved to an immutable image ID once.
This is not an interactive PTY, rollback system, or a claim of VM-grade isolation.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from .workspace import StructuredWorkspaceToolProvider
from ..core.contracts import ActionStatus, ToolDescriptor
from ..core.errors import EffectUnknown, IdempotencyConflict, ProviderProtocolError, ProviderUnavailable, ScopeConflict
from ..core.replay import digest_json, redact_text

COMMAND_SCHEMA = {
    "type": "object", "properties": {
        "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 64,
                 "description": "Argument vector inside the selected container, e.g. python3 and a workspace script. No host shell."},
        "cwd": {"type": "string", "description": "Existing directory relative to /workspace; defaults to ."},
        "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120},
    }, "required": ["argv"], "additionalProperties": False,
}
# The watchdog runs inside the container so a lost client does not remove the
# deadline. Child descendants die with the container's init. Exit 124 denotes a
# deadline (or an application returning 124); we do not pretend to distinguish it.
_SUPERVISOR = """import json,os,subprocess,sys
os.environ.clear()
os.environ.update(PATH='/usr/local/bin:/usr/bin:/bin',HOME='/tmp',LANG='C.UTF-8',TMPDIR='/tmp')
try:
 p=subprocess.Popen(json.loads(sys.argv[2]),stdin=subprocess.DEVNULL,start_new_session=True)
 try: code=p.wait(timeout=float(sys.argv[1]))
 except subprocess.TimeoutExpired:
  p.kill();p.wait();code=124
except OSError:
 print('Command executable unavailable',file=sys.stderr);code=127
sys.exit(code if code>=0 else 128-code)
"""
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def command_args(arguments: Mapping[str, Any]) -> tuple[list[str], str, float]:
    if set(arguments) - {"argv", "cwd", "timeout_seconds"}:
        raise ProviderProtocolError("unsupported command fields")
    argv = arguments.get("argv")
    if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 64
            or any(not isinstance(a, str) or "\x00" in a for a in argv)
            or not argv[0].strip() or sum(len(a.encode('utf-8')) for a in argv) > 16384):
        raise ProviderProtocolError("command requires a bounded argument vector")
    cwd = arguments.get("cwd", ".")
    if (not isinstance(cwd, str) or not cwd or len(cwd) > 1024 or '\\' in cwd
            or any(ord(c) < 32 for c in cwd) or PurePosixPath(cwd).is_absolute()
            or '..' in PurePosixPath(cwd).parts):
        raise ProviderProtocolError("command cwd must be relative to the workspace")
    timeout = arguments.get("timeout_seconds", 30)
    if (isinstance(timeout, bool) or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout) or not 1 <= timeout <= 120):
        raise ProviderProtocolError("command timeout must be from 1 to 120 seconds")
    return list(argv), str(PurePosixPath(cwd)), float(timeout)


class DockerCommandExecutor:
    """Local-daemon command adapter; construction checks configuration, not a model."""
    def __init__(self, workspace: Path, image: str):
        if sys.platform != "linux":
            raise ProviderUnavailable("container commands currently require a Linux host", code="command_platform_unsupported")
        if (not isinstance(image, str) or not image or len(image) > 256
                or image.startswith('-') or any(c.isspace() for c in image)):
            raise ProviderProtocolError("invalid operator-selected command image")
        self.workspace = workspace.resolve(strict=True)
        if ',' in str(self.workspace):
            raise ProviderProtocolError("Docker mount path must not contain a comma")
        self.docker = shutil.which("docker")
        if not self.docker:
            raise ProviderUnavailable("Docker CLI is not installed; no host command fallback", code="command_docker_unavailable")
        self._config_dir = tempfile.TemporaryDirectory(prefix="p-pulse-docker-")
        # Explicit local socket and an empty CLI config: ignore DOCKER_HOST,
        # contexts, proxy values, credentials, and other inherited environment.
        self._env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": self._config_dir.name}
        self._prefix = [self.docker, "--config", self._config_dir.name, "--host", "unix:///var/run/docker.sock"]
        try:
            image_id = self._control("image", "inspect", "--format", "{{.Id}}", image).strip()
            if not _IMAGE_ID.fullmatch(image_id):
                raise ProviderUnavailable("command image could not be resolved to an immutable ID")
            self.image_id = image_id
        except BaseException:
            self._config_dir.cleanup()
            raise
        self.uid = os.getuid() or 65534
        self.gid = os.getgid() or 65534
        self._lock = threading.Lock()
        self._closed = False
        self._active: set[str] = set()
        self._unresolved: set[str] = set()

    def _control(self, *argv: str) -> str:
        try:
            result = subprocess.run([*self._prefix, *argv], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._env, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            raise ProviderUnavailable("local Docker control failed", code="command_docker_unavailable") from None
        if result.returncode != 0:
            raise ProviderUnavailable("local Docker/image unavailable; preinstall the selected image", code="command_docker_unavailable")
        return result.stdout.decode("utf-8", errors="replace")[:4096]

    def invocation(self, name: str, argv: list[str], cwd: str, timeout: float) -> list[str]:
        target = self.workspace if cwd == '.' else self.workspace.joinpath(*PurePosixPath(cwd).parts)
        # Use the existing file boundary for the startup directory; a symlink
        # inside the command still resolves in the container, not the host.
        current = self.workspace
        for part in PurePosixPath(cwd).parts:
            current = current / part
            if current.is_symlink():
                raise ProviderProtocolError("command cwd cannot traverse a symlink")
        if not target.is_dir() or not target.resolve().is_relative_to(self.workspace):
            raise ProviderProtocolError("command cwd is not an existing workspace directory")
        return [*self._prefix, "run", "--rm", "--pull", "never", "--name", name,
            "--label", "io.ppulse.command=1", "--init", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--log-driver", "none",
            "--user", f"{self.uid}:{self.gid}", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--mount", f"type=bind,src={self.workspace},dst=/workspace",
            "--workdir", "/workspace" + ("/" + cwd if cwd != "." else ""),
            "--entrypoint", "python3", self.image_id, "-I", "-u", "-c", _SUPERVISOR,
            str(timeout), json.dumps(argv, ensure_ascii=False)]

    def execute(self, arguments, cancellation, *, guard: Callable[[], Any], start_guard, output_limit=4096):
        argv, cwd, timeout = command_args(arguments)
        name = "p-pulse-command-" + uuid4().hex
        invocation = self.invocation(name, argv, cwd, timeout)
        cancellation.raise_if_cancelled()
        guard()
        with self._lock:
            if self._closed:
                raise ProviderUnavailable("command adapter is closed")
            self._active.add(name)
        process = None
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        counts = {"stdout": 0, "stderr": 0}
        forced = False
        cleaned = True
        try:
            with start_guard():
                cancellation.raise_if_cancelled()
                guard()
                process = subprocess.Popen(invocation, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=self._env, start_new_session=True)
            deadline = time.monotonic() + timeout + 10  # daemon startup/attach allowance
            with selectors.DefaultSelector() as sel:
                for label in streams:
                    pipe = getattr(process, label)
                    os.set_blocking(pipe.fileno(), False)
                    sel.register(pipe, selectors.EVENT_READ, label)
                while sel.get_map() or process.poll() is None:
                    guard()
                    if cancellation.cancelled or time.monotonic() >= deadline:
                        forced = True
                        break
                    for key, _ in sel.select(.05):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            sel.unregister(key.fileobj)
                            continue
                        label = key.data
                        counts[label] += len(chunk)
                        room = max(0, output_limit - len(streams[label]))
                        streams[label].extend(chunk[:room])
            if not forced:
                code = process.wait(timeout=2)
                if code == 125:
                    # Docker did not provide an application exit. Do not infer
                    # no effect after losing contact with the daemon.
                    raise EffectUnknown("command daemon result unavailable", code="command_effect_unknown")
                output = {label: redact_text(bytes(data).decode('utf-8', errors='replace'))[0]
                          for label, data in streams.items()}
                return {"execution_status": "exited", "exit_code": code, **output,
                        "output_bytes": counts, "truncated": any(counts[k] > len(v) for k, v in streams.items()),
                        "image_id": self.image_id, "timeout_seconds": timeout,
                        "workspace_changes": "possible; not checkpointed or automatically rolled back",
                        "exit_124": "deadline exceeded or program exit 124" if code == 124 else None}
        except BaseException:
            if process is not None:
                forced = True
            raise
        finally:
            if process is not None:
                if forced:
                    # Remove only this unguessable invocation name, never prune
                    # containers from another work/host. Repeat after the CLI
                    # exits to close its possible create-versus-cancel race.
                    try:
                        self._control("rm", "--force", name)
                    except ProviderUnavailable:
                        pass
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        cleaned = False
                    try:
                        self._control("rm", "--force", name)
                    except ProviderUnavailable:
                        # A normal --rm exit is also absent. Absence is checked
                        # via a successful list, not inferred from any error.
                        try:
                            if self._control("ps", "-aq", "--filter", "name=^/" + name + "$").strip():
                                cleaned = False
                        except ProviderUnavailable:
                            cleaned = False
                for label in streams:
                    getattr(process, label).close()
            with self._lock:
                self._active.discard(name)
                if not cleaned:
                    self._unresolved.add(name)
            if forced:
                # Stopping the container never proves its prior writes did not
                # occur. The kernel recovery path owns the uncertain action.
                raise EffectUnknown("command stopped; prior effects may exist" if cleaned else
                                    "command cleanup unconfirmed", code="command_effect_unknown")
        raise EffectUnknown("command deadline reached; inspect existing files", code="command_effect_unknown")

    def close(self):
        with self._lock:
            if self._active:
                raise RuntimeError("cannot close command executor with active calls")
            pending = tuple(self._unresolved)
        for name in pending:
            try:
                self._control("rm", "--force", name)
            except ProviderUnavailable:
                if self._control("ps", "-aq", "--filter", "name=^/" + name + "$").strip():
                    raise ProviderUnavailable("command cleanup is unresolved", code="command_cleanup_unresolved")
            with self._lock:
                self._unresolved.discard(name)
        with self._lock:
            self._closed = True
        self._config_dir.cleanup()


class CommandWorkspaceToolProvider(StructuredWorkspaceToolProvider):
    """Add a command capability without changing existing file-tool authority."""
    def __init__(self, *args, command_executor=None, command_guard=lambda: None,
                 command_start_guard=nullcontext, **kwargs):
        super().__init__(*args, **kwargs)
        self.command_executor = command_executor
        self.command_guard, self.command_start_guard = command_guard, command_start_guard
        self._command_bindings: dict[str, str] = {}
        self._command_consumed: set[str] = set()
        self._command_lock = threading.Lock()

    def descriptors(self):
        tools = super().descriptors()
        if self.command_executor is None:
            return tools
        return tools + (ToolDescriptor(name="run_command", mutable=True, requires_approval=True,
            capability="workspace.command", source="pulse_harness.command",
            description="Run an approved, bounded command in a disposable network-disabled container. "
                        "Only /workspace persists. May modify/delete workspace files; no automatic rollback. "
                        "Result includes exit_code; nonzero is not success. Not an interactive terminal."),)

    def preflight(self):
        return {**super().preflight(), "run_command": self.command_executor is not None,
                "command_environment": "docker-network-none" if self.command_executor else None}

    def action_authority_identity(self, scope, call, *, action_request_id):
        if call.name != "run_command":
            return super().action_authority_identity(scope, call, action_request_id=action_request_id)
        if self.command_executor is None:
            raise ProviderUnavailable("command capability is not configured")
        command_args(call.arguments)
        return {"adapter": "pulse_harness.command.v1", "action_request_id": action_request_id,
                "scope_digest": scope.digest, "workspace_provenance": self._workspace_provenance,
                "storage_identity": self._storage_identity(), "image_id": self.command_executor.image_id,
                "policy": "network-none-readonly-root-workspace-rw-v1"}

    def bind_action_authority(self, scope, call, *, action_request_id):
        if call.name != "run_command":
            return super().bind_action_authority(scope, call, action_request_id=action_request_id)
        action = self.store.get_action(scope, action_request_id)
        authority = self.store.get_action_authority(scope, action_request_id)
        if (action.tool_name != call.name or action.args_digest != digest_json(dict(call.arguments))
                or not authority or authority.get("provider_source") != "pulse_harness.command"
                or authority.get("provider_type") != f"{type(self).__module__}.{type(self).__qualname__}"
                or authority.get("identity") != self.action_authority_identity(scope, call, action_request_id=action_request_id)):
            raise ScopeConflict("command does not match the approved execution authority")
        with self._command_lock:
            if action_request_id not in self._command_bindings:
                if action.status not in {ActionStatus.INTENT, ActionStatus.APPROVAL_PENDING}:
                    raise ScopeConflict("orphan command cannot rebind after approval or execution")
                self._command_bindings[action_request_id] = action.args_digest

    def execute_for_scope(self, scope, call, cancellation, *, action_request_id=None):
        if call.name != "run_command":
            return super().execute_for_scope(scope, call, cancellation, action_request_id=action_request_id)
        if not action_request_id:
            raise ScopeConflict("command requires a kernel-owned approved action")
        self.bind_action_authority(scope, call, action_request_id=action_request_id)
        action = self.store.get_action(scope, action_request_id)
        if action.status is not ActionStatus.RUNNING or not action.approval_id:
            raise ScopeConflict("command must be claimed by its original approved turn")
        with self._command_lock:
            if action_request_id in self._command_consumed:
                raise IdempotencyConflict("command execution cannot be replayed")
            self._command_consumed.add(action_request_id)
        limit = max(128, min(4096, (self.store.max_payload_bytes - 2048) // 12))
        return self.command_executor.execute(call.arguments, cancellation, guard=self.command_guard,
            start_guard=self.command_start_guard, output_limit=limit)
