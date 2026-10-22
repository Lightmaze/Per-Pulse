"""Provider-neutral durable records shared by the kernel and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4

from .scope import HarnessScope


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    QUIESCED = "quiesced"
    CLOSED = "closed"


class TurnStatus(StrEnum):
    OPEN = "open"
    MODEL_STREAMING = "model_streaming"
    WAITING_APPROVAL = "waiting_approval"
    SETTLING = "settling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class ModelRoundStatus(StrEnum):
    """Durable authority state for one provider request/response attempt."""

    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class ActionStatus(StrEnum):
    INTENT = "intent"
    APPROVAL_PENDING = "approval_pending"
    STARTING = "starting"
    RUNNING = "running"
    TERMINALIZING = "terminalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"

    @property
    def terminal(self) -> bool:
        return self in {
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED,
            ActionStatus.CANCELLED,
            ActionStatus.UNCERTAIN,
        }


class ActionClassification(StrEnum):
    """Durable closed-set classification for every action request.

    New actions must be classified by the owning turn runner in the same
    transaction as the action row.  The legacy value is reserved for actions
    migrated from a pre-classification schema where mutability was not a
    durable fact; such actions may be read after recovery but never started.
    """

    MUTABLE_REQUIRED = "mutable_required"
    READ_ONLY_NOT_REQUIRED = "read_only_not_required"
    LEGACY_V3_UNATTRIBUTED = "legacy_v3_unattributed"


class ActionClassificationOrigin(StrEnum):
    """Durable provenance for an action classification row."""

    CREATED_V5 = "created_v5"
    MIGRATED_V3 = "migrated_v3"
    MIGRATED_V4_AUTHORITY = "migrated_v4_authority"
    MIGRATED_V4_READ_ONLY = "migrated_v4_read_only"


class ApprovalDecision(StrEnum):
    ALLOW_ONCE = "allow_once"
    DENY = "deny"
    CANCEL = "cancel"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    ALLOWED_ONCE = "allowed_once"
    DENIED = "denied"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class OperationStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class TerminalOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class HarnessSession:
    scope: HarnessScope
    status: SessionStatus
    revision: int
    cursor: int
    retained_from: int
    created_at: str
    updated_at: str
    # The observer cursor is bounded and may advance independently from the
    # append-only transcript truth.  These fields are the durable authority
    # for proving that transcript retention has not silently shortened the
    # conversation used for restart/model continuation.
    transcript_cursor: int = 0
    transcript_head_digest: str = ""
    protocol_version: str = "pulse-harness-kernel.v0.1"


@dataclass(frozen=True, slots=True)
class HarnessTurn:
    scope: HarnessScope
    status: TurnStatus
    revision: int
    created_at: str
    updated_at: str
    prompt: str = ""


@dataclass(frozen=True, slots=True)
class ModelRoundRecord:
    """Durable model attempt, response and provider-provenance authority."""

    round_id: str
    scope: HarnessScope
    round_index: int
    request_cursor: int
    terminal_cursor: int
    request_digest: str
    provider_evidence: Mapping[str, Any]
    provider_evidence_digest: str
    response_id: str
    provider_response_id: str | None
    status: ModelRoundStatus
    delta_count: int
    done_count: int
    response_digest: str
    assistant_item_id: str
    tool_call_count: int
    provider_usage: Mapping[str, Any] | None
    provider_usage_digest: str
    usage_evidence_class: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    scope: HarnessScope
    event_type: str
    source: str
    phase: str
    status: str
    payload: Mapping[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    item_id: str = field(default_factory=lambda: str(uuid4()))
    parent_item_id: str | None = None
    seq: int = 0
    delta_seq: int = 0
    revision: int = 0
    action_request_id: str | None = None
    approval_id: str | None = None
    operation_id: str | None = None
    redacted: bool = False
    truncated: bool = False
    gap: bool = False
    created_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "item_id": self.item_id,
            "parent_item_id": self.parent_item_id,
            "world_id": self.scope.world_id,
            "engram_id": self.scope.engram_id,
            "context_id": self.scope.context_id,
            "session_id": self.scope.session_id,
            "turn_id": self.scope.turn_id,
            "epoch": self.scope.epoch,
            "owner_id": self.scope.owner_id,
            "capability_digest": self.scope.capability_digest,
            "seq": self.seq,
            "delta_seq": self.delta_seq,
            "revision": self.revision,
            "event_type": self.event_type,
            "source": self.source,
            "phase": self.phase,
            "status": self.status,
            "action_request_id": self.action_request_id,
            "approval_id": self.approval_id,
            "operation_id": self.operation_id,
            "payload": dict(self.payload),
            "redacted": self.redacted,
            "truncated": self.truncated,
            "gap": self.gap,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ActionRecord:
    scope: HarnessScope
    action_request_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    args_digest: str
    status: ActionStatus
    revision: int
    approval_id: str | None
    operation_id: str | None
    result: Mapping[str, Any] | None
    error_code: str | None
    result_delivered: bool
    created_at: str
    updated_at: str
    terminal_at: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status.terminal


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    action_request_id: str
    scope: HarnessScope
    status: ApprovalStatus
    decision: ApprovalDecision | None
    expires_at: str
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    action_request_id: str
    scope: HarnessScope
    status: OperationStatus
    revision: int
    result: Mapping[str, Any] | None
    error_code: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ReplayPage:
    events: tuple[HarnessEvent, ...]
    next_seq: int
    retained_from: int
    gap: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    description: str
    mutable: bool = False
    requires_approval: bool = False
    capability: str = ""
    source: str = "core"


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_call_id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    status: str
    output: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    action_request_id: str | None = None
    approval_id: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    scope: HarnessScope
    status: TurnStatus
    cursor: int
    assistant_text: str = ""
    pending_action_request_id: str | None = None
    error_code: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    change_id: str
    action_request_id: str
    scope: HarnessScope
    path: str
    before_digest: str | None
    after_digest: str
    diff: str
    status: str
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_id: str
    action_request_id: str
    scope: HarnessScope
    path: str
    artifact_path: str
    content_digest: str
    status: str
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    recovery_id: str
    action_request_id: str
    scope: HarnessScope
    state: str
    reason: str
    confirmed_exit: bool
    created_at: str = field(default_factory=utc_now)
