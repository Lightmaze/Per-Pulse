"""Durable transcript view over canonical session events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .session_store import HarnessStore
from .scope import HarnessScope


@dataclass(frozen=True, slots=True)
class TranscriptMessage:
    message_id: str
    scope: HarnessScope
    role: str
    content: str
    item_id: str
    event_id: str
    seq: int
    transcript_seq: int = 0
    parent_item_id: str | None = None
    redacted: bool = False
    truncated: bool = False


class TranscriptStore:
    """Append-only message API backed by durable transcript truth and events."""

    def __init__(self, store: HarnessStore) -> None:
        self.store = store

    def append(
        self,
        scope: HarnessScope,
        *,
        role: str,
        content: str,
        item_id: str | None = None,
        parent_item_id: str | None = None,
    ) -> TranscriptMessage:
        if role not in {"system", "user", "assistant", "tool", "reasoning"}:
            raise ValueError("unsupported transcript role")
        if not isinstance(content, str):
            raise ValueError("transcript content must be text")
        row = self.store.append_transcript(
            scope,
            role=role,
            content=content,
            item_id=item_id or str(uuid4()),
            parent_item_id=parent_item_id,
        )
        return self._message(row)

    def messages(self, scope: HarnessScope) -> tuple[TranscriptMessage, ...]:
        return tuple(self._message(row) for row in self.store.transcript_rows(scope))

    @staticmethod
    def _message(row: dict[str, Any]) -> TranscriptMessage:
        return TranscriptMessage(
            message_id=str(row["message_id"]),
            scope=row["scope"],
            role=str(row["role"]),
            content=str(row["content"]),
            item_id=str(row["item_id"]),
            event_id=str(row["event_id"]),
            seq=int(row["seq"]),
            transcript_seq=int(row["transcript_seq"]),
            parent_item_id=row["parent_item_id"],
            redacted=bool(row["redacted"]),
            truncated=bool(row["truncated"]),
        )
