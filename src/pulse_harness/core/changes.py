"""Structured change contracts; no shell or path concatenation belongs here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .contracts import ChangeRecord


@dataclass(frozen=True, slots=True)
class ChangePreview:
    change: ChangeRecord
    applied: bool = False


@dataclass(frozen=True, slots=True)
class ChangeApplyResult:
    change: ChangeRecord
    checkpoint_id: str
    applied: bool
    metadata: Mapping[str, str]
