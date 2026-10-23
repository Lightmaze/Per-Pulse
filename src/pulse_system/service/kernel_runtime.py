"""Connect the existing PulseWorld to the Pulse-owned model/tool TurnRunner.

Kernel scope ownership is logical and stable across process restarts. Physical
execution is additionally fenced by the world's current owner/publication
permit on admission, every model delta, and each file operation. The immutable
Kernel transcript is not rewritten to pretend the new process wrote old turns.
The HTTP observer and the world share one KernelService, never two stores.

Modified for Per Pulse: opt-in disconnected workspace startup; default
provider checks are preserved. These modifications are licensed Apache-2.0.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing, contextmanager
import hashlib
import logging
import math
import os
import sqlite3
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from pulse_harness.adapters.chat_http import ChatHTTPModelAdapter, model_factory_from_env
from pulse_harness.life.service import LifeHub
from pulse_harness.life import tools as life_tools
from pulse_harness.adapters.history import ConversationHistoryTools, HISTORY_SCHEMAS
from pulse_harness.adapters.command import CommandWorkspaceToolProvider, DockerCommandExecutor
from pulse_harness.core import HarnessScope, ModelRoundStatus, SessionStatus, TurnStatus
from pulse_harness.core.errors import HarnessError as KernelError, LeaseFenced, ScopeConflict
from pulse_harness.core.lifecycle import HarnessRuntimeBootstrap
from pulse_harness.core.provider import validate_model_adapter
from pulse_system.agent.harness.base import HarnessError, HarnessTurnResult
from pulse_system.interaction.api.harness_kernel import HarnessKernelService
from . import workspace_index, workspace_reading, native_replay

_TERMINAL = {TurnStatus.SUCCEEDED, TurnStatus.FAILED, TurnStatus.CANCELLED, TurnStatus.BLOCKED}


def _error(code: str, detail: str, *, accepted: bool | None = False) -> HarnessError:
    return HarnessError(code, detail, "inspect the Kernel session and retry through the world input API",
                        phase="kernel", prompt_accepted=accepted)


class _GuardedStream:
    def __init__(self, stream, guard, finish):
        self.stream, self.guard, self.finish = stream, guard, finish
        self._closed = False
        self._close_done = threading.Event()
        self._close_error: BaseException | None = None
        self._lock = threading.Lock()

    def __iter__(self):
        try:
            for delta in self.stream:
                self.guard()
                yield delta
        finally:
            self.close()

    def close(self):
        with self._lock:
            first = not self._closed
            self._closed = True
        if not first:
            self._close_done.wait()
            if self._close_error is not None:
                raise self._close_error
            return
        try:
            self.stream.close()
        except BaseException as exc:
            self._close_error = exc
            raise
        finally:
            try:
                self.finish(getattr(self.stream, "usage", None))
            finally:
                self._close_done.set()


class _GuardedModel:
    def __init__(self, model, guard):
        self.model, self.guard = model, guard

    @property
    def capabilities(self):
        return self.model.capabilities

    def preflight_stream(self):
        self.guard()
        self.model.preflight_stream()

    def stream(self, request, *, cancel_event=None):
        self.guard()
        stream = self.model.stream(request, cancel_event=cancel_event)
        return _GuardedStream(stream, self.guard, lambda _wire: None)

    def close(self):
        self.model.close()


class _WorldWorkspace(CommandWorkspaceToolProvider):
    def __init__(self, *args, guard, mutation_guard, allow_mutations, history_scopes, life_hub, cognition, cognition_authorize, work, team, local=None, posture=None, replay_authorize=None, **kwargs):
        self._guard, self._mutation_guard = guard, mutation_guard
        self._allow_mutations = allow_mutations
        super().__init__(*args, command_guard=guard, command_start_guard=mutation_guard, **kwargs)
        self._life = life_hub
        self._work = work
        self._team = team
        self._local = local
        self._posture = posture
        self._replay_authorize = replay_authorize
        self._cognition = cognition
        self._cognition_authorize = cognition_authorize
        self._history = ConversationHistoryTools(self.store, history_scopes)

    def descriptors(self):
        return tuple(d for d in super().descriptors()
                     if self._allow_mutations or not d.mutable) + (workspace_index.descriptor(), workspace_reading.descriptor(), native_replay.descriptor()) + self._history.descriptors() + life_tools.descriptors() + (() if self._team is None else (self._team_descriptor(),)) + (() if self._work is None else (self._work_descriptor(),)) + (() if self._cognition is None else (self._cognition_descriptor(),)) + (() if self._local is None else (self._local_descriptor(),)) + (() if self._posture is None else (self._posture_descriptor(),))

    def descriptors_for_scope(self, scope):
        return ((native_replay.descriptor(),) if native_replay.mode(self.store, scope)
                else self.descriptors())

    def model_round_limit_for_scope(self, scope, configured):
        entry = native_replay.mode(self.store, scope)
        return min(configured, entry['model_round_ceiling']) if entry else configured

    def _local_descriptor(self):
        from .native_local import descriptor
        return descriptor(allow_mutations=self._allow_mutations)

    def _team_descriptor(self):
        from .native_team import descriptor
        return descriptor()

    def _work_descriptor(self):
        from .native_work import descriptor
        return descriptor()

    def _posture_descriptor(self):
        from .native_posture import descriptor
        return descriptor()

    def _cognition_descriptor(self):
        from stream_cognition.native import descriptor
        return descriptor()

    def execute_for_scope(self, scope, call, cancellation, *, action_request_id=None):
        self._guard()
        native_replay.check_tool(self.store, scope, call.name)
        if call.name == native_replay.NAME:
            with self._mutation_guard():
                return native_replay.execute(self, scope, call, cancellation,
                                             action_request_id=action_request_id)
        if call.name == workspace_reading.NAME:
            with self._mutation_guard():
                return workspace_reading.execute(self, scope, call, cancellation,
                                                 action_request_id=action_request_id)
        if call.name == workspace_index.NAME:
            with self._mutation_guard():
                return workspace_index.browse(self, scope, call, cancellation)
        if call.name == "local":
            if self._local is None:
                raise ScopeConflict("native local control is unavailable")
            return self._local.execute(scope, call, cancellation, action_request_id=action_request_id)
        if call.name == "team":
            if self._team is None:
                raise ScopeConflict("native team is unavailable")
            return self._team.execute(scope, call, cancellation, action_request_id=action_request_id)
        if call.name == "work":
            if self._work is None:
                raise ScopeConflict("native Work is unavailable")
            return self._work.execute(scope, call, cancellation, action_request_id=action_request_id)
        if call.name == "posture":
            if self._posture is None:
                raise ScopeConflict("posture learning is not enabled")
            return self._posture.execute(scope, call, cancellation, action_request_id=action_request_id)
        if call.name == "cognition":
            if self._cognition is None:
                raise ScopeConflict("native cognition is not enabled")
            return self._cognition.execute(scope, call, cancellation,
                action_request_id=action_request_id, authorize=self._cognition_authorize)
        if call.name in life_tools.LIFE_SCHEMAS:
            return life_tools.execute(self._life, scope, call, cancellation)
        if call.name in HISTORY_SCHEMAS:
            result = self._history.execute_for_scope(scope, call, cancellation)
            return native_replay.annotate_history(self, scope, result)
        if call.name != "read_file" and not self._allow_mutations:
            raise ScopeConflict("the selected world profile is read-only")
        if call.name == "run_command":
            # Admit briefly under the publication guard, then release it while
            # the bounded command runs; shutdown must be able to cancel it.
            return super().execute_for_scope(scope, call, cancellation, action_request_id=action_request_id)
        # World publication revocation is linearized with file execution; the
        # provider's own checkpoint/digest/approval authority remains intact.
        with self._mutation_guard():
            return super().execute_for_scope(scope, call, cancellation, action_request_id=action_request_id)

    def restore(self, scope, checkpoint_id):
        self._guard()
        if not self._allow_mutations:
            raise ScopeConflict("the selected world profile is read-only")
        with self._mutation_guard():
            return super().restore(scope, checkpoint_id)


class _WorldKernelService(HarnessKernelService):
    """Expose observation/approval for world turns without a second input rail."""
    def __init__(self, *args, guard, approval_guard, **kwargs):
        self._world_guard = guard
        self._approval_guard = approval_guard
        super().__init__(*args, **kwargs)

    def _require_open(self):
        super()._require_open()
        self._world_guard()

    def resolve_approval(self, session_scope, action_request_id, decision):
        scope = self._scope_for_action(session_scope, action_request_id)
        with self._approval_guard(scope):
            return super().resolve_approval(session_scope, action_request_id, decision)

    def close(self):
        # Native runtime has already joined world and approval callers. Unlike
        # the generic observer, failed adapter cleanup must not prove exit.
        with self._runtime_lock:
            if self._closed:
                return
            for runtime in self._runtimes.values():
                runtime.runner.close()
            self._runtimes.clear()
            self.store.close()
            self._closed = True

    def create(self, scope):
        raise ScopeConflict("create world sessions through the PulseWorld, not the Kernel observer")

    def run_turn(self, scope, *, prompt=None):
        raise ScopeConflict("submit world input through the PulseWorld; observer cannot bypass its ledger")

    def control(self, scope, *, operation, prompt=None, reason="user_control"):
        if operation == "steer":
            raise ScopeConflict("submit steering through world input so its history is retained")
        return super().control(scope, operation=operation, prompt=prompt, reason=reason)


class KernelHarnessRuntime:
    """One world's persistent Engram sessions over the existing TurnRunner.

    A world worker stays attached while approval is pending; the ordinary
    Kernel approval API settles the same turn. This preserves one causal
    terminal in the world rather than returning a fake success at the pause.
    """
    def __init__(self, *, db_path: str | Path, workspace: str | Path,
                 checkpoint_root: str | Path | None = None,
                 model_factory: Callable[[HarnessScope], ChatHTTPModelAdapter] | None = None,
                 allow_mutations: bool = False,
                 allow_unconfigured: bool = False,
                 enable_posture: bool | None = None) -> None:
        self.db_path = str(db_path) if str(db_path) == ":memory:" else str(Path(db_path).expanduser().resolve())
        self.workspace = Path(workspace).expanduser().resolve()
        self.checkpoint_root = checkpoint_root
        # Per Pulse (Apache-2.0): an explicitly unconfigured Kernel can own
        # durable pages while the UI is offline. It never executes a fake turn.
        # This opt-in also avoids ambient provider discovery for example data;
        # ordinary callers retain the existing environment-based preflight.
        self._model_factory = (
            model_factory if model_factory is not None or allow_unconfigured
            else model_factory_from_env()
        )
        self._allow_unconfigured = allow_unconfigured
        self._allow_mutations = allow_mutations
        self.life = LifeHub()
        self.work = None
        self.team = None
        self.local = None
        self.cognition = None
        self.coverme = None
        self.expert = None
        self.posture = None
        self.posture_memory = None
        from .posture import posture_enabled
        if enable_posture is not None and type(enable_posture) is not bool:
            raise ValueError("enable_posture must be a boolean")
        self.posture_enabled = posture_enabled() if enable_posture is None else enable_posture
        from .expert_computation import expert_enabled
        self._expert_enabled = expert_enabled()
        from .coverme import load_selected_wiki
        self._coverme_index = load_selected_wiki(self.workspace)
        self._cognition_steps = self._cognition_allowance()
        self._command_executor = None
        self.kernel: _WorldKernelService | None = None
        self._world_id: str | None = None
        if self.db_path != ":memory:" and Path(self.db_path).is_relative_to(self.workspace):
            raise ValueError("Kernel database must be outside the writable workspace")
        self._database_owner = None
        self._guard_callback = None
        self._history_lineage = None
        self._context_for_turn = None
        self._input_for_turn = None
        self._publication = None
        self._lock = threading.Condition(threading.RLock())
        # Turn admission is serialized per subject-owned context. Lifecycle
        # operations (abort, succession, shutdown) remain subject-wide.
        self._active: dict[tuple[str, str], HarnessScope] = {}
        self._runner_locks: dict[str, Any] = {}
        self._approval_calls = 0
        self._shutdown_lock = threading.Lock()
        self._closed = False
        self._closing = False

    @staticmethod
    def _cognition_allowance():
        flag = os.environ.get("P_PULSE_COGNITION", "0")
        if flag not in ("0", "1"):
            raise ValueError("P_PULSE_COGNITION must be 0 or 1")
        if flag == "0":
            return None
        raw = os.environ.get("P_PULSE_COGNITION_STEPS", "")
        if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 100000:
            raise ValueError("enabled cognition requires P_PULSE_COGNITION_STEPS (1..100000 durable cumulative steps)")
        try:
            from stream_cognition.native import NativeController, RETURN_PROTOCOL
        except ImportError as exc:
            raise ValueError("install the matching optional pulse-stream-cognition module before enabling it") from exc
        if type(RETURN_PROTOCOL) is not int or RETURN_PROTOCOL != 1:
            raise ValueError("install a matching pulse-stream-cognition module with native return protocol 1")
        return int(raw)

    def preflight(self) -> None:
        if self._model_factory is None:
            if self._allow_unconfigured:
                return
            raise _error("provider_unconfigured", "set P_PULSE_CHAT_ENDPOINT, P_PULSE_CHAT_MODEL and the credential environment variable")
        scope = HarnessScope("preflight", "preflight", "preflight", 0, "preflight")
        adapter = self._model_factory(scope)
        try:
            validate_model_adapter(adapter)
        except KernelError as exc:
            raise _error(exc.code, str(exc)) from exc
        finally:
            if adapter is not None:
                adapter.close()

    def bind_world(self, world_id: str, guard: Callable[[], Any], publication,
                   *, history_lineage: Callable[[str], tuple[str, ...]] | None = None,
                   context_for_turn: Callable[[str, str], str] | None = None, work=None, team=None, local=None,
                   input_for_turn: Callable[[str, str], str | None] | None = None,
                   world_storage=None, posture=None) -> None:
        """Called by RuntimeService after it owns/reconciles the durable world."""
        if self.kernel is not None:
            raise RuntimeError("Kernel runtime is already bound")
        self._world_id, self._guard_callback, self._publication = world_id, guard, publication
        self._history_lineage = history_lineage
        self._context_for_turn = context_for_turn
        self._input_for_turn = input_for_turn
        self.work = work
        self.team = team
        self.local = local
        self.posture = posture
        path = Path(self.db_path)
        if self.db_path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._check()
        self._claim_database(world_id)
        try:
            image = os.environ.get("P_PULSE_COMMAND_IMAGE")
            if image:
                self._command_executor = DockerCommandExecutor(self.workspace, image)
            if self._coverme_index is not None:
                from .coverme import CoverMeRuntime
                self.coverme = CoverMeRuntime(
                    ":memory:" if self.db_path == ":memory:" else self.db_path + ".coverme.sqlite3",
                    self._coverme_index, guard=self._check,
                )
            if self.posture_enabled:
                if world_storage is None:
                    raise ValueError("posture requires the owning World's storage")
                from .posture import PostureMemory
                self.posture_memory = PostureMemory(
                    world_storage, world_id, guard=self._check, history_lineage=self._history_lineage,
                )
            if self._expert_enabled or self.posture_enabled:
                from .expert_computation import ExpertRuntime
                self.expert = ExpertRuntime(
                    ":memory:" if self.db_path == ":memory:" else self.db_path + ".expert.sqlite3",
                    guard=self._check, posture_memory=self.posture_memory,
                    expert_active=self._expert_enabled,
                )
            self.kernel = _WorldKernelService(
                self.db_path, self.workspace, self.checkpoint_root, guard=self._check,
                approval_guard=self._approval_guard,
                model_adapter_factory=self._make_model,
                response_reviewer_factory=(self._make_response_reviewer
                    if self.coverme is not None or self.posture_memory is not None else None),
                turn_advisor_factory=(self.expert.advisor if self.expert is not None else None),
                tool_provider_factory=lambda store, root, checkpoints: _WorldWorkspace(
                    store, root, checkpoints, guard=self._check,
                    mutation_guard=self._mutation_guard, allow_mutations=self._allow_mutations,
                    command_executor=self._command_executor, history_scopes=self._history_scopes, life_hub=self.life,
                    cognition=self.cognition, cognition_authorize=self._cognition_authorize, work=self.work, team=self.team, local=self.local, posture=self.posture, replay_authorize=self.authorize_replay),
            )
            if self._cognition_steps is not None:
                if self.db_path == ":memory:":
                    raise ValueError("native cognition requires a private durable Kernel path")
                from stream_cognition.native import NativeController
                self.cognition = NativeController(self.db_path + ".cognition.sqlite3",
                    step_limit=self._cognition_steps, config_for_scope=self._cognition_model_config,
                    check_scope=self._cognition_scope_check)
        except BaseException:
            if self.expert is not None:
                self.expert.close()
                self.expert = None
            if self.coverme is not None:
                self.coverme.close()
                self.coverme = None
            if self.cognition is not None:
                self.cognition.close()
            if self.kernel is not None:
                self.kernel.close()
                self.kernel = None
            # Failed optional environment setup must not strand the database
            # lease and prevent a corrected startup from reopening this world.
            if self._command_executor is not None:
                self._command_executor.close()
                self._command_executor = None
            if self._database_owner is not None:
                self._database_owner.close()
                self._database_owner = None
            raise

    def _make_response_reviewer(self, store, model, scope):
        from pulse_harness.core.response_review import CombinedResponseReviewer
        reviewers = []
        if self.coverme is not None:
            reviewers.append(("CoverMe", self.coverme.reviewer(store, model, scope)))
        if self.posture_memory is not None:
            reviewers.append(("posture", self.expert.posture_reviewer(store, model, scope)))
        if len(reviewers) == 1:
            return reviewers[0][1]
        return CombinedResponseReviewer(tuple(reviewers), max_payload_bytes=store.max_payload_bytes)

    def _claim_database(self, world_id):
        if self.db_path == ":memory:":
            return
        # A separate SQLite transaction is a cross-process lifetime mutex; it
        # never locks the transcript database itself. Persist the association
        # before holding the lock so crash/restart cannot change logical owner.
        connection = sqlite3.connect(self.db_path + ".owner.sqlite3", timeout=0,
                                     isolation_level=None, check_same_thread=False)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("CREATE TABLE IF NOT EXISTS world_owner (slot INTEGER PRIMARY KEY CHECK(slot=1), world_id TEXT NOT NULL)")
            row = connection.execute("SELECT world_id FROM world_owner WHERE slot=1").fetchone()
            if row is not None and row[0] != world_id:
                raise ScopeConflict("Kernel database belongs to a different PulseWorld")
            # Do not recover a previously standalone Kernel belonging to
            # another world just because it has no native owner file yet.
            if Path(self.db_path).is_file() and Path(self.db_path).stat().st_size:
                with closing(sqlite3.connect(Path(self.db_path).as_uri() + "?mode=ro", uri=True)) as prior:
                    has_sessions = prior.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
                    if has_sessions and prior.execute("SELECT 1 FROM sessions WHERE world_id != ? LIMIT 1", (world_id,)).fetchone():
                        raise ScopeConflict("existing Kernel history belongs to another world")
            if row is None:
                connection.execute("INSERT INTO world_owner VALUES(1, ?)", (world_id,))
            connection.execute("COMMIT")
            connection.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            connection.close()
            raise _error("kernel_database_unavailable", "Kernel database ownership is busy or unavailable") from exc
        except BaseException:
            connection.close()
            raise
        self._database_owner = connection

    def _make_model(self, scope):
        if self._model_factory is None:
            raise _error("provider_unconfigured", "AI is disconnected; configure a model before running a turn")
        adapter = self._model_factory(scope)
        # This bridge owns HTTP clients, not arbitrary child processes. New
        # transports need their own explicit lifecycle integration.
        if type(adapter) is not ChatHTTPModelAdapter:
            if adapter is not None:
                adapter.close()
            raise ScopeConflict("native world currently supports ChatHTTPModelAdapter only")
        adapter.extend_tool_schemas({
            workspace_index.NAME: workspace_index.SCHEMA,
            workspace_reading.NAME: workspace_reading.SCHEMA,
            native_replay.NAME: native_replay.SCHEMA,
        })
        if self.cognition is not None:
            from stream_cognition.native import SCHEMA, TOOL_NAME
            adapter.extend_tool_schemas({TOOL_NAME: SCHEMA})
        if self.work is not None:
            from .native_work import SCHEMA, NAME
            adapter.extend_tool_schemas({NAME: SCHEMA})
        if self.posture is not None:
            from .native_posture import SCHEMA, NAME
            adapter.extend_tool_schemas({NAME: SCHEMA})
        if self.team is not None:
            from .native_team import SCHEMA, NAME
            adapter.extend_tool_schemas({NAME: SCHEMA})
        if self.local is not None:
            from .native_local import schema, NAME
            adapter.extend_tool_schemas({NAME: schema(allow_mutations=self._allow_mutations)})
        adapter.life_image_resolver = self.life.resolve_image
        return _GuardedModel(adapter, self._check)

    def _cognition_scope_check(self, scope):
        self._check()
        if scope.context_id:
            raise ScopeConflict("first native cognition controller is Home-only")
        if scope.session_scope() != self.scope_for(scope.engram_id):
            raise ScopeConflict("cognition is not bound to this native Home")
        session = self.kernel.store.get_session(scope.session_scope())
        if session.status not in {SessionStatus.CREATED, SessionStatus.ACTIVE}:
            raise LeaseFenced("Home cognition owner is no longer active")

    def _cognition_authorize(self, scope, action_request_id, call):
        self._cognition_scope_check(scope)
        with self._lock:
            if self._active.get((scope.engram_id, scope.context_id)) != scope or not scope.turn_id:
                raise ScopeConflict("cognition control requires the exact active Home turn")
        action = self.kernel.store.get_action(scope, action_request_id)
        if action.tool_name != call.name or dict(action.arguments) != dict(call.arguments) or action.terminal:
            raise ScopeConflict("cognition control does not match the native action")

    def authorize_work(self, scope, action_request_id, call):
        if call.arguments.get("op") == "report":
            self._check()
            if (not scope.context_id.startswith("work:")
                    or scope.session_scope() != self.scope_for(scope.engram_id, scope.context_id)):
                raise ScopeConflict("report is bound to the current owned Work, never Home")
            if self.kernel.store.get_session(scope.session_scope()).status not in {
                    SessionStatus.CREATED, SessionStatus.ACTIVE}:
                raise LeaseFenced("report Work is not active")
        else:
            self._cognition_scope_check(scope)  # All other operations remain Home-only.
        with self._lock:
            if self._active.get((scope.engram_id, scope.context_id)) != scope or not action_request_id:
                raise ScopeConflict("Work control requires the exact active context turn")
        action = self.kernel.store.get_action(scope, action_request_id)
        if action.tool_name != "work" or dict(action.arguments) != dict(call.arguments) or action.terminal:
            raise ScopeConflict("Work control does not match the current native action")

    def authorize_replay(self, scope, action_request_id, call):
        self._check()
        if scope.session_scope() != self.scope_for(scope.engram_id, scope.context_id):
            raise ScopeConflict("replay belongs to its original native context")
        with self._lock:
            if self._active.get((scope.engram_id, scope.context_id)) != scope or not action_request_id:
                raise ScopeConflict("replay requires its attached original turn")
        action = self.kernel.store.get_action(scope, action_request_id)
        if action.tool_name != native_replay.NAME or dict(action.arguments) != dict(call.arguments) or action.terminal:
            raise ScopeConflict("replay does not match its native action")

    def authorize_posture(self, scope, action_request_id, call):
        self._check()
        if not self.posture_enabled or scope.session_scope() != self.scope_for(scope.engram_id, scope.context_id):
            raise ScopeConflict("posture belongs to the current owned context")
        if self.kernel.store.get_session(scope.session_scope()).status not in {
                SessionStatus.CREATED, SessionStatus.ACTIVE}:
            raise LeaseFenced("posture context is no longer active")
        with self._lock:
            if self._active.get((scope.engram_id, scope.context_id)) != scope or not action_request_id:
                raise ScopeConflict("posture requires the exact active context turn")
        action = self.kernel.store.get_action(scope, action_request_id)
        if action.tool_name != "posture" or dict(action.arguments) != dict(call.arguments) or action.terminal:
            raise ScopeConflict("posture does not match its native action")

    def authorize_local(self, scope, action_request_id, call):
        self._check()
        if scope.context_id or scope.session_scope() != self.scope_for(scope.engram_id):
            raise ScopeConflict("local control belongs to the current local context, never a Work")
        session = self.kernel.store.get_session(scope.session_scope())
        if session.status not in {SessionStatus.CREATED, SessionStatus.ACTIVE}:
            raise LeaseFenced("local context is no longer active")
        with self._lock:
            if self._active.get((scope.engram_id, "")) != scope or not action_request_id:
                raise ScopeConflict("local control requires the exact active context turn")
        action = self.kernel.store.get_action(scope, action_request_id)
        if action.tool_name != "local" or dict(action.arguments) != dict(call.arguments) or action.terminal:
            raise ScopeConflict("local control does not match the current native action")

    def _cognition_model_config(self, scope):
        self._cognition_scope_check(scope)
        adapter = self._model_factory(scope)
        try:
            if type(adapter) is not ChatHTTPModelAdapter:
                raise ScopeConflict("native cognition requires the configured ChatHTTP adapter")
            adapter.preflight_stream()
            return adapter.config
        finally:
            if adapter is not None:
                adapter.close()

    @contextmanager
    def _approval_guard(self, scope):
        with self._lock:
            self._check()
            active_key = (scope.engram_id, scope.context_id)
            if self._active.get(active_key) != scope:
                raise ScopeConflict("approval has no attached world turn; cancel or reconcile it instead")
            self._approval_calls += 1
            run_lock = self._runner_locks[scope.session_id]
        try:
            # WAITING_APPROVAL is durable just before the runner yields its
            # execution lease. A fast user click must wait for that yield,
            # not approve an action and then fail to resume an active runner.
            with run_lock:
                with self._lock:
                    self._check()
                    if self._active.get(active_key) != scope:
                        raise ScopeConflict("approval has no attached world turn; cancel or reconcile it instead")
                yield
        finally:
            with self._lock:
                self._approval_calls -= 1
                self._lock.notify_all()

    def _check(self):
        if self._closed or self._closing:
            raise LeaseFenced("world Kernel runtime is closed")
        if self._world_id is None or self._guard_callback is None:
            raise LeaseFenced("world Kernel is not yet bound")
        try:
            self._guard_callback()
        except Exception as exc:
            raise LeaseFenced("world runtime owner is no longer current") from exc

    @contextmanager
    def _mutation_guard(self):
        self._check()
        with self._publication.transaction_guard():
            self._check()
            yield

    def scope_for(self, engram_id: str, context_id: str = "") -> HarnessScope:
        if not isinstance(engram_id, str) or not engram_id.strip() or self._world_id is None:
            raise _error("kernel_scope_invalid", "a bound world and non-empty Engram identity are required")
        if not isinstance(context_id, str):
            raise _error("kernel_scope_invalid", "context identity must be text")
        context_id = context_id.strip()
        # The empty context is the historical Home/default scope. Preserve its
        # exact session id so existing native histories remain addressable.
        if context_id:
            context_digest = hashlib.sha256(context_id.encode("utf-8")).hexdigest()[:24]
            session_id = f"pulse-kernel:{self._world_id}:{engram_id}:ctx:{context_digest}"
        else:
            session_id = f"pulse-kernel:{self._world_id}:{engram_id}"
        # Stable logical Kernel ownership is separate from physical World
        # owner/epoch. Every execution checks the latter through _check().
        return HarnessScope(
            world_id=self._world_id,
            engram_id=engram_id,
            session_id=session_id,
            epoch=0,
            owner_id=f"pulse-world:{self._world_id}",
            context_id=context_id,
            capability_digest="pulse-world-kernel.v1",
        )

    def _history_scopes(self, scope: HarnessScope) -> tuple[HarnessScope, ...]:
        self._check()
        if scope.session_scope() != self.scope_for(scope.engram_id, scope.context_id):
            raise ScopeConflict("history caller is not this world's native session")
        lineage = (self._history_lineage(scope.engram_id) if self._history_lineage
                   is not None else (scope.engram_id,))
        own = tuple(self.scope_for(engram_id, scope.context_id) for engram_id in lineage)
        extra = self.team.history_scopes(scope) if self.team is not None else ()
        return (own + tuple(s for s in extra if s not in own))[:256]

    def _session(self, engram_id: str, context_id: str = ""):
        self._check()
        assert self.kernel is not None
        scope = self.scope_for(engram_id, context_id)
        self.kernel.store.create_session(scope)
        return scope, self.kernel._runtime(scope)

    def snapshot(self, engram_id: str) -> dict[str, Any]:
        scope = self.scope_for(engram_id)
        assert self.kernel is not None
        try:
            session = self.kernel.store.get_session(scope)
        except KernelError as exc:
            if isinstance(exc, ScopeConflict) and str(exc).startswith("unknown session:"):
                raise _error("harness_session_unknown", "Engram has not yet opened a Kernel session") from exc
            raise _error(exc.code, str(exc)) from exc
        with self._lock:
            active = any(key[0] == engram_id for key in self._active)
        return {"engram_id": engram_id, "session_id": scope.session_id,
                "session_file": self.db_path, "scope": scope.as_dict(),
                "state": "RUNNING" if active else "READY", "adapter_kind": "kernel",
                "bootstrapped": session.transcript_cursor > 0, "cursor": session.transcript_cursor}

    def summary(self, engram_ids=()):
        with self._lock:
            sessions = []
            states: dict[str, int] = {}
            if self.kernel is not None and not self._closed:
                requested = tuple(dict.fromkeys(engram_ids))
                for session in self.kernel.store.sessions_for_world(
                    self._world_id,
                    engram_ids=requested,
                ):
                    states[session.status.value] = states.get(session.status.value, 0) + 1
                    sessions.append({"engram_id": session.scope.engram_id,
                                     "scope": session.scope.as_dict(),
                                     "status": session.status.value,
                                     "cursor": session.transcript_cursor})
            resident = 0 if self.kernel is None else len(self.kernel._runtimes)
            return {"kind": "kernel", "active_turns": len(self._active),
                    "resident_clients": resident, "live_sessions": resident,
                    "bindings": len(sessions), "states": states, "sessions": sessions,
                    "active_approvals": self._approval_calls,
                    "adapter_contract": "pulse_owned_kernel_http",
                    "state": "closed" if self._closed else "closing" if self._closing else "ready"}

    def run_turn(self, engram_id: str, prompt: str, *, timeout_sec: float | None = None,
                 bootstrap_text: str | None = None, turn_id: str | None = None) -> HarnessTurnResult:
        if timeout_sec is not None and (isinstance(timeout_sec, bool) or not math.isfinite(timeout_sec) or timeout_sec <= 0):
            raise _error("kernel_timeout_invalid", "turn timeout must be positive and finite")
        if not isinstance(prompt, str) or (bootstrap_text is not None and not isinstance(bootstrap_text, str)):
            raise _error("kernel_input_invalid", "prompt and initial context must be text")
        accepted = False
        claimed = False
        active_key = None
        timer = None
        runtime = None
        try:
            context_id = ""
            if turn_id is not None and self._context_for_turn is not None:
                context_id = self._context_for_turn(engram_id, turn_id)
                if not isinstance(context_id, str):
                    raise _error("kernel_context_invalid", "runtime context resolver returned a non-text identity")
            with self._lock:
                self._check()
                active_key = (engram_id, context_id.strip())
                if active_key in self._active:
                    raise _error(
                        "kernel_turn_busy",
                        "this Engram context already has a running turn",
                    )
                scope, runtime = self._session(engram_id, context_id)
                scope = scope.for_turn(turn_id or str(uuid4()))
                self._active[active_key] = scope
                run_lock = self._runner_locks.setdefault(scope.session_id, threading.RLock())
                claimed = True
            assert self.kernel is not None
            first = self.kernel.store.get_session(scope.session_scope()).transcript_cursor == 0
            submitted = "\n\n".join(part for part in (bootstrap_text if first else None, prompt) if part)
            # An old pending/blocked turn is never silently displaced on
            # restart. It remains observable and explicitly cancellable.
            for previous in self.kernel.store.turns_for_session(scope.session_scope(), limit=128):
                if previous.scope.turn_id != scope.turn_id and previous.status not in {
                    TurnStatus.SUCCEEDED, TurnStatus.FAILED, TurnStatus.CANCELLED}:
                    raise _error("kernel_recovery_required", "resolve the previous nonterminal Kernel turn before new work")
            internal = self._input_for_turn(engram_id, scope.turn_id) if self._input_for_turn and turn_id is not None else None
            if internal is not None:
                # The trusted host supplies only provenance and an instruction to
                # fetch actual work through a real tool. Never forge a user turn.
                from pulse_harness.core.transcript import TranscriptStore
                submitted = internal
            self.kernel.store.create_turn(scope, submitted)
            if internal is not None:
                TranscriptStore(self.kernel.store).append(scope, role="system", content=submitted,
                    item_id="cognition-return:" + scope.turn_id)
            accepted = True
            expired = threading.Event()
            def expire():
                expired.set()
                try:
                    runtime.runner.abort(scope, reason="world_turn_timeout")
                except KernelError:
                    pass
            if timeout_sec is not None:
                timer = threading.Timer(timeout_sec, expire)
                timer.daemon = True
                timer.start()
            with run_lock:
                self._check()
                result = runtime.runner.run(scope, prompt=None if internal is not None else submitted)
            while result.status not in _TERMINAL:
                self._check()
                time.sleep(0.025)
                result = runtime.runner._outcome(scope)
            self._check()
            rounds = self.kernel.store.model_rounds_for_turn(scope)
            if self.expert is not None:
                try:
                    self.expert.record_outcome(self.kernel.store, scope, result)
                except (sqlite3.Error, ValueError):
                    # Keep successful work deliverable; an unrecorded outcome
                    # cannot receive training credit and stays visibly pending.
                    logging.getLogger(__name__).warning("expert_outcome_not_recorded", exc_info=False)
            completed_rounds = tuple(
                round_record
                for round_record in rounds
                if round_record.status is ModelRoundStatus.COMPLETED
            )
            usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "provider_requests": len(completed_rounds),
            }
            accounted = True
            for round_record in completed_rounds:
                if round_record.provider_usage is None:
                    accounted = False
                    continue
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cached_tokens",
                    "cache_write_tokens",
                ):
                    usage[key] += int(round_record.provider_usage[key])
            from .coverme import recorded_fork_usage
            fork_usage, fork_accounted = recorded_fork_usage(self.kernel.store, scope)
            for key in usage:
                usage[key] += fork_usage[key]
            accounted = accounted and fork_accounted
            from .expert_computation import recorded_expert_usage
            expert_usage, expert_accounted = recorded_expert_usage(self.kernel.store, scope)
            for key in usage:
                usage[key] += expert_usage[key]
            accounted = accounted and expert_accounted
            if result.status is not TurnStatus.SUCCEEDED or not result.assistant_text.strip():
                code = "world_turn_timeout" if expired.is_set() else result.error_code or f"kernel_turn_{result.status.value}"
                raise _error(code, "Kernel turn did not produce a settled final response", accepted=True)
            projected, regime = native_replay.project_text(self.kernel.store, scope, result.assistant_text)
            return HarnessTurnResult(
                execution_regime=regime,
                engram_id=engram_id, session_file=self.db_path, session_id=scope.session_id,
                content=projected, stop_reason="stop", **usage,
                tool_calls=self.kernel.store.count_actions_for_turn(scope),
                evidence_class=(
                    "TEST_DOUBLE"
                    if runtime.runner.model_adapter.capabilities.test_only
                    else (
                        "KERNEL_PROVIDER_USAGE_DURABLE"
                        if accounted
                        else "KERNEL_PROVIDER_USAGE_UNACCOUNTED"
                    )
                ),
            )
        except KernelError as exc:
            raise _error(exc.code, str(exc), accepted=accepted) from exc
        finally:
            if timer is not None:
                timer.cancel()
                timer.join()
            with self._lock:
                if claimed and self._active.get(active_key) == locals().get("scope"):
                    self._active.pop(active_key, None)
                self._lock.notify_all()

    def abort(self, engram_id: str) -> None:
        assert self.kernel is not None
        with self._lock:
            scopes = tuple(
                scope
                for key, scope in self._active.items()
                if key[0] == engram_id
            )
        cancelled_work = self.work.cancel_subject(engram_id) if self.work is not None else 0
        cancelled_work += self.team.cancel_subject(engram_id) if self.team is not None else 0
        cancelled_work += self.posture.cancel_subject(engram_id) if self.posture is not None else 0
        paused = self.cognition.pause_subject(engram_id) if self.cognition is not None else 0
        if not scopes:
            if paused or cancelled_work:
                return
            raise _error("kernel_turn_not_running", "Engram has no attached world turn")
        for scope in scopes:
            self.kernel._runtimes[scope.session_id].runner.abort(
                scope,
                reason="world_abort",
            )

    def steer(self, engram_id: str, content: str) -> None:
        raise _error("kernel_steer_requires_world_input", "append the message through PulseWorld input; do not bypass its causal history")

    def succeed(self, old_engram_id: str, new_engram_id: str,
                *, capacity_timeout_sec: float | None = None) -> None:
        self._check()
        assert self.kernel is not None
        with self._lock:
            if any(key[0] == old_engram_id for key in self._active):
                raise _error("kernel_turn_busy", "finish the predecessor turn before succession")
        if self.cognition is not None:
            self.cognition.pause_subject(old_engram_id)
        for session in self.kernel.store.sessions_for_world(
            self._world_id,
            engram_ids=(old_engram_id,),
        ):
            if session.status is SessionStatus.ACTIVE:
                self.kernel.store.set_session_status(
                    session.scope,
                    SessionStatus.QUIESCED,
                )
        # The manager owns the new Engram and seed. We preserve every old
        # context transcript and leave the new generation unbootstrapped,
        # never copy it twice.

    def close_session(self, engram_id: str) -> None:
        with self._lock:
            active = any(key[0] == engram_id for key in self._active)
        if active:
            self.abort(engram_id)
        elif self.cognition is not None:
            self.cognition.pause_subject(engram_id)

    def close(self):
        # The Runtime coordinator bounds its wait and retains this owner until
        # all calls actually exit; we must not fake a joined result on timeout.
        with self._shutdown_lock:
            with self._lock:
                if self._closed:
                    return self._close_report
                self._closing = True
                self.life.close()  # revoke capture before waiting on model/approval owners
                active = tuple(self._active.items())
                observed = 0 if self.kernel is None else len(self.kernel._runtimes)
            if self.cognition is not None:
                self.cognition.close()
            if self.kernel is not None:
                for _, scope in active:
                    self.kernel._runtimes[scope.session_id].runner.abort(scope, reason="world_shutdown")
                with self._lock:
                    while self._active or self._approval_calls:
                        self._lock.wait()
                self.kernel.close()
            if self.coverme is not None:
                self.coverme.close()
            if self.expert is not None:
                self.expert.close()
            if self._command_executor is not None:
                self._command_executor.close()
            if self._database_owner is not None:
                self._database_owner.close()
                self._database_owner = None
            self._closed = True
            self._close_report = {
                "active_before": len(active), "sessions_observed": observed,
                "continuity_writers_sealed": observed, "unresolved": 0,
                "owner_joined": True, "process_tree_state": "not_applicable",
                "cancel_signalled": bool(active),
            }
            return self._close_report


class KernelHarnessFactory:
    kind = "kernel"

    def __init__(self, *, db_path: str | Path, workspace: str | Path,
                 checkpoint_root: str | Path | None = None,
                 model_factory: Callable[[HarnessScope], ChatHTTPModelAdapter] | None = None,
                 allow_mutations: bool = False,
                 allow_unconfigured: bool = False,
                 enable_posture: bool | None = None) -> None:
        self._kwargs = dict(db_path=db_path, workspace=workspace, checkpoint_root=checkpoint_root,
                            model_factory=model_factory, allow_mutations=allow_mutations,
                            allow_unconfigured=allow_unconfigured, enable_posture=enable_posture)
        self._bootstrapped = False

    def bootstrap(self, *, workspace, config, owner_id, epoch):
        if self._bootstrapped:
            raise RuntimeError("Kernel factory already bootstrapped")
        self._bootstrapped = True
        return HarnessRuntimeBootstrap(KernelHarnessRuntime(**self._kwargs), self.kind,
                                       object(), owner_id, epoch)


class DeferredAuxiliaryLLM:
    """Optional embedding/legacy helpers must not require a second key to boot.

    Invoking a helper resolves its real configured adapter or fails explicitly;
    no fake embedding or completion is substituted. Native turn accounting
    conservatively assumes no cache discount from this unrelated provider.
    """
    mock = False
    cache_read_discount = 1.0

    def __init__(self, **config):
        from pulse_system.substrate.llm.adapter import LLMStats
        self._config = config
        self.provider = config["provider"]
        self.model = config.get("model")
        self._adapter = None
        self._lock = threading.Lock()
        self.stats = LLMStats()

    def _get(self):
        with self._lock:
            if self._adapter is None:
                from pulse_system.substrate.llm.adapter import LLMAdapter
                self._adapter = LLMAdapter(**self._config)
            return self._adapter

    def complete(self, *args, **kwargs):
        return self._get().complete(*args, **kwargs)

    def embed(self, *args, **kwargs):
        return self._get().embed(*args, **kwargs)

    def get_stats(self):
        return self._adapter.get_stats() if self._adapter is not None else self.stats

    def estimate_tokens(self, text):
        from pulse_system.substrate.llm.adapter import estimate_tokens
        return estimate_tokens(text)

    def estimate_messages_tokens(self, messages):
        return sum(self.estimate_tokens(m["content"]) for m in messages)
