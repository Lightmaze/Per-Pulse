"""Pulse-owned model → tool → settle loop.

This module is intentionally independent from Pi RPC/JSONL/extensions and
from PulseWorld lifecycle.  It consumes only the provider-neutral store,
typed model/tool ports, and opaque Harness scope.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from threading import Event, RLock
from typing import Any, Mapping
from uuid import uuid4

from .contracts import (
    ActionClassification,
    ActionRecord,
    ActionStatus,
    HarnessEvent,
    ModelRoundRecord,
    ModelRoundStatus,
    ToolCall,
    ToolDescriptor,
    ToolResult,
    TerminalOutcome,
    TurnOutcome,
    SessionStatus,
    TurnStatus,
)
from .errors import (
    CapabilityDenied,
    EffectUnknown,
    HarnessError,
    IdempotencyConflict,
    InvalidTransition,
    LeaseFenced,
    ProviderCapabilityMissing,
    ProviderProtocolError,
    ProviderUnavailable,
    RedactionRequired,
    RevisionConflict,
    ScopeConflict,
    StoreUnavailable,
)
from .events import EventDraft
from .hooks import HookContext, HookDecision, HookRejected, HookSet, HookStage
from .provider import (
    CancellationRequested,
    CancellationToken,
    ClosableModelStream,
    ModelAdapter,
    ModelDelta,
    ModelDeltaKind,
    ModelRequest,
    ProviderEvidence,
    ProviderUsage,
    ToolProvider,
    validate_model_adapter,
)
from .replay import bound_payload, bound_text, digest_json
from .response_review import (
    RESPONSE_REVIEW_PREFIX,
    ResponseReview,
    ResponseReviewer,
    decode_review_envelope,
    encode_review_envelope,
)
from .session_store import HarnessStore
from .scope import HarnessScope
from .transcript import TranscriptStore
from .turn_advice import TURN_ADVICE_PREFIX, TurnAdvice, TurnAdvisor, decode_advice, encode_advice


@dataclass(frozen=True, slots=True)
class _PersistedToolCall:
    call: ToolCall
    action_request_id: str


class _ModelRoundWriterPermit:
    """TurnRunner-owned process-local permit; Store never constructs it."""

    __slots__ = ("_nonce",)

    def __init__(self) -> None:
        self._nonce = object()

    def __copy__(self) -> "_ModelRoundWriterPermit":
        return type(self)()

    def __deepcopy__(self, memo: dict[int, object]) -> "_ModelRoundWriterPermit":
        del memo
        return type(self)()


@dataclass(slots=True)
class _RunContext:
    model_cancel: Event
    active_round_id: str | None = None
    active_action_id: str | None = None
    active_token: CancellationToken | None = None


def _action_request_id(scope: HarnessScope, call: ToolCall) -> str:
    """Namespace provider call IDs by the full durable Harness scope."""

    return f"{scope.digest}:{call.tool_call_id}"


_MODEL_STREAM_CLEANUP_UNCERTAIN = "model_stream_cleanup_uncertain"


class TurnRunner:
    """The sole owner of the independent Harness turn state machine."""

    def __init__(
        self,
        store: HarnessStore,
        *,
        model_adapter: ModelAdapter | None,
        tool_provider: ToolProvider,
        hooks: HookSet | None = None,
        max_model_rounds: int = 16,
        max_model_deltas: int = 256,
        max_model_stream_bytes: int | None = None,
        response_reviewer: ResponseReviewer | None = None,
        turn_advisor: TurnAdvisor | None = None,
    ) -> None:
        if max_model_rounds < 1:
            raise ValueError("max_model_rounds must be positive")
        if max_model_deltas < 1:
            raise ValueError("max_model_deltas must be positive")
        if max_model_stream_bytes is not None and max_model_stream_bytes < 1:
            raise ValueError("max_model_stream_bytes must be positive")
        self.store = store
        self.model_adapter = model_adapter
        self.tool_provider = tool_provider
        self.hooks = hooks or HookSet()
        self.response_reviewer = response_reviewer
        self.turn_advisor = turn_advisor
        self.max_model_rounds = max_model_rounds
        self.max_model_deltas = max_model_deltas
        self.max_model_stream_bytes = (
            max_model_stream_bytes
            if max_model_stream_bytes is not None
            else max(store.max_payload_bytes * 16, store.max_payload_bytes)
        )
        provider_evidence_digest: str | None = None
        if model_adapter is not None:
            try:
                provider_evidence_digest = digest_json(
                    validate_model_adapter(model_adapter).as_dict()
                )
            except HarnessError:
                # Preserve the normal provider gate at run/resume time, but
                # never grant a writer permit to an unverified adapter.
                provider_evidence_digest = None
        self.__model_round_writer_permit = _ModelRoundWriterPermit()
        store._bind_model_round_writer(
            self,
            self.__model_round_writer_permit,
            can_write=provider_evidence_digest is not None,
            provider_evidence_digest=provider_evidence_digest,
        )
        self._lock = RLock()
        self._active: dict[tuple[str, str], _RunContext] = {}

    def run(self, scope: HarnessScope, *, prompt: str | None = None) -> TurnOutcome:
        """Create/reuse a durable turn and drive it until settle or pause."""

        if not scope.turn_id:
            raise ValueError("TurnRunner requires a turn scope")
        self.store.create_session(scope.session_scope())
        self._ensure_session_active(scope)
        if prompt is not None:
            self.store.create_turn(scope, prompt)
            self._append_user_prompt(scope)
        else:
            self.store.get_turn(scope)

        with self._claim_context(scope) as context:
            current = self.store.get_turn(scope)
            if current.status in {
                TurnStatus.SUCCEEDED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }:
                return self._outcome(scope)
            if current.status is TurnStatus.WAITING_APPROVAL:
                return self._waiting_outcome(scope)
            if current.status is TurnStatus.BLOCKED:
                return self._outcome(scope)
            try:
                evidence = validate_model_adapter(self.model_adapter)
                descriptors = self._tool_descriptors(scope)
            except HarnessError as exc:
                return self._block_or_fail(scope, exc, evidence=None)
            return self._drive_model(
                scope,
                context,
                evidence=evidence,
                descriptors=descriptors,
            )

    def resume(self, scope: HarnessScope) -> TurnOutcome:
        """Resume durable approval/tool continuation without replaying effects."""

        if not scope.turn_id:
            raise ValueError("TurnRunner requires a turn scope")
        with self._claim_context(scope) as context:
            current = self.store.get_turn(scope)
            if current.status in {
                TurnStatus.SUCCEEDED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }:
                return self._outcome(scope)
            try:
                persisted = self._load_continuation_calls(scope)
            except HarnessError as exc:
                return self._block_or_fail(scope, exc, evidence=None)
            pending = self.store.pending_actions(scope)
            if pending:
                self._transition_turn(scope, TurnStatus.WAITING_APPROVAL)
                return self._waiting_outcome(scope, pending[0].action_request_id)
            if not persisted and current.status in {
                TurnStatus.MODEL_STREAMING,
                TurnStatus.SETTLING,
            }:
                latest_round = self._latest_model_round(scope)
                if latest_round is None or latest_round.status is not ModelRoundStatus.COMPLETED:
                    return self._block_or_fail(
                        scope,
                        StoreUnavailable(
                            "model turn has no completed terminal authority",
                            code="recovery_required",
                        ),
                        evidence=self._durable_provider_evidence(scope),
                    )
                if self._has_pending_response_review(scope, latest_round):
                    try:
                        evidence = validate_model_adapter(self.model_adapter)
                        descriptors = self._tool_descriptors(scope)
                    except HarnessError as exc:
                        return self._block_or_fail(scope, exc, evidence=None)
                    return self._drive_model(
                        scope,
                        context,
                        evidence=evidence,
                        descriptors=descriptors,
                    )
                if not self._has_settleable_assistant(scope, latest_round):
                    return self._block_or_fail(
                        scope,
                        ProviderProtocolError(
                            "completed model round has no authorized assistant response",
                            code="missing_model_terminal",
                        ),
                        evidence=self._durable_provider_evidence(scope),
                    )
                return self._settle_completed_model_turn(
                    scope, evidence=self._durable_provider_evidence(scope)
                )
            try:
                evidence = validate_model_adapter(self.model_adapter)
                descriptors = self._tool_descriptors(scope)
            except HarnessError as exc:
                return self._block_or_fail(scope, exc, evidence=None)

            if not persisted:
                if current.status is TurnStatus.OPEN:
                    return self._drive_model(
                        scope,
                        context,
                        evidence=evidence,
                        descriptors=descriptors,
                    )
                return self._block_or_fail(
                    scope,
                    ProviderProtocolError("no durable tool continuation is available"),
                    evidence=evidence,
                )

            results: list[ToolResult] = []
            for persisted_call in persisted:
                descriptor = self._descriptor(descriptors, persisted_call.call.name)
                try:
                    action, authority = self._create_action_for_call(
                        scope, persisted_call.call, descriptor
                    )
                except HarnessError as exc:
                    return self._block_or_fail(scope, exc, evidence=evidence)
                if not action.terminal:
                    try:
                        self._bind_action_authority(
                            scope, persisted_call.call, action, authority
                        )
                    except HarnessError as exc:
                        if not action.terminal:
                            self.store.terminal_action(
                                scope,
                                action.action_request_id,
                                TerminalOutcome.FAILED,
                                result={},
                                error_code=exc.code,
                            )
                        return self._block_or_fail(scope, exc, evidence=evidence)
                if action.status is ActionStatus.APPROVAL_PENDING:
                    self._transition_turn(scope, TurnStatus.WAITING_APPROVAL)
                    return self._waiting_outcome(scope, action.action_request_id, evidence)
                if action.status in {ActionStatus.STARTING, ActionStatus.RUNNING}:
                    # A new process must call RecoveryManager before resume.  A
                    # running row is never silently replayed by this runner.
                    if action.status is ActionStatus.RUNNING:
                        return self._block_or_fail(
                            scope,
                            StoreUnavailable(
                                "running action requires recovery before resume",
                                code="recovery_required",
                            ),
                            evidence=evidence,
                        )
                    try:
                        result = self._execute_action(
                            scope, persisted_call.call, action, context, authority
                        )
                    except EffectUnknown as exc:
                        return self._block_or_fail(scope, exc, evidence=evidence)
                else:
                    result = self._tool_result(persisted_call.call, action)
                self._append_tool_result(scope, persisted_call, result)
                results.append(result)
                if context.model_cancel.is_set() or self.store.get_turn(scope).status is TurnStatus.CANCELLED:
                    return self._outcome(scope, error_code="cancelled")
                if not self._run_hook(
                    scope,
                    HookStage.AFTER_TOOL,
                    context,
                    tool_call=persisted_call.call,
                    action_request_id=persisted_call.action_request_id,
                    evidence=evidence,
                ):
                    return self._block_or_fail(
                        scope,
                        HookRejected("hook rejected the tool result"),
                        evidence=evidence,
                    )

            return self._drive_model(
                scope,
                context,
                evidence=evidence,
                descriptors=descriptors,
                continuation_of=tuple(item.call for item in persisted),
                tool_results=tuple(results),
            )

    def abort(self, scope: HarnessScope, *, reason: str = "abort") -> TurnOutcome:
        """Abort a turn; a running effect is conservatively marked UNCERTAIN."""

        with self._lock:
            context = self._active.get((scope.session_id, scope.turn_id))
            if context is not None:
                context.model_cancel.set()
                if context.active_token is not None:
                    context.active_token.cancel(reason)
                active_action_id = context.active_action_id
            else:
                active_action_id = None

        # Fence admitted attempts and close the turn's admission atomically. The
        # adapter's cancel event is cooperative, so an in-process abort that
        # cannot prove provider stop is represented as UNCERTAIN; a late
        # provider return can never replace this durable winner.
        self.store.reconcile_model_rounds(
            scope,
            reason=reason,
            status=ModelRoundStatus.UNCERTAIN,
            cancel_turn=True,
            _writer=self,
            _writer_capability=self.__model_round_writer_permit,
        )
        actions = self.store.actions_for_turn(scope)
        for action in actions:
            if action.terminal:
                continue
            action_reason = reason
            outcome = TerminalOutcome.CANCELLED
            if action.status in {ActionStatus.STARTING, ActionStatus.RUNNING} or action.action_request_id == active_action_id:
                outcome = TerminalOutcome.UNCERTAIN
                action_reason = "abort_effect_unknown"
            self.store.terminal_action(
                scope,
                action.action_request_id,
                outcome,
                result={},
                error_code=action_reason,
            )
        return self._outcome(scope, error_code=reason)

    def interrupt(self, scope: HarnessScope) -> TurnOutcome:
        return self.abort(scope, reason="interrupt")

    def steer(
        self,
        scope: HarnessScope,
        prompt: str,
        *,
        command_id: str | None = None,
    ) -> TurnOutcome:
        """Add one durable user steering item before an OPEN turn is driven."""

        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("steer prompt must be non-empty text")
        current = self.store.get_turn(scope)
        if current.status is not TurnStatus.OPEN:
            raise InvalidTransition(
                f"steer is only available for an open turn, not {current.status.value}"
            )
        stable_id = command_id or str(uuid4())
        TranscriptStore(self.store).append(
            scope,
            role="user",
            content=prompt,
            item_id=f"steer:{stable_id}",
        )
        self._append_event(
            scope,
            event_type="control.steer",
            phase="control",
            status="steered",
            payload={"command_id": stable_id},
            event_id=f"control-steer:{scope.turn_id}:{stable_id}",
            item_id=f"control-steer-item:{scope.turn_id}:{stable_id}",
        )
        return self.run(scope)

    def close(self) -> None:
        close = getattr(self.model_adapter, "close", None)
        if callable(close):
            close()

    def _ensure_session_active(self, scope: HarnessScope) -> None:
        session = self.store.get_session(scope.session_scope())
        if session.status is SessionStatus.CREATED:
            self.store.set_session_status(scope.session_scope(), SessionStatus.ACTIVE)

    def _append_user_prompt(self, scope: HarnessScope) -> None:
        TranscriptStore(self.store).append(
            scope,
            role="user",
            content=self.store.get_turn(scope).prompt,
            item_id=f"user-prompt:{scope.turn_id}",
        )

    def _tool_descriptors(self, scope: HarnessScope | None = None) -> tuple[ToolDescriptor, ...]:
        try:
            discover = getattr(self.tool_provider, "descriptors_for_scope", None)
            descriptors = tuple(discover(scope) if scope is not None and callable(discover)
                                else self.tool_provider.descriptors())
        except Exception as exc:
            raise ProviderProtocolError("tool provider descriptor discovery failed") from exc
        if not all(isinstance(descriptor, ToolDescriptor) for descriptor in descriptors):
            raise ProviderProtocolError("tool provider returned an invalid descriptor")
        if any(not descriptor.name.strip() for descriptor in descriptors):
            raise ProviderProtocolError("tool provider returned an unnamed descriptor")
        by_name = {descriptor.name: descriptor for descriptor in descriptors}
        if len(by_name) != len(descriptors):
            raise ProviderProtocolError("tool provider returned duplicate descriptors")
        return tuple(sorted(descriptors, key=lambda descriptor: descriptor.name))

    def _bind_action_authority(
        self,
        scope: HarnessScope,
        call: ToolCall,
        action: ActionRecord,
        authority: Mapping[str, Any] | None,
    ) -> None:
        """Require and bind the durable authority before an effect."""

        if authority is None:
            return
        if not authority.get("required"):
            return
        self.store.validate_action_authority(
            scope,
            action.action_request_id,
            authority,
        )
        if authority.get("identity") is None:
            raise CapabilityDenied(
                "mutable action provider authority is unavailable",
                code="action_authority_unavailable",
            )

        binder = getattr(self.tool_provider, "bind_action_authority", None)
        if not callable(binder):
            raise CapabilityDenied(
                "mutable action provider has no authority binder",
                code="action_authority_unavailable",
            )
        binder(
            scope,
            call,
            action_request_id=action.action_request_id,
        )

    def _action_authority(
        self,
        scope: HarnessScope,
        call: ToolCall,
        descriptor: ToolDescriptor,
    ) -> Mapping[str, Any] | None:
        """Build the durable provider identity before admitting a mutable call."""

        if not descriptor.mutable:
            return None
        identity_loader = getattr(self.tool_provider, "action_authority_identity", None)
        identity: Mapping[str, Any] | None = None
        if callable(identity_loader):
            try:
                candidate = identity_loader(
                    scope,
                    call,
                    action_request_id=_action_request_id(scope, call),
                )
            except HarnessError:
                raise
            except Exception as exc:
                raise ProviderProtocolError(
                    "mutable action provider authority discovery failed",
                    code="action_authority_unavailable",
                ) from exc
            if candidate is not None and not isinstance(candidate, Mapping):
                raise ProviderProtocolError(
                    "mutable action provider authority identity is invalid",
                    code="action_authority_unavailable",
                )
            identity = dict(candidate) if candidate is not None else None
        provider_type = f"{type(self.tool_provider).__module__}.{type(self.tool_provider).__qualname__}"
        descriptor_digest = digest_json(
            {
                "name": descriptor.name,
                "description": descriptor.description,
                "mutable": descriptor.mutable,
                "requires_approval": descriptor.requires_approval,
                "capability": descriptor.capability,
                "source": descriptor.source,
            }
        )
        return {
            "version": 1,
            "required": True,
            "provider_type": provider_type,
            "provider_source": descriptor.source,
            "descriptor_digest": descriptor_digest,
            "identity": identity,
        }

    def _create_action_for_call(
        self,
        scope: HarnessScope,
        call: ToolCall,
        descriptor: ToolDescriptor,
    ) -> tuple[ActionRecord, Mapping[str, Any] | None]:
        authority = self._action_authority(scope, call, descriptor)
        try:
            action = self.store.create_action(
                scope,
                action_request_id=_action_request_id(scope, call),
                tool_name=call.name,
                arguments=call.arguments,
                requires_approval=self._requires_approval(descriptor),
                classification=(
                    ActionClassification.MUTABLE_REQUIRED
                    if descriptor.mutable
                    else ActionClassification.READ_ONLY_NOT_REQUIRED
                ),
                authority=authority,
            )
        except IdempotencyConflict as exc:
            if authority is None:
                raise
            # A durable action already exists, but the provider/descriptor
            # presented by this process no longer matches it.  End a pending
            # action before returning the structured failure; never let a
            # legacy compatibility path execute the downgraded provider.
            try:
                existing = self.store.get_action(
                    scope, _action_request_id(scope, call)
                )
                if not existing.terminal:
                    self.store.terminal_action(
                        scope,
                        existing.action_request_id,
                        TerminalOutcome.FAILED,
                        result={},
                        error_code="action_authority_mismatch",
                    )
            except HarnessError:
                pass
            raise ProviderProtocolError(
                "mutable action provider authority differs from its durable action",
                code="action_authority_mismatch",
            ) from exc
        return action, authority

    @staticmethod
    def _descriptor(descriptors: tuple[ToolDescriptor, ...], name: str) -> ToolDescriptor:
        for descriptor in descriptors:
            if descriptor.name == name:
                return descriptor
        raise ProviderProtocolError(f"model requested an unregistered tool: {name}")

    @staticmethod
    def _requires_approval(descriptor: ToolDescriptor) -> bool:
        return descriptor.requires_approval or descriptor.mutable

    def _drive_model(
        self,
        scope: HarnessScope,
        context: _RunContext,
        *,
        evidence: ProviderEvidence,
        descriptors: tuple[ToolDescriptor, ...],
        continuation_of: tuple[ToolCall, ...] = (),
        tool_results: tuple[ToolResult, ...] = (),
        assistant_text: str = "",
    ) -> TurnOutcome:
        # Approval and process restarts must consume the original turn's
        # budget rather than granting another full model loop.
        for _round in range(self.store.next_model_round_index(scope), self.max_model_rounds):
            if context.model_cancel.is_set():
                return self._cancelled_outcome(scope, "interrupt")
            try:
                capper = getattr(self.tool_provider, "model_round_limit_for_scope", None)
                if callable(capper):
                    cap = capper(scope, self.max_model_rounds)
                    if type(cap) is not int or not 1 <= cap <= self.max_model_rounds:
                        raise ProviderProtocolError("invalid scoped model allowance")
                    if _round >= cap:
                        break
                if callable(getattr(self.tool_provider, "descriptors_for_scope", None)):
                    descriptors = self._tool_descriptors(scope)
            except HarnessError as exc:
                return self._block_or_fail(scope, exc, evidence=evidence)
            except Exception as exc:
                return self._block_or_fail(scope, ProviderProtocolError(
                    "scoped execution policy failed"), evidence=evidence)
            self._transition_turn(scope, TurnStatus.MODEL_STREAMING)
            if not self._run_hook(
                scope,
                HookStage.BEFORE_MODEL,
                context,
                evidence=evidence,
            ):
                return self._block_or_fail(
                    scope,
                    HookRejected("hook rejected model execution"),
                    evidence=evidence,
                )
            request = self._request(
                scope,
                descriptors,
                continuation_of=continuation_of,
                tool_results=tool_results,
            )
            if self.turn_advisor is not None and (
                self.store.next_model_round_index(scope) == 0
                or getattr(self.turn_advisor, "continues", False)
            ):
                try:
                    self._admit_turn_advice(request, context.model_cancel)
                    request = self._request(scope, descriptors, continuation_of=continuation_of,
                                            tool_results=tool_results)
                except CancellationRequested:
                    return self._cancelled_outcome(scope, "interrupt")
                except HarnessError as exc:
                    return self._block_or_fail(scope, exc, evidence=evidence)
            round_index = self.store.next_model_round_index(scope)
            round_id = f"{scope.digest}:model-round:{round_index}"
            response_id = f"{round_id}:response"
            request_digest = digest_json(request.as_dict())
            round_started = False
            round_finished = False
            deltas: list[ModelDelta] = []
            done_seen = False
            stream_bytes = 0
            stream: Any | None = None
            stream_cleanup_attempted = False
            stream_cleanup_succeeded = True

            def cleanup_model_stream(*, cancel: bool) -> bool:
                """Stop the adapter stream before publishing any terminal round."""

                nonlocal stream, stream_cleanup_attempted, stream_cleanup_succeeded
                if cancel:
                    context.model_cancel.set()
                if stream_cleanup_attempted:
                    return stream_cleanup_succeeded
                stream_cleanup_attempted = True
                if stream is None:
                    stream_cleanup_succeeded = True
                    return True
                try:
                    close = getattr(stream, "close", None)
                except Exception:
                    close = None
                if not callable(close):
                    stream_cleanup_succeeded = False
                else:
                    try:
                        stream_cleanup_succeeded = close() is not False
                    except Exception:
                        stream_cleanup_succeeded = False
                # Do not retry a provider-owned close from a later exception
                # path.  A raising close is itself evidence that stop could
                # not be confirmed and must produce the UNCERTAIN winner.
                stream = None
                return stream_cleanup_succeeded

            def finish_cancelled_model_round() -> TurnOutcome:
                """Close a cancelled stream before claiming its terminal winner."""

                nonlocal round_finished
                cleanup_ok = cleanup_model_stream(cancel=True)
                terminal_status = (
                    ModelRoundStatus.CANCELLED
                    if cleanup_ok
                    else ModelRoundStatus.UNCERTAIN
                )
                terminal_reason = (
                    None if cleanup_ok else _MODEL_STREAM_CLEANUP_UNCERTAIN
                )
                self._finish_model_round(
                    scope,
                    round_id,
                    terminal_status,
                    delta_count=len(deltas),
                    done_count=int(done_seen),
                    reason=terminal_reason,
                )
                round_finished = True
                with self._lock:
                    if context.active_round_id == round_id:
                        context.active_round_id = None
                if not cleanup_ok:
                    return self._block_or_fail(
                        scope,
                        ProviderProtocolError(
                            "ModelAdapter stream cleanup could not be confirmed",
                            code=_MODEL_STREAM_CLEANUP_UNCERTAIN,
                        ),
                        evidence=evidence,
                    )
                return self._cancelled_outcome(scope, "interrupt")

            try:
                self.store.start_model_round(
                    scope,
                    round_id=round_id,
                    round_index=round_index,
                    request_cursor=request.cursor,
                    request_digest=request_digest,
                    provider_evidence=evidence.as_dict(),
                    response_id=response_id,
                    _writer=self,
                    _writer_capability=self.__model_round_writer_permit,
                )
                round_started = True
                with self._lock:
                    context.active_round_id = round_id
                stream = self.model_adapter.stream(  # type: ignore[union-attr]
                    request, cancel_event=context.model_cancel
                )
                if not isinstance(stream, ClosableModelStream):
                    raise ProviderProtocolError(
                        "ModelAdapter returned a non-closable stream",
                        code="closable_stream_unsupported",
                    )
                for delta in stream:
                    if context.model_cancel.is_set():
                        return finish_cancelled_model_round()
                    if not isinstance(delta, ModelDelta):
                        raise ProviderProtocolError("ModelAdapter emitted an invalid delta")
                    if done_seen:
                        raise ProviderProtocolError(
                            "ModelAdapter emitted a delta after DONE",
                            code="model_terminal_violation",
                        )
                    if len(deltas) >= self.max_model_deltas:
                        raise ProviderProtocolError(
                            "ModelAdapter exceeded the model delta bound",
                            code="model_stream_too_large",
                        )
                    delta_bytes = self._model_delta_admission_bytes(delta)
                    if stream_bytes + delta_bytes > self.max_model_stream_bytes:
                        raise ProviderProtocolError(
                            "ModelAdapter exceeded the aggregate stream byte bound",
                            code="model_stream_too_large",
                        )
                    stream_bytes += delta_bytes
                    deltas.append(delta)
                    if delta.kind is ModelDeltaKind.DONE:
                        done_seen = True
                if not done_seen:
                    if context.model_cancel.is_set() or self.store.get_turn(scope).status is TurnStatus.CANCELLED:
                        return finish_cancelled_model_round()
                    raise ProviderProtocolError("ModelAdapter stream ended without done")
                if context.model_cancel.is_set() or self.store.get_turn(scope).status is TurnStatus.CANCELLED:
                    return finish_cancelled_model_round()

                if not cleanup_model_stream(cancel=False):
                    raise ProviderProtocolError(
                        "ModelAdapter stream cleanup could not be confirmed",
                        code=_MODEL_STREAM_CLEANUP_UNCERTAIN,
                    )

                done_usage = next(
                    (delta.usage for delta in deltas if delta.kind is ModelDeltaKind.DONE),
                    None,
                )
                done_provider_response_id = next(
                    (
                        delta.provider_response_id
                        for delta in deltas
                        if delta.kind is ModelDeltaKind.DONE
                    ),
                    None,
                )

                text_parts = [delta.text for delta in deltas if delta.kind is ModelDeltaKind.TEXT]
                reasoning_parts = [
                    delta.text for delta in deltas if delta.kind is ModelDeltaKind.REASONING
                ]
                raw_calls = [
                    delta.tool_call
                    for delta in deltas
                    if delta.kind is ModelDeltaKind.TOOL_CALL and delta.tool_call is not None
                ]
                calls = [self._canonical_tool_call(model_call) for model_call in raw_calls]
                seen_call_ids = self._known_tool_call_ids(scope)
                for model_call in calls:
                    if model_call.tool_call_id in seen_call_ids:
                        raise ProviderProtocolError(
                            "tool_call_id is already used in this turn",
                            code="duplicate_tool_call_id",
                        )
                    seen_call_ids.add(model_call.tool_call_id)
                    self._descriptor(descriptors, model_call.name)
                text = "".join(text_parts)
                reasoning = "".join(reasoning_parts)
                persisted_calls = tuple(
                    _PersistedToolCall(model_call, _action_request_id(scope, model_call))
                    for model_call in calls
                )
                assistant_payload = self._assistant_payload(text, persisted_calls)
                self._validate_model_response(
                    deltas=deltas,
                    text=text,
                    reasoning=reasoning,
                    calls=tuple(calls),
                    assistant_payload=assistant_payload,
                )

                response_digest = digest_json(assistant_payload)
                review_context = None
                review_pushed = False
                if (
                    self.response_reviewer is not None
                    and not calls
                    and text.strip()
                    and round_index + 1 < self.max_model_rounds
                    and self.store.max_payload_bytes >= 1024
                    and not self._response_review_seen(scope)
                ):
                    review_context = self._prepare_response_review(
                        request,
                        text,
                        round_id=round_id,
                        response_id=response_id,
                        response_digest=response_digest,
                        cancel_event=context.model_cancel,
                    )
                    review_pushed = decode_review_envelope(review_context)["context"] is not None
                if context.model_cancel.is_set() or self.store.get_turn(scope).status is TurnStatus.CANCELLED:
                    return finish_cancelled_model_round()

                text_index = 0
                reasoning_index = 0
                call_index = 0
                for delta in (() if review_pushed else deltas):
                    if delta.kind is ModelDeltaKind.TEXT:
                        self._append_delta(
                            scope,
                            event_type="turn.text_delta",
                            phase="stream",
                            status="delta",
                            payload={
                                "round_id": round_id,
                                "response_id": response_id,
                                "text": delta.text,
                            },
                            item_id=f"model-text:{response_id}",
                            delta_seq=text_index,
                            revision=text_index,
                        )
                        text_index += 1
                    elif delta.kind is ModelDeltaKind.REASONING:
                        self._append_delta(
                            scope,
                            event_type="turn.reasoning_delta",
                            phase="reasoning",
                            status="delta",
                            payload={
                                "round_id": round_id,
                                "response_id": response_id,
                                "text": delta.text,
                            },
                            item_id=f"model-reasoning:{response_id}",
                            delta_seq=reasoning_index,
                            revision=reasoning_index,
                        )
                        reasoning_index += 1
                    elif delta.kind is ModelDeltaKind.TOOL_CALL:
                        model_call = calls[call_index]
                        self._append_delta(
                            scope,
                            event_type="turn.tool_call",
                            phase="tool",
                            status="delta",
                            payload={
                                "round_id": round_id,
                                "response_id": response_id,
                                "tool_call_id": model_call.tool_call_id,
                                "name": model_call.name,
                                "arguments": dict(model_call.arguments),
                            },
                            item_id=f"model-tool-call:{response_id}:{call_index}",
                        )
                        call_index += 1
                if reasoning and not review_pushed:
                    TranscriptStore(self.store).append(
                        scope,
                        role="reasoning",
                        content=reasoning,
                        item_id=f"reasoning:{response_id}",
                    )
                assistant_item_id = (
                    f"assistant:{response_id}"
                    if (text or persisted_calls) and not review_pushed else ""
                )
                assistant_content = None
                if (text or persisted_calls) and not review_pushed:
                    assistant_content = (
                        json.dumps(
                            assistant_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if persisted_calls
                        else text
                    )
                terminal_round = self._complete_model_round(
                    scope,
                    round_id,
                    assistant_content=assistant_content,
                    assistant_item_id=assistant_item_id,
                    response_digest=response_digest,
                    delta_count=len(deltas),
                    done_count=1,
                    tool_call_count=len(persisted_calls),
                    provider_usage=done_usage,
                    provider_response_id=done_provider_response_id,
                    review_context=review_context,
                )
                round_finished = True
                with self._lock:
                    if context.active_round_id == round_id:
                        context.active_round_id = None
                if terminal_round.status is not ModelRoundStatus.COMPLETED:
                    return self._outcome(
                        scope,
                        error_code=terminal_round.status.value,
                        evidence=evidence,
                    )
            except CancellationRequested:
                return finish_cancelled_model_round()
            except HarnessError as exc:
                cleanup_ok = cleanup_model_stream(cancel=True)
                terminal_error: HarnessError = exc
                terminal_status = ModelRoundStatus.FAILED
                terminal_reason: str | None = None
                if not cleanup_ok:
                    terminal_error = ProviderProtocolError(
                        "ModelAdapter stream cleanup could not be confirmed",
                        code=_MODEL_STREAM_CLEANUP_UNCERTAIN,
                    )
                    terminal_status = ModelRoundStatus.UNCERTAIN
                    terminal_reason = _MODEL_STREAM_CLEANUP_UNCERTAIN
                if round_started and not round_finished:
                    self._finish_model_round(
                        scope,
                        round_id,
                        terminal_status,
                        delta_count=len(deltas),
                        done_count=int(done_seen),
                        reason=terminal_reason,
                    )
                    round_finished = True
                with self._lock:
                    if context.active_round_id == round_id:
                        context.active_round_id = None
                return self._block_or_fail(scope, terminal_error, evidence=evidence)
            except Exception as exc:
                cleanup_ok = cleanup_model_stream(cancel=True)
                terminal_error = (
                    ProviderProtocolError("ModelAdapter stream failed")
                    if not isinstance(exc, ProviderProtocolError)
                    else exc
                )
                terminal_status = ModelRoundStatus.FAILED
                terminal_reason = None
                if not cleanup_ok:
                    terminal_error = ProviderProtocolError(
                        "ModelAdapter stream cleanup could not be confirmed",
                        code=_MODEL_STREAM_CLEANUP_UNCERTAIN,
                    )
                    terminal_status = ModelRoundStatus.UNCERTAIN
                    terminal_reason = _MODEL_STREAM_CLEANUP_UNCERTAIN
                if round_started and not round_finished:
                    self._finish_model_round(
                        scope,
                        round_id,
                        terminal_status,
                        delta_count=len(deltas),
                        done_count=int(done_seen),
                        reason=terminal_reason,
                    )
                    round_finished = True
                with self._lock:
                    if context.active_round_id == round_id:
                        context.active_round_id = None
                return self._block_or_fail(scope, terminal_error, evidence=evidence)
            if review_pushed:
                # Continue the same session with its original tools and scope.
                # The candidate only survives as source-bound private review
                # evidence; it is never an assistant result or streamed text.
                continuation_of = ()
                tool_results = ()
                continue
            assistant_text += text

            if not calls:
                if not self._run_hook(
                    scope,
                    HookStage.AFTER_MODEL,
                    context,
                    evidence=evidence,
                ):
                    return self._block_or_fail(
                        scope,
                        HookRejected("hook rejected model settlement"),
                        evidence=evidence,
                    )
                return self._settle_completed_model_turn(
                    scope,
                    assistant_text=assistant_text,
                    evidence=evidence,
                )

            durable_calls = self._load_continuation_calls(scope)
            if tuple(item.call.tool_call_id for item in durable_calls) != tuple(
                item.call.tool_call_id for item in persisted_calls
            ):
                return self._block_or_fail(
                    scope,
                    ProviderProtocolError(
                        "durable tool continuation differs from model response",
                        code="continuation_authority_mismatch",
                    ),
                    evidence=evidence,
                )
            results: list[ToolResult] = []
            for persisted_call in durable_calls:
                descriptor = self._descriptor(descriptors, persisted_call.call.name)

                try:
                    action, authority = self._create_action_for_call(
                        scope, persisted_call.call, descriptor
                    )
                except HarnessError as exc:
                    return self._block_or_fail(scope, exc, evidence=evidence)
                if not action.terminal:
                    try:
                        self._bind_action_authority(
                            scope, persisted_call.call, action, authority
                        )
                    except HarnessError as exc:
                        if not action.terminal:
                            self.store.terminal_action(
                                scope,
                                action.action_request_id,
                                TerminalOutcome.FAILED,
                                result={},
                                error_code=exc.code,
                            )
                        return self._block_or_fail(scope, exc, evidence=evidence)
                if action.status is ActionStatus.APPROVAL_PENDING:
                    self._transition_turn(scope, TurnStatus.WAITING_APPROVAL)
                    return self._waiting_outcome(
                        scope, action.action_request_id, evidence, assistant_text
                    )
                if not self._run_hook(
                    scope,
                    HookStage.BEFORE_TOOL,
                    context,
                    tool_call=persisted_call.call,
                    action_request_id=persisted_call.action_request_id,
                    evidence=evidence,
                ):
                    if not action.terminal:
                        self.store.terminal_action(
                            scope,
                            action.action_request_id,
                            TerminalOutcome.FAILED,
                            result={},
                            error_code="hook_rejected",
                        )
                    return self._block_or_fail(
                        scope,
                        HookRejected("hook rejected tool execution"),
                        evidence=evidence,
                    )
                try:
                    result = self._execute_action(
                        scope, persisted_call.call, action, context, authority
                    )
                except EffectUnknown as exc:
                    return self._block_or_fail(scope, exc, evidence=evidence)
                self._append_tool_result(scope, persisted_call, result)
                results.append(result)
                if context.model_cancel.is_set() or self.store.get_turn(scope).status is TurnStatus.CANCELLED:
                    return self._outcome(scope, error_code="cancelled")
                if not self._run_hook(
                    scope,
                    HookStage.AFTER_TOOL,
                    context,
                    tool_call=persisted_call.call,
                    action_request_id=persisted_call.action_request_id,
                    evidence=evidence,
                ):
                    return self._block_or_fail(
                        scope,
                        HookRejected("hook rejected the tool result"),
                        evidence=evidence,
                    )

            continuation_of = tuple(calls)
            tool_results = tuple(results)

        return self._block_or_fail(
            scope,
            ProviderProtocolError("model loop exceeded its bounded round limit", code="model_loop_limit"),
            evidence=evidence,
        )

    def _admit_turn_advice(self, request: ModelRequest, cancel_event: Event) -> None:
        scope = request.scope
        index = self.store.next_model_round_index(scope)
        identity = TURN_ADVICE_PREFIX + scope.turn_id + (f":round:{index}" if index else "")
        if any(m.item_id == identity for m in TranscriptStore(self.store).messages(scope)):
            return
        try:
            advice = self.turn_advisor.advise(request, cancel_event=cancel_event)
            if advice is None:
                return
            if not isinstance(advice, TurnAdvice):
                raise ValueError("invalid advisor result")
            envelope = encode_advice(scope.digest, advice)
            self._require_text_payload(envelope, "turn advice")
        except (CancellationRequested, LeaseFenced, ScopeConflict, StoreUnavailable):
            raise
        except Exception:
            # A broken optional calculation does not hold authorized work hostage.
            # The receipt makes the skip visible without leaking exception text.
            envelope = encode_advice(scope.digest, TurnAdvice(receipt={
                "status": "skipped", "reason": "advisor_failed_or_invalid"}))
            self._require_text_payload(envelope, "turn advice skip")
        if cancel_event.is_set():
            raise CancellationRequested("turn advice cancelled")
        TranscriptStore(self.store).append(scope, role="system", content=envelope, item_id=identity)

    def _require_text_payload(self, value: str, label: str) -> None:
        clean, redacted, truncated = bound_text(value, self.store.max_payload_bytes)
        if redacted:
            raise RedactionRequired(f"{label} contains a raw credential")
        if truncated or clean != value:
            raise ProviderProtocolError(
                f"{label} exceeds the durable payload bound",
                code="payload_too_large",
            )

    def _prepare_response_review(
        self,
        request: ModelRequest,
        text: str,
        *,
        round_id: str,
        response_id: str,
        response_digest: str,
        cancel_event: Event,
    ) -> str:
        """Fail open on plugin failures, never on cancellation or store failures."""

        def envelope(result: ResponseReview) -> str:
            if not isinstance(result, ResponseReview) or not isinstance(result.receipt, Mapping):
                raise ValueError("reviewer returned an invalid response review")
            if result.context is not None:
                if not isinstance(result.context, str) or not result.context.strip():
                    raise ValueError("reviewer returned invalid context")
                self._require_text_payload(result.context, "response review context")
            receipt = self._require_mapping_payload(result.receipt, "response review receipt")
            value = encode_review_envelope(
                round_id=round_id,
                response_id=response_id,
                response_digest=response_digest,
                candidate_text=text if result.context is not None else None,
                context=result.context,
                receipt=receipt,
            )
            self._require_text_payload(value, "response review envelope")
            return value

        result: ResponseReview | None = None
        try:
            result = self.response_reviewer.review(  # type: ignore[union-attr]
                request, text, round_id=round_id, cancel_event=cancel_event
            )
            if cancel_event.is_set():
                raise CancellationRequested("response review cancelled")
            return envelope(result)
        except CancellationRequested:
            raise
        except (LeaseFenced, ScopeConflict, StoreUnavailable):
            # Revoked host authority or unavailable durable state is not an
            # optional plugin miss. The ordinary turn failure path must stop
            # publication of the still-private candidate.
            raise
        except Exception:
            if cancel_event.is_set():
                raise CancellationRequested("response review cancelled")
            if isinstance(result, ResponseReview):
                try:
                    # A fork may already have consumed provider usage before
                    # its context fails admission (including JSON escaping in
                    # the host envelope). Keep its safe durable accounting
                    # receipt even when the candidate cannot be revised.
                    if not isinstance(result.receipt, Mapping):
                        raise ValueError("reviewer returned an invalid receipt")
                    receipt = self._require_mapping_payload(
                        result.receipt, "response review receipt"
                    )
                    return envelope(ResponseReview(receipt={
                        **receipt,
                        "status": "skipped",
                        "reason": "review_failed_or_invalid",
                    }))
                except (CancellationRequested, LeaseFenced, ScopeConflict, StoreUnavailable):
                    raise
                except Exception:
                    if cancel_event.is_set():
                        raise CancellationRequested("response review cancelled")
            return envelope(ResponseReview(
                receipt={"status": "skipped", "reason": "review_failed_or_invalid"}
            ))

    def _response_review_seen(self, scope: HarnessScope) -> bool:
        return any(
            message.item_id.startswith(RESPONSE_REVIEW_PREFIX)
            for message in TranscriptStore(self.store).messages(scope)
        )

    def _has_pending_response_review(
        self, scope: HarnessScope, round_record: ModelRoundRecord
    ) -> bool:
        if (
            round_record.status is not ModelRoundStatus.COMPLETED
            or round_record.tool_call_count
            or round_record.assistant_item_id
        ):
            return False
        review_id = f"{RESPONSE_REVIEW_PREFIX}{round_record.response_id}"
        for message in TranscriptStore(self.store).messages(scope):
            if message.item_id == review_id:
                review = decode_review_envelope(message.content)
                return review["context"] is not None and review["round_id"] == round_record.round_id
        return False

    @staticmethod
    def _model_delta_admission_bytes(delta: ModelDelta) -> int:
        """Measure one delta without retaining an unbounded stream buffer."""

        if delta.kind in {ModelDeltaKind.TEXT, ModelDeltaKind.REASONING}:
            return len(delta.text.encode("utf-8"))
        if delta.kind is ModelDeltaKind.TOOL_CALL:
            call = delta.tool_call
            if call is None:  # pragma: no cover - ModelDelta validates this
                raise ProviderProtocolError("tool-call delta has no call")
            try:
                encoded = json.dumps(
                    {
                        "tool_call_id": call.tool_call_id,
                        "name": call.name,
                        "arguments": dict(call.arguments),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ProviderProtocolError(
                    "tool-call delta is not canonical",
                    code="model_stream_invalid",
                ) from exc
            return len(encoded)
        usage_bytes = b""
        if delta.usage is not None:
            usage_bytes = json.dumps(
                delta.usage.as_dict(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        response_id_bytes = (delta.provider_response_id or "").encode("utf-8")
        return (
            len((delta.finish_reason or "").encode("utf-8"))
            + len(usage_bytes)
            + len(response_id_bytes)
        )

    def _finish_model_round(
        self,
        scope: HarnessScope,
        round_id: str,
        status: ModelRoundStatus,
        *,
        delta_count: int,
        done_count: int,
        response_digest: str = "",
        assistant_item_id: str = "",
        tool_call_count: int = 0,
        reason: str | None = None,
    ) -> ModelRoundRecord:
        """Return the durable winner when abort races provider settlement."""

        try:
            return self.store.finish_model_round(
                scope,
                round_id,
                status,
                delta_count=delta_count,
                done_count=done_count,
                response_digest=response_digest,
                assistant_item_id=assistant_item_id,
                tool_call_count=tool_call_count,
                reason=reason,
                _writer=self,
                _writer_capability=self.__model_round_writer_permit,
            )
        except IdempotencyConflict:
            for round_record in self.store.model_rounds_for_turn(scope):
                if round_record.round_id == round_id:
                    if round_record.status is not ModelRoundStatus.STREAMING:
                        return round_record
                    break
            raise

    def _complete_model_round(
        self,
        scope: HarnessScope,
        round_id: str,
        *,
        assistant_content: str | None,
        assistant_item_id: str,
        response_digest: str,
        delta_count: int,
        done_count: int,
        tool_call_count: int,
        provider_usage: ProviderUsage | None,
        provider_response_id: str | None,
        review_context: str | None = None,
    ) -> ModelRoundRecord:
        """Atomically publish assistant truth only if this runner still owns the round."""

        try:
            return self.store.complete_model_round_with_assistant(
                scope,
                round_id,
                assistant_content=assistant_content,
                assistant_item_id=assistant_item_id,
                response_digest=response_digest,
                delta_count=delta_count,
                done_count=done_count,
                tool_call_count=tool_call_count,
                provider_usage=(provider_usage.as_dict() if provider_usage is not None else None),
                usage_evidence_class=(
                    provider_usage.evidence_class
                    if provider_usage is not None
                    else "KERNEL_PROVIDER_USAGE_UNACCOUNTED"
                ),
                provider_response_id=provider_response_id,
                review_context=review_context,
                _writer=self,
                _writer_capability=self.__model_round_writer_permit,
            )
        except IdempotencyConflict:
            for round_record in self.store.model_rounds_for_turn(scope):
                if round_record.round_id == round_id:
                    if round_record.status is not ModelRoundStatus.STREAMING:
                        return round_record
                    break
            raise

    def _require_mapping_payload(self, value: Mapping[str, Any], label: str) -> dict[str, Any]:
        from .secrets import normalize_secret_refs

        normalized = normalize_secret_refs(dict(value))
        if not isinstance(normalized, Mapping):  # pragma: no cover - defensive
            raise ProviderProtocolError(f"{label} is not a canonical object")
        clean, redacted, truncated = bound_payload(
            dict(normalized), self.store.max_payload_bytes
        )
        if redacted:
            raise RedactionRequired(f"{label} contains a raw credential")
        if truncated or clean != dict(normalized):
            raise ProviderProtocolError(
                f"{label} exceeds the durable payload bound",
                code="payload_too_large",
            )
        return clean

    def _canonical_tool_call(self, call: ToolCall) -> ToolCall:
        from .secrets import normalize_secret_refs

        arguments = normalize_secret_refs(dict(call.arguments))
        if not isinstance(arguments, Mapping):  # pragma: no cover - defensive
            raise ProviderProtocolError("tool-call arguments are not canonical")
        self._require_mapping_payload(arguments, "tool-call arguments")
        return ToolCall(call.tool_call_id, call.name, dict(arguments))

    @staticmethod
    def _assistant_payload(
        text: str, calls: tuple[_PersistedToolCall, ...]
    ) -> dict[str, Any]:
        return {
            "text": text,
            "tool_calls": [
                {
                    "tool_call_id": item.call.tool_call_id,
                    "action_request_id": item.action_request_id,
                    "name": item.call.name,
                    "arguments": dict(item.call.arguments),
                }
                for item in calls
            ],
        }

    def _validate_model_response(
        self,
        *,
        deltas: list[ModelDelta],
        text: str,
        reasoning: str,
        calls: tuple[ToolCall, ...],
        assistant_payload: Mapping[str, Any],
    ) -> None:
        if len([delta for delta in deltas if delta.kind is ModelDeltaKind.DONE]) != 1:
            raise ProviderProtocolError(
                "model response must contain exactly one DONE",
                code="model_terminal_violation",
            )
        self._require_text_payload(text, "model text") if text else None
        self._require_text_payload(reasoning, "model reasoning") if reasoning else None
        for delta in deltas:
            if delta.kind in {ModelDeltaKind.TEXT, ModelDeltaKind.REASONING}:
                self._require_text_payload(delta.text, "model delta")
            elif delta.kind is ModelDeltaKind.TOOL_CALL and delta.tool_call is not None:
                self._require_mapping_payload(
                    {
                        "tool_call_id": delta.tool_call.tool_call_id,
                        "name": delta.tool_call.name,
                        "arguments": dict(delta.tool_call.arguments),
                    },
                    "model tool-call delta",
                )
        self._require_mapping_payload(assistant_payload, "model response")
        if calls:
            assistant_content = json.dumps(
                assistant_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self._require_text_payload(assistant_content, "assistant continuation")

    def _latest_model_round(self, scope: HarnessScope) -> ModelRoundRecord | None:
        rounds = self.store.model_rounds_for_turn(scope)
        return rounds[-1] if rounds else None

    def _durable_provider_evidence(self, scope: HarnessScope) -> ProviderEvidence | None:
        latest = self._latest_model_round(scope)
        if latest is None:
            return None
        try:
            return ProviderEvidence.from_dict(latest.provider_evidence)
        except (TypeError, ValueError) as exc:
            raise StoreUnavailable("durable provider evidence is invalid") from exc

    def _parse_assistant_message(
        self, scope: HarnessScope, content: str
    ) -> tuple[str, tuple[_PersistedToolCall, ...]]:
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            return content, ()
        if not isinstance(decoded, Mapping) or "tool_calls" not in decoded:
            return content, ()
        text = decoded.get("text", "")
        raw_calls = decoded.get("tool_calls")
        if not isinstance(text, str) or not isinstance(raw_calls, list):
            raise ProviderProtocolError(
                "durable assistant continuation is malformed",
                code="continuation_authority_mismatch",
            )
        calls: list[_PersistedToolCall] = []
        seen: set[str] = set()
        for raw in raw_calls:
            if not isinstance(raw, Mapping):
                raise ProviderProtocolError(
                    "durable tool continuation is malformed",
                    code="continuation_authority_mismatch",
                )
            action_id = raw.get("action_request_id")
            tool_call_id = raw.get("tool_call_id")
            name = raw.get("name")
            arguments = raw.get("arguments")
            if not all(isinstance(value, str) and value for value in (action_id, tool_call_id, name)):
                raise ProviderProtocolError(
                    "durable tool continuation identity is malformed",
                    code="continuation_authority_mismatch",
                )
            if tool_call_id in seen:
                raise ProviderProtocolError(
                    "durable tool continuation repeats a tool_call_id",
                    code="duplicate_tool_call_id",
                )
            if not isinstance(arguments, Mapping):
                raise ProviderProtocolError(
                    "durable tool continuation arguments are malformed",
                    code="continuation_authority_mismatch",
                )
            call = self._canonical_tool_call(ToolCall(tool_call_id, name, dict(arguments)))
            if action_id != _action_request_id(scope, call):
                raise ProviderProtocolError(
                    "durable tool continuation scope is invalid",
                    code="continuation_authority_mismatch",
                )
            seen.add(tool_call_id)
            calls.append(_PersistedToolCall(call, action_id))
        return text, tuple(calls)

    def _validate_assistant_transcript_authority(self, scope: HarnessScope) -> None:
        rounds = self.store.model_rounds_for_session(scope)
        by_item = {
            round_record.assistant_item_id: round_record
            for round_record in rounds
            if round_record.assistant_item_id
        }
        messages = TranscriptStore(self.store).messages(scope)
        for message in messages:
            if message.role != "assistant":
                continue
            round_record = by_item.get(message.item_id)
            if round_record is None:
                raise ProviderProtocolError(
                    "assistant transcript has no model terminal authority",
                    code="missing_model_terminal",
                )
            if round_record.status is not ModelRoundStatus.COMPLETED:
                raise ProviderProtocolError(
                    "assistant transcript is backed by a non-terminal model round",
                    code="missing_model_terminal",
                )
            text, calls = self._parse_assistant_message(message.scope, message.content)
            payload = self._assistant_payload(text, calls)
            if digest_json(payload) != round_record.response_digest:
                raise ProviderProtocolError(
                    "assistant transcript does not match model terminal authority",
                    code="continuation_authority_mismatch",
                )
            if len(calls) != round_record.tool_call_count:
                raise ProviderProtocolError(
                    "assistant transcript tool-call count differs from authority",
                    code="continuation_authority_mismatch",
                )

    def _known_tool_call_ids(self, scope: HarnessScope) -> set[str]:
        self._validate_assistant_transcript_authority(scope.session_scope())
        known: set[str] = set()
        for message in TranscriptStore(self.store).messages(scope):
            if message.role != "assistant":
                continue
            _, calls = self._parse_assistant_message(message.scope, message.content)
            for persisted in calls:
                if persisted.call.tool_call_id in known:
                    raise ProviderProtocolError(
                        "durable transcript repeats a tool_call_id",
                        code="duplicate_tool_call_id",
                    )
                known.add(persisted.call.tool_call_id)
        return known

    def _request(
        self,
        scope: HarnessScope,
        descriptors: tuple[ToolDescriptor, ...],
        *,
        continuation_of: tuple[ToolCall, ...],
        tool_results: tuple[ToolResult, ...],
    ) -> ModelRequest:
        self._validate_assistant_transcript_authority(scope.session_scope())
        messages_list: list[dict[str, Any]] = []
        for message in TranscriptStore(self.store).messages(scope.session_scope()):
            content = message.content
            if message.item_id.startswith(TURN_ADVICE_PREFIX):
                # Retain provenance in history without replaying obsolete advice
                # into a later task, including after the module is disabled.
                if message.scope.turn_id != scope.turn_id:
                    continue
                advice = decode_advice(content)
                if message.role != "system" or advice["scope_digest"] != scope.digest:
                    raise ProviderProtocolError("turn advice scope mismatch")
                if advice["context"] is None:
                    continue
                content = advice["context"]
            if message.item_id.startswith(RESPONSE_REVIEW_PREFIX):
                review = decode_review_envelope(content)
                if review["context"] is None:
                    continue
                content = review["context"]
            messages_list.append({
                "role": message.role,
                "content": content,
                "item_id": message.item_id,
                "transcript_seq": message.transcript_seq,
            })
        messages = tuple(messages_list)
        cursor = self.store.get_session(scope.session_scope()).cursor
        return ModelRequest(
            scope=scope,
            messages=messages,
            tools=descriptors,
            cursor=cursor,
            continuation_of=continuation_of,
            tool_results=tool_results,
        )

    def _execute_action(
        self,
        scope: HarnessScope,
        call: ToolCall,
        action: ActionRecord,
        context: _RunContext,
        authority: Mapping[str, Any] | None,
    ) -> ToolResult:
        action = self.store.claim_action_start(
            scope,
            action.action_request_id,
            authority=authority,
        )
        if action.terminal:
            return self._tool_result(call, action)
        if action.status is not ActionStatus.STARTING:
            return self._tool_result(call, action)
        action = self.store.mark_action_running(scope, action.action_request_id)
        if action.terminal:
            return self._tool_result(call, action)
        token = CancellationToken()
        with self._lock:
            context.active_action_id = action.action_request_id
            context.active_token = token
            if context.model_cancel.is_set():
                token.cancel("turn_cancelled")
        current = self.store.get_action(scope, action.action_request_id)
        if current.terminal or token.cancelled:
            if not current.terminal:
                current = self.store.terminal_action(
                    scope,
                    action.action_request_id,
                    TerminalOutcome.UNCERTAIN,
                    result={},
                    error_code="cancelled_before_effect_start",
                )
            with self._lock:
                context.active_action_id = None
                context.active_token = None
            return self._tool_result(call, current)
        try:
            scoped_execute = getattr(self.tool_provider, "execute_for_scope", None)
            if callable(scoped_execute):
                output = scoped_execute(
                    scope,
                    call,
                    token,
                    action_request_id=action.action_request_id,
                )
            else:
                output = self.tool_provider.execute(call, token)
            if not isinstance(output, Mapping):
                raise ProviderProtocolError("tool provider returned a non-mapping result")
            if token.cancelled:
                terminal = self.store.terminal_action(
                    scope,
                    action.action_request_id,
                    TerminalOutcome.UNCERTAIN,
                    result={},
                    error_code="cancelled_effect_unknown",
                )
            else:
                try:
                    self._require_mapping_payload(
                        {"output": dict(output)}, "tool result"
                    )
                except RedactionRequired:
                    terminal = self.store.terminal_action(
                        scope,
                        action.action_request_id,
                        TerminalOutcome.UNCERTAIN,
                        result={},
                        error_code="result_redaction_required",
                    )
                except ProviderProtocolError:
                    terminal = self.store.terminal_action(
                        scope,
                        action.action_request_id,
                        TerminalOutcome.UNCERTAIN,
                        result={},
                        error_code="payload_too_large",
                    )
                else:
                    terminal = self.store.terminal_action(
                        scope,
                        action.action_request_id,
                        TerminalOutcome.SUCCEEDED,
                        result={"output": dict(output)},
                    )
        except EffectUnknown:
            # The adapter has crossed (or may have crossed) its physical
            # effect boundary but could not publish a durable terminal.  Do
            # not turn this into a failed action: leave the action/operation
            # running so restart recovery owns the one UNCERTAIN winner.
            raise
        except CancellationRequested:
            terminal = self.store.terminal_action(
                scope,
                action.action_request_id,
                TerminalOutcome.CANCELLED,
                result={},
                error_code="action_cancelled",
            )
        except RedactionRequired:
            terminal = self.store.terminal_action(
                scope,
                action.action_request_id,
                TerminalOutcome.UNCERTAIN,
                result={},
                error_code="result_redaction_required",
            )
        except HarnessError as exc:
            terminal = self.store.terminal_action(
                scope,
                action.action_request_id,
                TerminalOutcome.FAILED,
                result={},
                error_code=exc.code,
            )
        except Exception:
            terminal = self.store.terminal_action(
                scope,
                action.action_request_id,
                TerminalOutcome.FAILED,
                result={},
                error_code="tool_execution_failed",
            )
        finally:
            with self._lock:
                context.active_action_id = None
                context.active_token = None
        return self._tool_result(call, terminal)

    @staticmethod
    def _tool_result(call: ToolCall, action: ActionRecord) -> ToolResult:
        result = action.result or {}
        output = result.get("output", result) if isinstance(result, Mapping) else {}
        if not isinstance(output, Mapping):
            output = {"value": output}
        return ToolResult(
            tool_call_id=call.tool_call_id,
            status=action.status.value,
            output=dict(output),
            error_code=action.error_code,
            action_request_id=action.action_request_id,
            approval_id=action.approval_id,
            operation_id=action.operation_id,
        )

    def _append_tool_result(
        self,
        scope: HarnessScope,
        persisted: _PersistedToolCall,
        result: ToolResult,
    ) -> None:
        result_payload = self._require_mapping_payload(
            {
            "tool_call_id": result.tool_call_id,
            "action_request_id": result.action_request_id,
            "status": result.status,
            "output": dict(result.output),
            "error_code": result.error_code,
            },
            "tool result",
        )
        content = json.dumps(
            result_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        TranscriptStore(self.store).append(
            scope,
            role="tool",
            content=content,
            item_id=f"tool-result:{persisted.action_request_id}",
        )
        self.store.deliver_action_result(scope, persisted.action_request_id)
        self._append_event(
            scope,
            event_type="turn.tool_result",
            phase="tool",
            status=result.status,
            payload={
                **result_payload,
            },
            event_id=f"tool-result-event:{persisted.action_request_id}",
            item_id=f"tool-result-event-item:{persisted.action_request_id}",
            action_request_id=persisted.action_request_id,
            approval_id=result.approval_id,
            operation_id=result.operation_id,
        )

    def _load_continuation_calls(self, scope: HarnessScope) -> tuple[_PersistedToolCall, ...]:
        self._validate_assistant_transcript_authority(scope.session_scope())
        rounds = self.store.model_rounds_for_turn(scope)
        if not rounds:
            return ()
        latest = rounds[-1]
        if latest.status is ModelRoundStatus.STREAMING:
            raise StoreUnavailable(
                "model response is still streaming and requires recovery",
                code="recovery_required",
            )
        if latest.status is not ModelRoundStatus.COMPLETED:
            return ()
        if latest.tool_call_count == 0:
            return ()
        messages = TranscriptStore(self.store).messages(scope)
        message = next(
            (item for item in messages if item.item_id == latest.assistant_item_id),
            None,
        )
        if message is None:
            raise ProviderProtocolError(
                "model terminal authority has no assistant continuation",
                code="missing_model_terminal",
            )
        _, calls = self._parse_assistant_message(message.scope, message.content)
        if len(calls) != latest.tool_call_count:
            raise ProviderProtocolError(
                "model terminal authority tool-call count is inconsistent",
                code="continuation_authority_mismatch",
            )
        return calls

    def _has_settleable_assistant(
        self, scope: HarnessScope, round_record: ModelRoundRecord
    ) -> bool:
        if round_record.status is not ModelRoundStatus.COMPLETED:
            return False
        if round_record.tool_call_count:
            return False
        if not round_record.assistant_item_id:
            return not self._has_pending_response_review(scope, round_record)
        messages = TranscriptStore(self.store).messages(scope)
        return any(
            message.item_id == round_record.assistant_item_id
            and message.role == "assistant"
            for message in messages
        )

    def _run_hook(
        self,
        scope: HarnessScope,
        stage: HookStage,
        context: _RunContext,
        *,
        tool_call: ToolCall | None = None,
        action_request_id: str | None = None,
        evidence: ProviderEvidence,
    ) -> bool:
        turn = self.store.get_turn(scope)
        session = self.store.get_session(scope.session_scope())
        hook_context = HookContext(
            stage=stage,
            scope=scope,
            turn_revision=turn.revision,
            transcript_cursor=session.transcript_cursor,
            tool_call=tool_call,
            action_request_id=action_request_id,
            capabilities=evidence.capabilities,
        )
        try:
            result = self.hooks.evaluate(hook_context)
        except HookRejected as exc:
            self._append_event(
                scope,
                event_type="hook.decision",
                phase="hook",
                status="rejected",
                payload={"stage": stage.value, "error_code": exc.code},
            )
            return False
        if result.decision is HookDecision.DENY:
            self._append_event(
                scope,
                event_type="hook.decision",
                phase="hook",
                status="rejected",
                payload={"stage": stage.value, "reason": result.reason},
            )
            return False
        self._append_event(
            scope,
            event_type="hook.decision",
            phase="hook",
            status="accepted",
            payload={"stage": stage.value},
        )
        return True

    def _settle_completed_model_turn(
        self,
        scope: HarnessScope,
        *,
        evidence: ProviderEvidence | None,
        assistant_text: str | None = None,
    ) -> TurnOutcome:
        """Respect a cancellation that wins after durable model completion.

        Approval continuations keep the original wall-time deadline. An abort
        may therefore commit between model completion and either final turn
        transition, including after its revision was read. The completed
        model/effect remains evidence, but cannot revive a cancelled turn.
        """
        try:
            self._transition_turn(scope, TurnStatus.SETTLING)
            self._transition_turn(scope, TurnStatus.SUCCEEDED)
        except (InvalidTransition, RevisionConflict):
            if self.store.get_turn(scope).status is not TurnStatus.CANCELLED:
                raise
            return self._outcome(scope, error_code="cancelled", evidence=evidence)
        return self._outcome(scope, assistant_text=assistant_text, evidence=evidence)

    def _transition_turn(self, scope: HarnessScope, status: TurnStatus) -> None:
        current = self.store.get_turn(scope)
        if current.status is status:
            return
        updated = self.store.set_turn_status(scope, status, expected_revision=current.revision)
        self._append_event(
            scope,
            event_type="turn.status",
            phase="turn",
            status=status.value,
            payload={"status": status.value, "revision": updated.revision},
            event_id=f"turn-status:{scope.turn_id}:{updated.revision}",
            item_id=f"turn-status-item:{scope.turn_id}:{updated.revision}",
        )

    def _append_delta(
        self,
        scope: HarnessScope,
        *,
        event_type: str,
        phase: str,
        status: str,
        payload: Mapping[str, Any],
        item_id: str,
        delta_seq: int = 0,
        revision: int = 0,
    ) -> HarnessEvent:
        return self._append_event(
            scope,
            event_type=event_type,
            phase=phase,
            status=status,
            payload=payload,
            item_id=item_id,
            delta_seq=delta_seq,
            revision=revision,
        )

    def _append_event(
        self,
        scope: HarnessScope,
        *,
        event_type: str,
        phase: str,
        status: str,
        payload: Mapping[str, Any],
        event_id: str | None = None,
        item_id: str | None = None,
        delta_seq: int = 0,
        revision: int = 0,
        action_request_id: str | None = None,
        approval_id: str | None = None,
        operation_id: str | None = None,
    ) -> HarnessEvent:
        return self.store.append_event(
            EventDraft(
                scope=scope,
                event_type=event_type,
                source="harness",
                phase=phase,
                status=status,
                payload=payload,
                event_id=event_id or str(uuid4()),
                item_id=item_id or str(uuid4()),
                delta_seq=delta_seq,
                revision=revision,
                action_request_id=action_request_id,
                approval_id=approval_id,
                operation_id=operation_id,
            )
        )

    def _block_or_fail(
        self,
        scope: HarnessScope,
        error: HarnessError,
        *,
        evidence: ProviderEvidence | None,
    ) -> TurnOutcome:
        status = (
            TurnStatus.BLOCKED
            if isinstance(error, (ProviderUnavailable, ProviderCapabilityMissing))
            or error.code in {"effect_unknown", "hook_rejected", "recovery_required"}
            else TurnStatus.FAILED
        )
        current = self.store.get_turn(scope)
        if current.status in {
            TurnStatus.SUCCEEDED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        }:
            return self._outcome(scope, error_code=error.code, evidence=evidence)
        try:
            self._transition_turn(scope, status)
        except InvalidTransition:
            current = self.store.get_turn(scope)
            if current.status not in {status, TurnStatus.CANCELLED, TurnStatus.SUCCEEDED, TurnStatus.FAILED}:
                raise
        self._append_event(
            scope,
            event_type="turn.error",
            phase="turn",
            status=status.value,
            payload={"error_code": error.code},
            event_id=f"turn-error:{scope.turn_id}:{error.code}",
            item_id=f"turn-error-item:{scope.turn_id}:{error.code}",
        )
        return self._outcome(scope, error_code=error.code, evidence=evidence)

    def _cancelled_outcome(self, scope: HarnessScope, reason: str) -> TurnOutcome:
        current = self.store.get_turn(scope)
        if current.status not in {
            TurnStatus.SUCCEEDED,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        }:
            self._transition_turn(scope, TurnStatus.CANCELLED)
        return self._outcome(scope, error_code=reason)

    def _waiting_outcome(
        self,
        scope: HarnessScope,
        action_request_id: str | None = None,
        evidence: ProviderEvidence | None = None,
        assistant_text: str = "",
    ) -> TurnOutcome:
        if action_request_id is None:
            pending = self.store.pending_actions(scope)
            action_request_id = pending[0].action_request_id if pending else None
        return self._outcome(
            scope,
            assistant_text=assistant_text,
            pending_action_request_id=action_request_id,
            evidence=evidence,
        )

    def _outcome(
        self,
        scope: HarnessScope,
        *,
        assistant_text: str | None = None,
        pending_action_request_id: str | None = None,
        error_code: str | None = None,
        evidence: ProviderEvidence | None = None,
    ) -> TurnOutcome:
        turn = self.store.get_turn(scope)
        if evidence is None:
            evidence = self._durable_provider_evidence(scope)
        if assistant_text is None:
            assistant_text = self._assistant_text(scope)
        if pending_action_request_id is None and turn.status is TurnStatus.WAITING_APPROVAL:
            pending = self.store.pending_actions(scope)
            pending_action_request_id = pending[0].action_request_id if pending else None
        return TurnOutcome(
            scope=scope,
            status=turn.status,
            cursor=self.store.get_session(scope.session_scope()).cursor,
            assistant_text=assistant_text,
            pending_action_request_id=pending_action_request_id,
            error_code=error_code,
            evidence={"provider": evidence.as_dict()} if evidence is not None else {},
        )

    def _assistant_text(self, scope: HarnessScope) -> str:
        text: list[str] = []
        for message in TranscriptStore(self.store).messages(scope):
            if message.role != "assistant":
                continue
            # Ordinary JSON is still the assistant's exact response. Only
            # the existing canonical tool-continuation envelope has a text
            # field to unwrap; selector objects, arrays and JSON strings must
            # survive terminal-turn replay without being dropped or decoded.
            response_text, _ = self._parse_assistant_message(message.scope, message.content)
            text.append(response_text)
        return "".join(text)

    class _ContextLease:
        def __init__(self, runner: "TurnRunner", scope: HarnessScope) -> None:
            self.runner = runner
            self.scope = scope
            self.context: _RunContext | None = None

        def __enter__(self) -> _RunContext:
            with self.runner._lock:
                key = (self.scope.session_id, self.scope.turn_id)
                if key in self.runner._active:
                    raise InvalidTransition("turn already has an active runner")
                self.context = _RunContext(model_cancel=Event())
                self.runner._active[key] = self.context
                return self.context

        def __exit__(self, *_: object) -> None:
            with self.runner._lock:
                self.runner._active.pop((self.scope.session_id, self.scope.turn_id), None)

    def _claim_context(self, scope: HarnessScope) -> "TurnRunner._ContextLease":
        return TurnRunner._ContextLease(self, scope)
