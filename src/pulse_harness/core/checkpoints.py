"""Checkpoint contracts independent of any filesystem or git backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import CheckpointRecord
from .scope import HarnessScope


class CheckpointBackend(Protocol):
    def create(self, scope: HarnessScope, action_request_id: str, path: str) -> CheckpointRecord:
        ...

    def restore(self, scope: HarnessScope, checkpoint_id: str) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class CheckpointEvidence:
    checkpoint: CheckpointRecord
    restorable: bool
