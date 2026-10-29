"""Canonical event drafts and row conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from .contracts import HarnessEvent, utc_now
from .replay import decode_payload
from .scope import HarnessScope


@dataclass(frozen=True, slots=True)
class EventDraft:
    scope: HarnessScope
    event_type: str
    source: str
    phase: str
    status: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    item_id: str = field(default_factory=lambda: str(uuid4()))
    parent_item_id: str | None = None
    delta_seq: int = 0
    revision: int = 0
    action_request_id: str | None = None
    approval_id: str | None = None
    operation_id: str | None = None
    gap: bool = False
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("event_type must be non-empty")
        if not self.source.strip():
            raise ValueError("source must be non-empty")
        if not self.phase.strip():
            raise ValueError("phase must be non-empty")
        if self.delta_seq < 0 or self.revision < 0:
            raise ValueError("delta_seq and revision must be non-negative")


def event_from_row(row: Any) -> HarnessEvent:
    scope = HarnessScope(
        world_id=row["world_id"],
        engram_id=row["engram_id"],
        context_id=row["context_id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        epoch=int(row["epoch"]),
        owner_id=row["owner_id"],
        capability_digest=row["capability_digest"],
    )
    return HarnessEvent(
        scope=scope,
        event_type=row["event_type"],
        source=row["source"],
        phase=row["phase"],
        status=row["status"],
        payload=decode_payload(row["payload_json"]),
        event_id=row["event_id"],
        item_id=row["item_id"],
        parent_item_id=row["parent_item_id"],
        seq=int(row["seq"]),
        delta_seq=int(row["delta_seq"]),
        revision=int(row["revision"]),
        action_request_id=row["action_request_id"],
        approval_id=row["approval_id"],
        operation_id=row["operation_id"],
        redacted=bool(row["redacted"]),
        truncated=bool(row["truncated"]),
        gap=bool(row["gap"]),
        created_at=row["created_at"],
    )
