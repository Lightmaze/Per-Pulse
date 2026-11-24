"""Temporary worker ABI, intentionally separate from durable Engram identity."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol

from .scope import HarnessScope


class WorkerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    worker_id: str
    parent_scope: HarnessScope
    prompt: str
    deadline_epoch_ms: int
    quota: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class WorkerResult:
    worker_id: str
    status: WorkerStatus
    output: Mapping[str, object]
    evidence: Mapping[str, object]


class WorkerBackend(Protocol):
    def spawn(self, request: WorkerRequest) -> WorkerResult:
        ...

    def wait(self, worker_id: str) -> WorkerResult:
        ...

    def cancel(self, worker_id: str) -> WorkerResult:
        ...
