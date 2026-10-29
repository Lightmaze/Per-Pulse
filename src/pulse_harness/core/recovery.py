"""Restart recovery: keep pending approvals, fence unknown effects."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import RecoveryRecord
from .session_store import HarnessStore


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    records: tuple[RecoveryRecord, ...]
    pending_approval_count: int


class RecoveryManager:
    def __init__(self, store: HarnessStore) -> None:
        self.store = store

    def recover(self, *, reason: str = "process_restart") -> RecoveryReport:
        records = self.store.recover_inflight(reason=reason)
        # Pending approvals intentionally remain pending and resumable.  The
        # count is global to this store; observers can then show what still
        # needs a human decision without pretending an effect was replayed.
        pending = self.store.pending_approval_count()
        return RecoveryReport(records=records, pending_approval_count=pending)
