"""Page-scoped observation and controls over the existing native Kernel.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This is a product adapter, not another runner or state store. Browser requests
name a page and an exact durable turn/action. Session and ownership fields are
always derived from the World. In particular, stopping a page never calls the
subject-wide abort operation.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import threading
from typing import Any

from pulse_harness.core import ApprovalDecision, HarnessScope, ModelRoundStatus, TurnStatus
from pulse_harness.core.contracts import ActionRecord, ActionStatus, ApprovalStatus
from pulse_harness.core.errors import HarnessError, ScopeConflict, StoreUnavailable
from pulse_harness.core.replay import redact_value, stored_payload_flags
from pulse_system.core.causality import CausalTransitionError
from pulse_system.service import native_replay


_TERMINAL = {TurnStatus.SUCCEEDED, TurnStatus.FAILED, TurnStatus.CANCELLED}
_MAX_ACTIONS = 32
_MAX_ARGUMENT_BYTES = 32 * 1024


class ExecutionError(Exception):
    """A small, stable error envelope for the page API."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class PageExecution:
    """Read the original Kernel and route exact page-owned controls to it."""

    def __init__(self, runtime):
        self.runtime = runtime
        # Only duplicate controls share a lock. Approval continuation can take
        # time; it must never block stopping that turn or controlling another
        # page. Entries exist only while a caller holds or waits for them.
        self._locks_lock = threading.RLock()
        self._control_locks: dict[tuple[str, str], tuple[Any, int]] = {}

    @contextmanager
    def _serialize(self, kind: str, identity: str):
        key = (kind, identity)
        with self._locks_lock:
            lock, count = self._control_locks.get(key, (threading.RLock(), 0))
            self._control_locks[key] = lock, count + 1
        try:
            with lock:
                yield
        finally:
            with self._locks_lock:
                lock, count = self._control_locks[key]
                if count == 1:
                    del self._control_locks[key]
                else:
                    self._control_locks[key] = lock, count - 1

    def _scope(self, page_id: str) -> HarnessScope:
        subject_id = self.runtime.continuity_engram_id
        context_id = ""
        if page_id != "home":
            front = self.runtime.world.get_task_front(page_id)
            if front is None or front.focal_engram_id != subject_id:
                raise ExecutionError("page_not_found", "This page does not exist.", 404)
            context_id = self.runtime._native_kernel_context_for_center(front.center_id)
        return self.runtime.harness.scope_for(subject_id, context_id)

    @property
    def _kernel(self):
        kernel = self.runtime.kernel_service
        if kernel is None:
            raise ExecutionError("execution_unavailable", "Page execution is unavailable.", 503)
        return kernel

    def _history_scopes(self, scope: HarnessScope) -> tuple[HarnessScope, ...]:
        """Verified same-context ancestry for observation, never for controls."""
        try:
            lineage = self.runtime.causal_ledger.committed_history_lineage(
                scope.world_id, scope.engram_id,
            )
        except CausalTransitionError as error:
            raise ExecutionError(
                "execution_history_unavailable",
                "Historical execution evidence could not be verified. Try again.", 503,
            ) from error
        # The Harness's broader model-history reader can also include delegated
        # team material. That is not authority to expose another page's effects.
        return tuple(self.runtime.harness.scope_for(subject, scope.context_id) for subject in lineage)

    def _historical_actions(self, scopes: tuple[HarnessScope, ...], *,
                            status: ActionStatus, limit: int) -> list[ActionRecord]:
        """Newest generation first, with one bounded scan across all scopes."""
        actions = []
        for scope in scopes:
            try:
                rows = self._kernel.store.actions_for_session(
                    scope, status=status, limit=limit - len(actions),
                )
            except ScopeConflict as error:
                if str(error).startswith("unknown session:"):
                    continue
                raise
            actions.extend(reversed(rows))
            if len(actions) >= limit:
                break
        return actions

    def _uncertainty(self, scope: HarnessScope) -> dict:
        scopes = self._history_scopes(scope)
        records = self._historical_actions(scopes, status=ActionStatus.UNCERTAIN, limit=_MAX_ACTIONS + 1)
        historical = bool(records)
        if not historical:
            for previous in scopes:
                try:
                    historical = self._kernel.store.count_model_rounds_for_session(
                        previous, status=ModelRoundStatus.UNCERTAIN,
                    ) > 0
                except ScopeConflict as error:
                    if str(error).startswith("unknown session:"):
                        continue
                    raise
                if historical:
                    break
        actions = []
        for action in records[:_MAX_ACTIONS]:
            arguments, redacted, truncated = self._arguments(action.arguments)
            actions.append({
                "actionId": action.action_request_id, "turnId": action.scope.turn_id,
                "toolName": action.tool_name, "arguments": arguments,
                "argumentsTruncated": truncated, "argumentsRedacted": redacted,
                "status": action.status.value,
            })
        return {"historicalUncertainty": historical, "uncertainActions": actions,
                "uncertainActionsTruncated": len(records) > _MAX_ACTIONS}

    @staticmethod
    def _translate(error: HarnessError) -> ExecutionError:
        if isinstance(error, StoreUnavailable):
            return ExecutionError("execution_unavailable", "Execution state could not be read. Try again.", 503)
        if isinstance(error, ScopeConflict):
            return ExecutionError("execution_scope_conflict", "This execution does not belong to this page or is no longer active.")
        return ExecutionError(error.code, "The execution changed. Refresh this page before continuing.")

    def _attached(self, scope: HarnessScope) -> bool:
        harness = self.runtime.harness
        # This read is advisory. The native approval guard repeats the exact
        # ownership check inside its own lock before any effect is permitted.
        with harness._lock:
            return harness._active.get((scope.engram_id, scope.context_id)) == scope

    @staticmethod
    def _arguments(arguments: Any) -> tuple[dict, bool, bool]:
        clean, redacted = redact_value(arguments)
        clean = dict(clean) if isinstance(clean, dict) else {"value": clean}
        stored_redacted, stored_truncated = stored_payload_flags(clean)
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        truncated = stored_truncated or len(encoded.encode("utf-8")) > _MAX_ARGUMENT_BYTES
        if len(encoded.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
            clean = {"preview": encoded[:8192], "_truncated": True}
        return clean, redacted or stored_redacted, truncated

    def snapshot(self, page_id: str) -> dict:
        """Observe without creating a session, waking a runner or making a call."""
        scope = self._scope(page_id)
        kernel = self._kernel
        try:
            history = self._uncertainty(scope)
            try:
                session = kernel.store.get_session(scope)
            except ScopeConflict as error:
                if str(error).startswith("unknown session:"):
                    return {
                        "pageId": page_id, "sessionId": None, "turnId": None,
                        "status": "idle", "canCancel": False, "pendingActions": [],
                        "pendingActionsTruncated": False, "recoveryRequired": False,
                        "replay": None,
                        **history,
                        "updatedAt": None,
                    }
                raise
            turns = kernel.store.turns_for_session(scope, limit=1)
            turn = turns[-1] if turns else None
            pending = kernel.store.pending_actions(scope, limit=_MAX_ACTIONS + 1)
            pending_truncated = len(pending) > _MAX_ACTIONS
            pending = pending[-_MAX_ACTIONS:]
            actions = []
            for action in pending:
                approval = kernel.store.get_approval_for_action(action.scope, action.action_request_id)
                arguments, redacted, truncated = self._arguments(action.arguments)
                attached = self._attached(action.scope)
                same_turn = turn is not None and action.scope == turn.scope
                expires_at = approval.expires_at if approval else None
                expired = bool(expires_at and datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc))
                resolvable = bool(
                    approval is not None and approval.status is ApprovalStatus.PENDING
                    and action.status is ActionStatus.APPROVAL_PENDING
                    and same_turn and turn.status is TurnStatus.WAITING_APPROVAL
                    and attached and not expired and not truncated and not redacted
                )
                reason = None
                if not resolvable:
                    reason = (
                        "Action details are incomplete. Stop this turn and request a new action."
                        if truncated or redacted else
                        "This approval has expired. Stop this turn and request a new action."
                        if expired else
                        "This turn is no longer attached to its original execution. Stop it before starting again."
                        if not attached else
                        "This action changed. Refresh before deciding."
                    )
                actions.append({
                    "actionId": action.action_request_id, "turnId": action.scope.turn_id,
                    "toolName": action.tool_name, "arguments": arguments,
                    "argumentsTruncated": truncated, "argumentsRedacted": redacted,
                    "approvalId": approval.approval_id if approval else None,
                    "expiresAt": expires_at, "canResolve": resolvable,
                    "blockedReason": reason,
                })
            status = "idle"
            if turn is not None:
                status = {
                    TurnStatus.SUCCEEDED: "idle", TurnStatus.CANCELLED: "stopped",
                    TurnStatus.FAILED: "error", TurnStatus.BLOCKED: "needs_attention",
                    TurnStatus.WAITING_APPROVAL: "needs_attention",
                }.get(turn.status, "working")
            # Old uncertainty remains evidence, not a permanent demand to stop
            # later successful work. Only the current unresolved turn can need
            # an actionable recovery decision in this page's controls.
            recovery = bool(
                turn and turn.status not in _TERMINAL
                and (turn.status is TurnStatus.BLOCKED or not self._attached(turn.scope))
            )
            entry = native_replay.mode(kernel.store, turn.scope) if turn and turn.status not in _TERMINAL else None
            return {
                "pageId": page_id, "sessionId": session.scope.session_id,
                "turnId": turn.scope.turn_id if turn else None, "status": status,
                "canCancel": bool(turn and turn.status not in _TERMINAL),
                "pendingActions": actions, "pendingActionsTruncated": pending_truncated,
                "recoveryRequired": recovery,
                "replay": {"sourceCount": len(entry["message_ids"])} if entry else None,
                **history,
                "updatedAt": turn.updated_at if turn else session.updated_at,
            }
        except HarnessError as error:
            raise self._translate(error) from error

    def cancel(self, page_id: str, turn_id: str) -> dict:
        """Cancel exactly the displayed turn; a stale ID cannot retarget a page."""
        with self._serialize("cancel", turn_id):
            scope = self._scope(page_id).for_turn(turn_id)
            kernel = self._kernel
            try:
                turn = kernel.store.get_turn(scope)
                if turn.status is TurnStatus.CANCELLED:
                    return {
                        "pageId": page_id, "turnId": turn_id, "accepted": True,
                        "repeated": True, "execution": self.snapshot(page_id),
                    }
                if turn.status in _TERMINAL:
                    raise ExecutionError("turn_already_finished", "This turn has already finished. Refresh to see the current work.")
                kernel.control(scope, operation="cancel", reason="page_cancel")
                cancelled = kernel.store.get_turn(scope).status is TurnStatus.CANCELLED
                return {
                    "pageId": page_id, "turnId": turn_id, "accepted": cancelled,
                    "repeated": False, "execution": self.snapshot(page_id),
                }
            except HarnessError as error:
                raise self._translate(error) from error

    def _approval_receipt(
        self, page_id, scope, action, decision, *, repeated=False, continuation_error=None,
    ) -> dict:
        return {
            "pageId": page_id, "turnId": scope.turn_id,
            "actionId": action.action_request_id, "decision": decision,
            "accepted": True, "repeated": repeated,
            "actionStatus": action.status.value,
            "actionCompleted": action.status is ActionStatus.SUCCEEDED,
            "continuationError": continuation_error,
            "execution": self.snapshot(page_id),
        }

    @staticmethod
    def _continuation_error(error: HarnessError, action) -> dict:
        # Never project exception text or tool output here: either can contain
        # the sensitive material which prevented the continuation in the first
        # place. A committed effect and a later response are separate facts.
        if action.status is ActionStatus.SUCCEEDED:
            message = "The action completed, but the follow-up response could not finish. Do not repeat the action."
        elif action.status is ActionStatus.UNCERTAIN:
            message = "Your decision was recorded, but the action's effect could not be confirmed. Check its outcome before requesting it again."
        else:
            message = "Your decision was recorded. The action did not succeed, and the follow-up response could not finish."
        return {"code": error.code, "message": message}

    def resolve_approval(self, page_id: str, action_id: str, decision: str) -> dict:
        """Resolve one exact pending action using the native approval guard.

        A duplicate decision is observation only. It never calls resume again,
        even if a newer turn is running by the time the browser retries.
        """
        try:
            choice = ApprovalDecision(decision)
        except (ValueError, TypeError) as error:
            raise ExecutionError("approval_decision_invalid", "Choose Allow once, Deny or Cancel.", 400) from error
        with self._serialize("approval", action_id):
            session_scope = self._scope(page_id)
            kernel = self._kernel
            try:
                scope = kernel.store.action_scope_for_session(session_scope, action_id)
                action = kernel.store.get_action(scope, action_id)
                approval = kernel.store.get_approval_for_action(scope, action_id)
                if approval is None:
                    raise ExecutionError("approval_not_found", "This action has no pending approval.")
                if approval.status is not ApprovalStatus.PENDING:
                    if approval.decision is choice:
                        return self._approval_receipt(page_id, scope, action, decision, repeated=True)
                    raise ExecutionError("approval_already_resolved", "This approval has already been resolved. Refresh the page.")
                turn = kernel.store.get_turn(scope)
                if action.status is not ActionStatus.APPROVAL_PENDING or turn.status is not TurnStatus.WAITING_APPROVAL:
                    raise ExecutionError("approval_not_pending", "This action is no longer waiting for approval.")
                _, redacted, truncated = self._arguments(action.arguments)
                if choice is ApprovalDecision.ALLOW_ONCE and (redacted or truncated):
                    raise ExecutionError("approval_details_incomplete", "The complete action details are unavailable. Stop this turn and request a new action.")
                # Native ownership/expiry checks remain authoritative, including
                # after restart. No stale browser snapshot can grant authority.
                try:
                    kernel.resolve_approval(session_scope, action_id, choice)
                except HarnessError as error:
                    # The action may already have committed before appending
                    # its result or continuing the model fails. Report that
                    # durable effect honestly; never invite a second execution.
                    resolved = kernel.store.get_approval_for_action(scope, action_id)
                    action = kernel.store.get_action(scope, action_id)
                    if resolved is None or resolved.decision is not choice or not action.terminal:
                        raise
                    return self._approval_receipt(
                        page_id, scope, action, decision,
                        continuation_error=self._continuation_error(error, action),
                    )
                resolved = kernel.store.get_approval_for_action(scope, action_id)
                if resolved is None or resolved.decision is not choice:
                    raise ExecutionError("approval_not_resolved", "This approval was not recorded. Refresh before deciding again.")
                action = kernel.store.get_action(scope, action_id)
                return self._approval_receipt(page_id, scope, action, decision)
            except HarnessError as error:
                raise self._translate(error) from error
