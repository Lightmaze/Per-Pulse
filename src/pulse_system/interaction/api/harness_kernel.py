"""Provider-neutral Observatory adapter for the independent Pulse Harness.

The historical ``/harness`` router observes the Pulse Pi compatibility rail.
This deliberately separate adapter exposes the independent kernel ABI.
It owns no model loop: ``HarnessKernelService`` delegates execution to the
provider-neutral ``TurnRunner`` and only projects its durable records into
bounded HTTP/SSE envelopes.

The default service has no model adapter.  A caller must explicitly inject a
verified adapter (the tests use a marked ``TEST_DOUBLE``); there is no mock or
Pi fallback in this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider
from pulse_harness.core import (
    ActionRecord,
    ActionStatus,
    ApprovalDecision,
    HarnessEvent,
    HarnessScope,
    HarnessStore,
    ModelRoundRecord,
    ModelRoundStatus,
    ProviderEvidence,
    RecoveryManager,
    SessionStatus,
    ToolProvider,
    TurnOutcome,
    TurnStatus,
    TurnRunner,
)
from pulse_harness.core.errors import (
    ApprovalConflict,
    HarnessError,
    InvalidReplayCursor,
    LeaseFenced,
    ScopeConflict,
    StoreUnavailable,
)
from pulse_harness.core.replay import digest_json

_logger = logging.getLogger("pulse_system.observatory.harness_kernel")

KERNEL_PROTOCOL = "pulse-harness-observatory.v1"
EVENT_PROTOCOL = "pulse-harness.event.v1"
MAX_API_EVENTS = 500
MAX_API_TURNS = 128
MAX_API_ACTIONS = 256
MAX_API_CHANGES = 256
MAX_API_ROUNDS = 256
MAX_API_RECOVERIES = 128
DEFAULT_STREAM_POLL_SEC = 0.25
MAX_STREAM_SECONDS = 300.0


class HarnessKernelApiError(HarnessError):
    """Stable adapter error for malformed or unavailable API inputs."""

    code = "harness_api_error"


@dataclass(frozen=True, slots=True)
class KernelSessionRuntime:
    """Process-local execution objects for one durable session."""

    scope: HarnessScope
    runner: TurnRunner
    provider: ToolProvider


class _ControlOnlyTools:
    """No effect surface for cancelling a recovered, unattached turn."""

    def descriptors(self):
        return ()

    def execute(self, _call, _cancellation):
        raise ScopeConflict("a control-only runner cannot execute tools")


ModelAdapterFactory = Callable[[HarnessScope], Any | None]
ToolProviderFactory = Callable[[HarnessStore, Path, Path], ToolProvider]
ResponseReviewerFactory = Callable[[HarnessStore, Any, HarnessScope], Any]
TurnAdvisorFactory = Callable[[HarnessStore, Any, HarnessScope], Any]


def _scope_wire(scope: HarnessScope) -> dict[str, Any]:
    return scope.as_dict()


def _record_wire(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        raw = value.as_dict()
        if isinstance(raw, Mapping):
            return dict(raw)
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        raw = asdict(value)
        return _json_safe(raw)
    if isinstance(value, Mapping):
        return _json_safe(dict(value))
    raise HarnessKernelApiError("durable record is not projectable", code="projection_invalid")


def _json_safe(value: Any) -> Any:
    """Bounded JSON-compatible projection without leaking implementation objects."""

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _event_envelope(event: HarnessEvent) -> dict[str, Any]:
    payload = _json_safe(dict(event.payload))
    if not isinstance(payload, Mapping):  # pragma: no cover - HarnessEvent contract
        payload = {"value": payload}
    return {
        "envelope_version": EVENT_PROTOCOL,
        "event_id": event.event_id,
        "item_id": event.item_id,
        "parent_item_id": event.parent_item_id,
        "scope": _scope_wire(event.scope),
        "world_id": event.scope.world_id,
        "engram_id": event.scope.engram_id,
        "context_id": event.scope.context_id,
        "session_id": event.scope.session_id,
        "turn_id": event.scope.turn_id,
        "epoch": event.scope.epoch,
        "owner_id": event.scope.owner_id,
        "capability_digest": event.scope.capability_digest,
        "seq": event.seq,
        "delta_seq": event.delta_seq,
        "revision": event.revision,
        "type": event.event_type,
        "event_type": event.event_type,
        "source": event.source,
        "phase": event.phase,
        "status": event.status,
        "payload": dict(payload),
        "payload_bytes": len(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "payload_digest": digest_json(payload),
        "action_request_id": event.action_request_id,
        "approval_id": event.approval_id,
        "operation_id": event.operation_id,
        "redacted": event.redacted,
        "truncated": event.truncated,
        "gap": event.gap,
        "occurred_at": event.created_at,
        "provenance": {
            "source_kind": "provider_neutral_harness",
            "adapter": "pulse_harness.core",
            "verified": True,
            "capabilities": ["durable_transcript", "bounded_replay"],
            "live_gate": "PROVIDER_FREE_CORE",
        },
    }


def _evidence_from_round(round_record: ModelRoundRecord | None) -> dict[str, Any]:
    if round_record is None:
        return {
            "source_kind": "provider_neutral_harness",
            "adapter": None,
            "adapter_version": None,
            "source": None,
            "verified": False,
            "test_only": False,
            "capabilities": [],
            "live_gate": "BLOCKED",
            "claim": "provider_unconfigured",
        }
    try:
        evidence = ProviderEvidence.from_dict(round_record.provider_evidence)
    except (TypeError, ValueError):
        return {
            "source_kind": "provider_evidence_invalid",
            "adapter": None,
            "adapter_version": None,
            "source": None,
            "verified": False,
            "test_only": False,
            "capabilities": [],
            "live_gate": "BLOCKED",
            "claim": "provider_evidence_invalid",
        }
    return {
        "source_kind": "model_adapter",
        "adapter": evidence.adapter_id,
        "adapter_version": evidence.adapter_version,
        "source": evidence.source,
        "verified": evidence.verified,
        "test_only": evidence.test_only,
        "capabilities": sorted(evidence.capabilities),
        "live_gate": "TEST_ONLY" if evidence.test_only else (
            "UNVERIFIED" if not evidence.verified else "VERIFIED_ADAPTER"
        ),
        "claim": "provider_free_test_double" if evidence.test_only else "adapter_evidence",
    }


def _outcome_wire(outcome: TurnOutcome) -> dict[str, Any]:
    return {
        "envelope_version": "pulse-harness.turn-outcome.v1",
        "scope": _scope_wire(outcome.scope),
        "session_id": outcome.scope.session_id,
        "turn_id": outcome.scope.turn_id,
        "status": outcome.status.value,
        "cursor": outcome.cursor,
        "assistant_text": outcome.assistant_text,
        "pending_action_request_id": outcome.pending_action_request_id,
        "error_code": outcome.error_code,
        "evidence": _json_safe(dict(outcome.evidence)),
    }


def _action_wire(action: ActionRecord) -> dict[str, Any]:
    return {
        "action_request_id": action.action_request_id,
        "scope": _scope_wire(action.scope),
        "tool_name": action.tool_name,
        "arguments": _json_safe(dict(action.arguments)),
        "args_digest": action.args_digest,
        "status": action.status.value,
        "terminal": action.terminal,
        "revision": action.revision,
        "approval_id": action.approval_id,
        "operation_id": action.operation_id,
        "result": _json_safe(dict(action.result)) if action.result is not None else None,
        "error_code": action.error_code,
        "result_delivered": action.result_delivered,
        "created_at": action.created_at,
        "updated_at": action.updated_at,
        "terminal_at": action.terminal_at,
    }


class HarnessKernelService:
    """Small host service that exposes the independent kernel.

    The service is intentionally not a second agent runtime.  Each call to
    ``TurnRunner`` remains the one model→tool→settle loop; this class only
    chooses the durable scope, provider-neutral workspace adapter and
    process-local runner needed by the HTTP observer/control surface.
    """

    def __init__(
        self,
        db_path: str | Path,
        workspace_root: str | Path,
        checkpoint_root: str | Path | None = None,
        *,
        model_adapter_factory: ModelAdapterFactory | None = None,
        tool_provider_factory: ToolProviderFactory | None = None,
        response_reviewer_factory: ResponseReviewerFactory | None = None,
        turn_advisor_factory: TurnAdvisorFactory | None = None,
        max_events_per_session: int = 4096,
        max_payload_bytes: int = 64 * 1024,
        approval_ttl_sec: float = 300.0,
    ) -> None:
        if model_adapter_factory is None:
            from pulse_harness.adapters.chat_http import model_factory_from_env
            model_adapter_factory = model_factory_from_env()
        self.store = HarnessStore(
            db_path,
            max_events_per_session=max_events_per_session,
            max_payload_bytes=max_payload_bytes,
            approval_ttl_sec=approval_ttl_sec,
        )
        self.workspace_root = Path(workspace_root).expanduser()
        self.checkpoint_root = Path(
            checkpoint_root
            if checkpoint_root is not None
            else self.workspace_root.parent / f"{self.workspace_root.name}-checkpoints"
        ).expanduser()
        self._model_adapter_factory = model_adapter_factory
        self._tool_provider_factory = tool_provider_factory
        self._response_reviewer_factory = response_reviewer_factory
        self._turn_advisor_factory = turn_advisor_factory
        self._runtimes: dict[str, KernelSessionRuntime] = {}
        self._runtime_lock = threading.RLock()
        self._closed = False
        # Process restart recovery is explicit and durable. Pending approvals
        # remain pending; possible physical effects are reconciled once.
        self._boot_recovery = RecoveryManager(self.store).recover(reason="process_restart")

    @property
    def protocol_version(self) -> str:
        return KERNEL_PROTOCOL

    def close(self) -> None:
        with self._runtime_lock:
            if self._closed:
                return
            self._closed = True
            runtimes = tuple(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            try:
                runtime.runner.close()
            except Exception:  # pragma: no cover - defensive shutdown boundary
                _logger.exception("kernel runner close failed")
        self.store.close()

    def reconcile_provider_usage(self, reconciler) -> dict[str, Any]:
        """Resolve durable usage gaps through explicit provider lookup ports."""

        self._require_open()
        from pulse_harness.core.usage_reconciliation import (
            ProviderUsageReconciliationManager,
        )

        report = ProviderUsageReconciliationManager(
            self.store, reconciler
        ).reconcile_all()
        return {
            "accepted": report.remaining_unaccounted_rounds == 0,
            "reconciled_round_ids": list(report.reconciled_round_ids),
            "blockers": [
                {
                    "round_id": blocker.round_id,
                    "reason_code": blocker.reason_code,
                }
                for blocker in report.blockers
            ],
            "remaining_unaccounted_rounds": report.remaining_unaccounted_rounds,
        }

    def _require_open(self) -> None:
        if self._closed:
            raise StoreUnavailable("Harness kernel service is closed", code="harness_closed")

    def _provider(self) -> ToolProvider:
        if self._tool_provider_factory is not None:
            return self._tool_provider_factory(
                self.store, self.workspace_root, self.checkpoint_root
            )
        return StructuredWorkspaceToolProvider(
            self.store, self.workspace_root, self.checkpoint_root
        )

    def _runtime(self, scope: HarnessScope) -> KernelSessionRuntime:
        with self._runtime_lock:
            self._require_open()
            session_scope = scope.session_scope()
            existing = self._runtimes.get(session_scope.session_id)
            if existing is not None:
                if existing.scope != session_scope:
                    raise LeaseFenced("kernel session runtime is fenced by scope")
                return existing
            provider = self._provider()
            adapter = (
                self._model_adapter_factory(session_scope)
                if self._model_adapter_factory is not None
                else None
            )
            runner = TurnRunner(
                self.store,
                model_adapter=adapter,
                tool_provider=provider,
                response_reviewer=(
                    self._response_reviewer_factory(self.store, adapter, session_scope)
                    if self._response_reviewer_factory is not None else None
                ),
                turn_advisor=(self._turn_advisor_factory(self.store, adapter, session_scope)
                              if self._turn_advisor_factory is not None else None),
            )
            runtime = KernelSessionRuntime(session_scope, runner, provider)
            self._runtimes[session_scope.session_id] = runtime
            return runtime

    def create(self, scope: HarnessScope) -> dict[str, Any]:
        self._require_open()
        session = self.store.create_session(scope.session_scope())
        if session.status is SessionStatus.CREATED:
            session = self.store.set_session_status(
                scope.session_scope(), SessionStatus.ACTIVE
            )
        self._runtime(scope)
        return self.session_projection(scope.session_scope())

    def wake(self, scope: HarnessScope) -> dict[str, Any]:
        """Rebind one durable session after restart without replaying effects."""

        self._require_open()
        self.store.get_session(scope.session_scope())
        self._runtime(scope)
        return self.session_projection(scope.session_scope())

    def run_turn(
        self,
        scope: HarnessScope,
        *,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        runtime = self._runtime(scope)
        outcome = runtime.runner.run(scope, prompt=prompt)
        return {
            "protocol_version": KERNEL_PROTOCOL,
            "outcome": _outcome_wire(outcome),
            "session": self.session_projection(scope.session_scope()),
        }

    def resolve_approval(
        self,
        session_scope: HarnessScope,
        action_request_id: str,
        decision: ApprovalDecision,
    ) -> dict[str, Any]:
        scope = self._scope_for_action(session_scope, action_request_id)
        runtime = self._runtime(scope)
        approval, action = self.store.resolve_approval(scope, action_request_id, decision)
        outcome: TurnOutcome | None = None
        if decision is ApprovalDecision.ALLOW_ONCE or action.terminal:
            outcome = runtime.runner.resume(scope)
            # ``resolve_approval`` returns the atomic STARTING snapshot for an
            # allow_once decision.  The continuation may already have settled
            # by the time the HTTP response is projected; refresh the durable
            # action so Workbench never presents a completed effect as merely
            # starting.
            action = self.store.get_action(scope, action_request_id)
        return {
            "protocol_version": KERNEL_PROTOCOL,
            "approval": _record_wire(approval),
            "action": _action_wire(action),
            "outcome": _outcome_wire(outcome) if outcome is not None else None,
            "session": self.session_projection(session_scope),
        }

    def control(
        self,
        scope: HarnessScope,
        *,
        operation: str,
        prompt: str | None = None,
        reason: str = "user_control",
    ) -> dict[str, Any]:
        if operation in {"interrupt", "abort", "cancel"}:
            # Cancellation is a control operation even when no model is
            # configured. Recovered sessions have no attached process runner;
            # constructing their provider would incorrectly require credentials
            # merely to stop old work. Use the same canonical abort state
            # machine with a read-only model-round permit and no tool surface.
            # Do not cache this object: later actual work must still go through
            # the normal provider/worker construction path.
            with self._runtime_lock:
                self._require_open()
                self.store.get_turn(scope)  # Existing durable scope is required.
                session_scope = scope.session_scope()
                runtime = self._runtimes.get(session_scope.session_id)
                if runtime is not None:
                    if runtime.scope != session_scope:
                        raise LeaseFenced("kernel session runtime is fenced by scope")
                    outcome = runtime.runner.abort(scope, reason=reason)
                else:
                    runner = TurnRunner(
                        self.store, model_adapter=None, tool_provider=_ControlOnlyTools(),
                    )
                    try:
                        outcome = runner.abort(scope, reason=reason)
                    finally:
                        runner.close()
        elif operation == "steer":
            if prompt is None:
                raise HarnessKernelApiError("steer requires prompt", code="prompt_required")
            runtime = self._runtime(scope)
            outcome = runtime.runner.steer(scope, prompt)
        else:
            raise HarnessKernelApiError("unsupported control operation", code="control_invalid")
        return {
            "protocol_version": KERNEL_PROTOCOL,
            "operation": operation,
            "accepted": True,
            "outcome": _outcome_wire(outcome),
            "session": self.session_projection(scope.session_scope()),
        }

    def recover(self, scope: HarnessScope) -> dict[str, Any]:
        """Return recovery evidence for one session without mutating the store.

        ``RecoveryManager.recover`` is a startup/reconciliation operation.  It
        is intentionally never used from this GET surface: observing A must
        not terminalize an in-flight action owned by B.
        """

        self._require_open()
        session_scope = scope.session_scope()
        session_records = self.store.recovery_records_for_session(
            session_scope,
            limit=MAX_API_RECOVERIES,
        )
        return {
            "protocol_version": KERNEL_PROTOCOL,
            "session_id": session_scope.session_id,
            "scope": _scope_wire(session_scope),
            "records": [_record_wire(record) for record in session_records][:MAX_API_RECOVERIES],
            "pending_approval_count": self.store.pending_approval_count_for_session(
                session_scope
            ),
            "observation_only": True,
            "mutation_performed": False,
            "evidence": {
                "source_kind": "durable_recovery_observer",
                "adapter": "pulse_harness.core",
                "verified": True,
                "live_gate": "PROVIDER_FREE_CORE",
            },
        }

    def _events(self, scope: HarnessScope, *, after_seq: int, limit: int):
        page = self.store.replay(
            scope.session_scope(),
            after_seq=after_seq,
            limit=min(max(1, limit), MAX_API_EVENTS, self.store.max_events_per_session),
        )
        return page

    def replay(
        self,
        scope: HarnessScope,
        *,
        after_seq: int = 0,
        limit: int = MAX_API_EVENTS,
    ) -> dict[str, Any]:
        page = self._events(scope, after_seq=after_seq, limit=limit)
        gap = None
        if page.gap:
            gap = {
                "missing_from": after_seq + 1,
                "missing_to": max(after_seq + 1, page.retained_from - 1),
                "reason": "retention_window",
                "scope": _scope_wire(scope.session_scope()),
                "turn_id": "",
                "item_id": None,
                "event_id": None,
                "seq": page.retained_from,
                "revision": 0,
                "type": "replay.gap",
                "source": "harness",
                "phase": "replay",
                "status": "gap",
                "provenance": {
                    "source_kind": "provider_neutral_harness",
                    "adapter": "pulse_harness.core",
                    "verified": True,
                    "live_gate": "PROVIDER_FREE_CORE",
                },
            }
        return {
            "protocol_version": KERNEL_PROTOCOL,
            "envelope_version": "pulse-harness.replay.v1",
            "session_id": scope.session_id,
            "scope": _scope_wire(scope.session_scope()),
            "turn_id": "",
            "item_id": None,
            "event_id": None,
            "seq": page.next_seq,
            "delta_seq": 0,
            "revision": 0,
            "type": "replay.cursor",
            "event_type": "replay.cursor",
            "source": "harness",
            "phase": "replay",
            "status": "ok",
            "events": [_event_envelope(event) for event in page.events],
            "after_seq": after_seq,
            "next_seq": page.next_seq,
            "retained_from": page.retained_from,
            "has_more": len(page.events) >= min(max(1, limit), MAX_API_EVENTS),
            "gap": gap,
            "truncated": page.truncated,
            "evidence": {
                "source_kind": "durable_harness_replay",
                "adapter": "pulse_harness.core",
                "verified": True,
                "live_gate": "PROVIDER_FREE_CORE",
            },
            "provenance": {
                "source_kind": "provider_neutral_harness",
                "adapter": "pulse_harness.core",
                "verified": True,
                "capabilities": ["durable_transcript", "bounded_replay"],
                "live_gate": "PROVIDER_FREE_CORE",
            },
        }

    def _scope_for_action(self, session_scope: HarnessScope, action_request_id: str) -> HarnessScope:
        return self.store.action_scope_for_session(session_scope, action_request_id)

    def _turn_ids(self, session_scope: HarnessScope) -> list[str]:
        """Return a bounded newest turn catalog from durable turn rows."""

        turns = self.store.turns_for_session(session_scope, limit=MAX_API_TURNS)
        return [turn.scope.turn_id for turn in turns if turn.scope.turn_id]

    @staticmethod
    def _round_wire(round_record: ModelRoundRecord) -> dict[str, Any]:
        return {
            "round_id": round_record.round_id,
            "scope": _scope_wire(round_record.scope),
            "round_index": round_record.round_index,
            "request_cursor": round_record.request_cursor,
            "terminal_cursor": round_record.terminal_cursor,
            "request_digest": round_record.request_digest,
            "response_id": round_record.response_id,
            "provider_response_id": round_record.provider_response_id,
            "status": round_record.status.value,
            "delta_count": round_record.delta_count,
            "done_count": round_record.done_count,
            "response_digest": round_record.response_digest,
            "assistant_item_id": round_record.assistant_item_id,
            "tool_call_count": round_record.tool_call_count,
            "provider_usage": (
                _json_safe(dict(round_record.provider_usage))
                if round_record.provider_usage is not None
                else None
            ),
            "provider_usage_digest": round_record.provider_usage_digest,
            "usage_evidence_class": round_record.usage_evidence_class,
            "provider_evidence": _json_safe(dict(round_record.provider_evidence)),
            "provider_evidence_digest": round_record.provider_evidence_digest,
            "created_at": round_record.created_at,
            "updated_at": round_record.updated_at,
        }

    def _turn_projection(
        self,
        session_scope: HarnessScope,
        turn_id: str,
        events: tuple[HarnessEvent, ...],
    ) -> dict[str, Any]:
        scope = session_scope.for_turn(turn_id)
        turn = self.store.get_turn(scope)
        actions = self.store.actions_for_turn(scope, limit=MAX_API_ACTIONS)
        action_count = self.store.count_actions_for_turn(scope)
        rounds = self.store.model_rounds_for_turn(scope, limit=MAX_API_ROUNDS)
        action_ids = {action.action_request_id for action in actions}
        uncertain_rounds = [
            round_record
            for round_record in rounds
            if round_record.status is ModelRoundStatus.UNCERTAIN
        ]
        turn_events = tuple(event for event in events if event.scope.turn_id == turn_id)
        approvals: list[dict[str, Any]] = []
        # Approval state is durable under ``actions -> approvals``.  Event
        # rows are an observer stream and may have been retired by retention;
        # reverse-scanning them makes an otherwise valid pending action
        # disappear from the Workbench.
        for action in actions:
            approval = self.store.get_approval_for_action(scope, action.action_request_id)
            if approval is not None:
                approvals.append(_record_wire(approval))
        operations: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        for action in actions:
            if action.operation_id:
                try:
                    operations.append(_record_wire(self.store.get_operation(scope, action.operation_id)))
                except HarnessError:
                    operations.append({
                        "operation_id": action.operation_id,
                        "action_request_id": action.action_request_id,
                        "status": "recovery_required",
                    })
            result = action.result if isinstance(action.result, Mapping) else {}
            output = result.get("output", result) if isinstance(result, Mapping) else {}
            if isinstance(output, Mapping):
                change_id = output.get("change_id")
                if isinstance(change_id, str) and change_id:
                    changes.append(
                        {
                            "change_id": change_id,
                            "scope": _scope_wire(action.scope),
                            "action_request_id": action.action_request_id,
                            "operation_id": output.get("operation_id") or action.operation_id,
                            "path": output.get("path"),
                            "before_digest": output.get("before_digest"),
                            "after_digest": output.get("after_digest"),
                            "diff": output.get("diff", ""),
                            "diff_digest": output.get("diff_digest"),
                            "status": output.get("status", action.status.value),
                            "redacted": bool(output.get("diff_redacted", False)),
                            "truncated": bool(output.get("diff_truncated", False)),
                        }
                    )
                checkpoint_id = output.get("checkpoint_id")
                if isinstance(checkpoint_id, str) and checkpoint_id:
                    checkpoints.append(
                        {
                            "checkpoint_id": checkpoint_id,
                            "scope": _scope_wire(action.scope),
                            "action_request_id": action.action_request_id,
                            "operation_id": output.get("operation_id") or action.operation_id,
                            "path": output.get("path"),
                            "content_digest": output.get("before_digest"),
                            "post_image_digest": output.get("after_digest"),
                            "status": output.get("status", action.status.value),
                            "restorable": action.status.value == "succeeded",
                        }
                    )
        return {
            "turn_id": turn_id,
            "scope": _scope_wire(scope),
            "status": turn.status.value,
            "revision": turn.revision,
            "prompt": turn.prompt,
            "created_at": turn.created_at,
            "updated_at": turn.updated_at,
            "terminal": turn.status in {
                TurnStatus.SUCCEEDED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            },
            "events": [_event_envelope(event) for event in turn_events[-MAX_API_EVENTS:]],
            "actions": [_action_wire(action) for action in actions],
            "approvals": approvals[:MAX_API_ACTIONS],
            "operations": [_json_safe(item) for item in operations[:MAX_API_ACTIONS]],
            "changes": [_json_safe(item) for item in changes[:MAX_API_CHANGES]],
            "checkpoints": [_json_safe(item) for item in checkpoints[:MAX_API_CHANGES]],
            "model_rounds": [
                self._round_wire(round_record)
                for round_record in rounds[-MAX_API_ROUNDS:]
            ],
            "recovery": {
                "required": bool(uncertain_rounds),
                "records": [
                    {
                        "round_id": round_record.round_id,
                        "status": round_record.status.value,
                        "error_code": "model_stream_cleanup_uncertain",
                    }
                    for round_record in uncertain_rounds[-MAX_API_RECOVERIES:]
                ],
                "automatic_replay": False,
            },
            "evidence": _evidence_from_round(rounds[-1] if rounds else None),
            "action_count": action_count,
            "event_count": self.store.count_events_for_turn(scope),
            "action_request_ids": sorted(action_ids),
        }

    def _workspace_projection(self, session_scope: HarnessScope) -> dict[str, Any]:
        runtime = self._runtimes.get(session_scope.session_id)
        provider = runtime.provider if runtime is not None else None
        if provider is None:
            return {
                "adapter": "pulse_harness.workspace.v1",
                "read_file": False,
                "mutate_file": False,
                "diff": False,
                "checkpoint": False,
                "live_gate": "BLOCKED",
            }
        preflight = getattr(provider, "preflight", None)
        try:
            values = dict(preflight()) if callable(preflight) else {}
        except Exception as exc:  # pragma: no cover - adapter boundary
            _logger.warning("workspace preflight failed: %s", exc)
            values = {}
        values.setdefault("adapter", "pulse_harness.workspace.v1")
        values["live_gate"] = "PROVIDER_FREE_STRUCTURED_WORKSPACE"
        values["evidence_class"] = "LIVE_WORKSPACE_CHECKPOINTED"
        return _json_safe(values)

    def session_projection(
        self,
        session_scope: HarnessScope,
        *,
        after_seq: int | None = None,
        limit: int = MAX_API_EVENTS,
    ) -> dict[str, Any]:
        """Build a bounded projection with an explicit replay cursor.

        ``after_seq=None`` means "the newest observer window" for the
        Workbench.  A caller that needs the beginning of the retained stream
        passes ``after_seq=0`` and follows ``replay.next_seq``.  Durable turn,
        action and model-round indexes supply the rest of the projection; no
        transcript or observer event table is fetched wholesale.
        """

        if limit < 1 or limit > MAX_API_EVENTS:
            raise ValueError("projection limit is outside the bounded range")
        session = self.store.get_session(session_scope)
        page_limit = min(limit, self.store.max_events_per_session)
        requested_after = (
            max(0, session.cursor - page_limit)
            if after_seq is None
            else after_seq
        )
        if requested_after < 0:
            raise InvalidReplayCursor("projection cursor must be non-negative")
        page = self.store.replay(
            session_scope,
            after_seq=requested_after,
            limit=page_limit,
        )
        events = page.events
        turn_ids = self._turn_ids(session_scope)
        turns: list[dict[str, Any]] = []
        for turn_id in turn_ids:
            try:
                turns.append(self._turn_projection(session_scope, turn_id, events))
            except ScopeConflict:
                raise
            except HarnessError as exc:
                turns.append({
                    "turn_id": turn_id,
                    "scope": _scope_wire(session_scope.for_turn(turn_id)),
                    "status": "recovery_required",
                    "error_code": exc.code,
                    "events": [],
                    "actions": [],
                    "approvals": [],
                    "operations": [],
                    "model_rounds": [],
                    "evidence": _evidence_from_round(None),
                })
        rounds = self.store.model_rounds_for_session(
            session_scope,
            limit=MAX_API_ROUNDS,
        )
        uncertain = self.store.actions_for_session(
            session_scope,
            status=ActionStatus.UNCERTAIN,
            limit=MAX_API_RECOVERIES,
        )
        uncertain_rounds = [
            round_record
            for round_record in rounds
            if round_record.status is ModelRoundStatus.UNCERTAIN
        ]
        pending = self.store.pending_approval_count_for_session(session_scope)
        gap = None
        if page.gap:
            gap = {
                "missing_from": requested_after + 1,
                "missing_to": max(requested_after + 1, page.retained_from - 1),
                "reason": "retention_window",
            }
        return {
            "protocol_version": KERNEL_PROTOCOL,
            "session": {
                "session_id": session.scope.session_id,
                "scope": _scope_wire(session.scope),
                "status": session.status.value,
                "revision": session.revision,
                "cursor": session.cursor,
                "transcript_cursor": session.transcript_cursor,
                "transcript_head_digest": session.transcript_head_digest,
                "retained_from": session.retained_from,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
            "turns": turns,
            "events": [_event_envelope(event) for event in events],
            "replay": {
                "after_seq": requested_after,
                "next_seq": page.next_seq,
                "retained_from": page.retained_from,
                "has_more": page.next_seq < session.cursor,
                "gap": gap,
                "truncated": page.truncated,
            },
            "workspace": self._workspace_projection(session_scope),
            "capacity": {
                "event_rows": len(events),
                "event_limit": self.store.max_events_per_session,
                "payload_limit_bytes": self.store.max_payload_bytes,
                "active_session_runners": len(self._runtimes),
                "turn_count": len(turn_ids),
                "pending_approvals": pending,
                "uncertain_actions": self.store.count_actions_for_session(
                    session_scope,
                    status=ActionStatus.UNCERTAIN,
                ),
                "uncertain_model_rounds": self.store.count_model_rounds_for_session(
                    session_scope,
                    status=ModelRoundStatus.UNCERTAIN,
                ),
                "bounded": True,
            },
            "recovery": {
                "required": bool(uncertain or uncertain_rounds),
                "records": [
                    {
                        "action_request_id": action.action_request_id,
                        "status": action.status.value,
                        "error_code": action.error_code,
                    }
                    for action in uncertain
                ] + [
                    {
                        "round_id": round_record.round_id,
                        "status": round_record.status.value,
                        "error_code": "model_stream_cleanup_uncertain",
                    }
                    for round_record in uncertain_rounds[:MAX_API_RECOVERIES]
                ][:MAX_API_RECOVERIES],
                "automatic_replay": False,
            },
            "evidence": _evidence_from_round(rounds[-1] if rounds else None),
        }


def _error_response(exc: Exception, *, default_status: int = 409) -> JSONResponse:
    if isinstance(exc, InvalidReplayCursor):
        status = 400
    elif isinstance(exc, (LeaseFenced, ScopeConflict)):
        status = 409
    elif isinstance(exc, StoreUnavailable):
        status = 503
    elif isinstance(exc, ApprovalConflict):
        status = 409
    elif isinstance(exc, HarnessError):
        status = 400
    else:
        _logger.warning("kernel API failure: %s", exc)
        status = 503
    code = getattr(exc, "code", "harness_api_error")
    body = {
        "protocol_version": KERNEL_PROTOCOL,
        "accepted": False,
        "error": code,
        "error_code": code,
        "detail": str(exc),
        "evidence": {
            "source_kind": "provider_neutral_harness",
            "adapter": "pulse_harness.core",
            "verified": False,
            "live_gate": "BLOCKED" if code == "provider_unconfigured" else "UNVERIFIED",
        },
    }
    if code == "provider_unconfigured":
        status = 503
    return JSONResponse(status_code=status if status else default_status, content=body)


def _body_scope(body: Mapping[str, Any], *, session_id: str | None = None, turn_id: str = "") -> HarnessScope:
    if not isinstance(body, Mapping):
        raise HarnessKernelApiError("request body must be a JSON object", code="invalid_body")
    required = ("world_id", "engram_id", "owner_id", "epoch")
    missing = [key for key in required if key not in body]
    if missing:
        raise HarnessKernelApiError(
            "scope is missing required fields: " + ", ".join(missing),
            code="scope_required",
        )
    candidate_session = body.get("session_id") or session_id or f"session-{uuid4()}"
    if session_id is not None and candidate_session != session_id:
        raise HarnessKernelApiError("session_id does not match the route", code="scope_conflict")
    epoch = body["epoch"]
    if isinstance(epoch, str):
        try:
            epoch = int(epoch)
        except ValueError as exc:
            raise HarnessKernelApiError("epoch must be an integer", code="scope_invalid") from exc
    try:
        return HarnessScope(
            world_id=body["world_id"],
            engram_id=body["engram_id"],
            context_id=body.get("context_id", ""),
            session_id=candidate_session,
            turn_id=turn_id or body.get("turn_id", ""),
            epoch=epoch,
            owner_id=body["owner_id"],
            capability_digest=body.get("capability_digest", ""),
        )
    except (TypeError, ValueError) as exc:
        raise HarnessKernelApiError("scope fields are invalid", code="scope_invalid") from exc


def create_harness_kernel_router(
    kernel: HarnessKernelService,
    *,
    prefix: str = "/v1/harness",
    poll_interval: float = DEFAULT_STREAM_POLL_SEC,
    stream_seconds: float = MAX_STREAM_SECONDS,
) -> APIRouter:
    """Mount the independent durable session/control/replay surface."""

    router = APIRouter(prefix=prefix, tags=["harness-kernel"])
    sleep_interval = max(0.05, float(poll_interval))
    connection_seconds = max(0.1, min(float(stream_seconds), MAX_STREAM_SECONDS))

    @router.post("/sessions", status_code=201)
    async def create_session(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            scope = _body_scope(body)
            return JSONResponse(status_code=201, content=kernel.create(scope))
        except Exception as exc:  # noqa: BLE001 - bounded API boundary
            return _error_response(exc)

    @router.post("/sessions/{session_id}/wake")
    async def wake_session(session_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            scope = _body_scope(body, session_id=session_id)
            return JSONResponse(content=kernel.wake(scope))
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    @router.get("/sessions/{session_id}")
    def inspect_session(session_id: str, request: Request) -> JSONResponse:
        # This synchronous store projection must run in FastAPI's worker
        # pool, not on the ASGI loop shared by messages and material reads.
        # Scope is intentionally supplied as query data only for read access;
        # the caller must still prove the full durable scope.  This avoids an
        # unscoped session-id lookup becoming an authority upgrade.
        try:
            query = request.query_params
            scope = _body_scope(
                {
                    "world_id": query.get("world_id"),
                    "engram_id": query.get("engram_id"),
                    "context_id": query.get("context_id", ""),
                    "owner_id": query.get("owner_id"),
                    "epoch": query.get("epoch"),
                    "capability_digest": query.get("capability_digest", ""),
                    "session_id": session_id,
                },
                session_id=session_id,
            )
            after_raw = query.get("after_seq")
            after_seq = int(after_raw) if after_raw is not None else None
            limit = int(query.get("limit", str(MAX_API_EVENTS)))
            if (
                (after_seq is not None and after_seq < 0)
                or limit < 1
                or limit > MAX_API_EVENTS
            ):
                raise HarnessKernelApiError(
                    "projection cursor or limit is invalid",
                    code="replay_query_invalid",
                )
            return JSONResponse(
                content=kernel.session_projection(
                    scope.session_scope(),
                    after_seq=after_seq,
                    limit=limit,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    @router.post("/sessions/{session_id}/turns")
    async def run_turn(session_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if not isinstance(body, Mapping):
                raise HarnessKernelApiError("request body must be an object", code="invalid_body")
            turn_id = body.get("turn_id") or f"turn-{uuid4()}"
            scope = _body_scope(body, session_id=session_id, turn_id=turn_id)
            prompt = body.get("prompt")
            if prompt is not None and not isinstance(prompt, str):
                raise HarnessKernelApiError("prompt must be text", code="prompt_invalid")
            # TurnRunner/provider calls are synchronous by core design. Keep
            # them off the ASGI loop so SSE, inspection and control requests
            # remain serviceable while a model turn is in flight.
            result = await asyncio.to_thread(kernel.run_turn, scope, prompt=prompt)
            outcome = result.get("outcome", {})
            status = outcome.get("status")
            if status == TurnStatus.BLOCKED.value and outcome.get("error_code") == "provider_unconfigured":
                return JSONResponse(status_code=503, content=result)
            return JSONResponse(content=result)
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    @router.get("/sessions/{session_id}/events")
    def replay_events(session_id: str, request: Request) -> JSONResponse:
        # Like inspection, replay may wait for the durable store lock.
        try:
            query = request.query_params
            scope = _body_scope(
                {
                    "world_id": query.get("world_id"),
                    "engram_id": query.get("engram_id"),
                    "context_id": query.get("context_id", ""),
                    "owner_id": query.get("owner_id"),
                    "epoch": query.get("epoch"),
                    "capability_digest": query.get("capability_digest", ""),
                    "session_id": session_id,
                },
                session_id=session_id,
            )
            after_seq = int(query.get("after_seq", "0"))
            limit = int(query.get("limit", str(MAX_API_EVENTS)))
            if after_seq < 0 or limit < 1 or limit > MAX_API_EVENTS:
                raise HarnessKernelApiError("replay cursor or limit is invalid", code="replay_query_invalid")
            return JSONResponse(content=kernel.replay(scope, after_seq=after_seq, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    @router.get("/sessions/{session_id}/stream")
    async def stream_events(session_id: str, request: Request) -> StreamingResponse:
        scope: HarnessScope | None = None
        try:
            query = request.query_params
            scope = _body_scope(
                {
                    "world_id": query.get("world_id"),
                    "engram_id": query.get("engram_id"),
                    "context_id": query.get("context_id", ""),
                    "owner_id": query.get("owner_id"),
                    "epoch": query.get("epoch"),
                    "capability_digest": query.get("capability_digest", ""),
                    "session_id": session_id,
                },
                session_id=session_id,
            )
            once = query.get("once", "0") in {"1", "true", "yes"}
            cursor_raw = request.headers.get("last-event-id") or query.get("after_seq", "0")
            cursor = int(cursor_raw)
            if cursor < 0:
                raise HarnessKernelApiError("stream cursor is invalid", code="replay_query_invalid")
        except Exception as exc:  # noqa: BLE001
            error_body = {
                "protocol_version": KERNEL_PROTOCOL,
                "envelope_version": "pulse-harness.error.v1",
                "scope": _scope_wire(scope.session_scope()) if scope is not None else None,
                "session_id": session_id,
                "turn_id": "",
                "item_id": None,
                "event_id": None,
                "seq": 0,
                "delta_seq": 0,
                "revision": 0,
                "type": "replay.error",
                "source": "harness",
                "phase": "replay",
                "status": "error",
                "error": getattr(exc, "code", "replay_unavailable"),
                "error_code": getattr(exc, "code", "replay_unavailable"),
                "detail": str(exc),
                "provenance": {
                    "source_kind": "provider_neutral_harness",
                    "adapter": "pulse_harness.core",
                    "verified": False,
                    "live_gate": "UNVERIFIED",
                },
            }
            return StreamingResponse(
                iter([f"event: harness_error\ndata: {json.dumps(error_body, ensure_ascii=False)}\nretry: 1000\n\n"]),
                media_type="text/event-stream",
                status_code=400,
            )

        async def stream():
            nonlocal cursor
            deadline = time.monotonic() + connection_seconds
            first = True
            while True:
                if await request.is_disconnected():
                    return
                try:
                    replay = await asyncio.to_thread(
                        kernel.replay,
                        scope,
                        after_seq=cursor,
                        limit=MAX_API_EVENTS,
                    )
                except Exception as exc:  # noqa: BLE001
                    payload = {
                        "protocol_version": KERNEL_PROTOCOL,
                        "envelope_version": "pulse-harness.error.v1",
                        "scope": _scope_wire(scope.session_scope()),
                        "session_id": scope.session_id,
                        "turn_id": "",
                        "item_id": None,
                        "event_id": None,
                        "seq": cursor,
                        "delta_seq": 0,
                        "revision": 0,
                        "type": "replay.error",
                        "source": "harness",
                        "phase": "replay",
                        "status": "error",
                        "error": getattr(exc, "code", "replay_unavailable"),
                        "detail": str(exc),
                        "provenance": {
                            "source_kind": "provider_neutral_harness",
                            "adapter": "pulse_harness.core",
                            "verified": False,
                            "live_gate": "UNVERIFIED",
                        },
                    }
                    yield _sse("harness_error", payload)
                    return
                if first:
                    yield _sse("harness_replay", replay)
                    first = False
                if replay.get("gap") is not None:
                    yield _sse("harness_gap", replay["gap"])
                events = replay.get("events", [])
                for event in events:
                    # A writable ASGI transport can send without suspending.
                    # Check between frames so a large replay cannot starve the
                    # loop's pending client-disconnect notification.
                    if await request.is_disconnected():
                        return
                    cursor = int(event["seq"])
                    yield _sse("harness_event", event, str(cursor))
                if once:
                    return
                if time.monotonic() >= deadline:
                    return
                if not events:
                    yield ": keepalive\n\n"
                await asyncio.sleep(sleep_interval)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @router.post("/sessions/{session_id}/actions/{action_request_id}/approval")
    async def resolve_approval(session_id: str, action_request_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if not isinstance(body, Mapping):
                raise HarnessKernelApiError("request body must be an object", code="invalid_body")
            scope = _body_scope(body, session_id=session_id)
            decision_raw = body.get("decision")
            try:
                decision = ApprovalDecision(decision_raw)
            except ValueError as exc:
                raise HarnessKernelApiError(
                    "decision must be allow_once, deny, or cancel", code="decision_invalid"
                ) from exc
            result = await asyncio.to_thread(
                kernel.resolve_approval, scope, action_request_id, decision
            )
            return JSONResponse(content=result)
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    @router.post("/sessions/{session_id}/control")
    async def control(session_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if not isinstance(body, Mapping):
                raise HarnessKernelApiError("request body must be an object", code="invalid_body")
            turn_id = body.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id.strip():
                raise HarnessKernelApiError("control requires turn_id", code="turn_required")
            scope = _body_scope(body, session_id=session_id, turn_id=turn_id)
            operation = body.get("operation")
            if not isinstance(operation, str):
                raise HarnessKernelApiError("control requires operation", code="control_invalid")
            result = await asyncio.to_thread(
                kernel.control,
                scope,
                operation=operation,
                prompt=body.get("prompt"),
                reason=body.get("reason", "user_control"),
            )
            return JSONResponse(content=result)
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    @router.get("/sessions/{session_id}/recovery")
    async def recovery(session_id: str, request: Request) -> JSONResponse:
        try:
            query = request.query_params
            scope = _body_scope(
                {
                    "world_id": query.get("world_id"),
                    "engram_id": query.get("engram_id"),
                    "context_id": query.get("context_id", ""),
                    "owner_id": query.get("owner_id"),
                    "epoch": query.get("epoch"),
                    "capability_digest": query.get("capability_digest", ""),
                    "session_id": session_id,
                },
                session_id=session_id,
            )
            # Recovery observation validates one durable session only.  Keep
            # the read off the ASGI loop just like turn/control execution;
            # startup owns the one mutating recovery pass.
            result = await asyncio.to_thread(kernel.recover, scope.session_scope())
            return JSONResponse(content=result)
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    @router.get("/sessions/{session_id}/capabilities")
    async def capabilities(session_id: str, request: Request) -> JSONResponse:
        try:
            query = request.query_params
            scope = _body_scope(
                {
                    "world_id": query.get("world_id"),
                    "engram_id": query.get("engram_id"),
                    "context_id": query.get("context_id", ""),
                    "owner_id": query.get("owner_id"),
                    "epoch": query.get("epoch"),
                    "capability_digest": query.get("capability_digest", ""),
                    "session_id": session_id,
                },
                session_id=session_id,
            )
            projection = kernel.session_projection(scope.session_scope())
            return JSONResponse(
                content={
                    "protocol_version": KERNEL_PROTOCOL,
                    "session_id": session_id,
                    "workspace": projection["workspace"],
                    "evidence": projection["evidence"],
                    "capacity": projection["capacity"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)

    return router


def _sse(event_name: str, payload: Mapping[str, Any], event_id: str | None = None) -> str:
    identity = f"id: {event_id}\n" if event_id is not None else ""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{identity}event: {event_name}\ndata: {data}\nretry: 1000\n\n"


__all__ = ["HarnessKernelService", "create_harness_kernel_router"]
