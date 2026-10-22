"""Control-plane commands; transport adapters may project these to HTTP/SSE."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4

from .scope import HarnessScope


class ControlKind(StrEnum):
    INTERRUPT = "interrupt"
    ABORT = "abort"
    STEER = "steer"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class ControlCommand:
    kind: ControlKind
    scope: HarnessScope
    command_id: str = field(default_factory=lambda: str(uuid4()))
    payload: Mapping[str, Any] = field(default_factory=dict)
    expected_revision: int | None = None


@dataclass(frozen=True, slots=True)
class ControlResult:
    command_id: str
    accepted: bool
    status: str
    error_code: str | None = None
