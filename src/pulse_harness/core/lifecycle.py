"""Provider-neutral lifecycle ports for Pulse-owned Harness adapters.

The kernel owns session and turn truth.  A host runtime may select a physical
adapter (Pi today, another adapter later) without making that adapter's wire
format part of ``pulse_harness.core``.  The lifecycle factory is deliberately
small: it carries scope and recovery evidence, but it does not create a world,
Engram, Purpose, succession, or scheduler.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from .scope import HarnessScope


class HarnessKind(StrEnum):
    """Explicit adapter kinds understood by a lifecycle factory."""

    PI = "pi"


@dataclass(frozen=True, slots=True)
class HarnessSessionRef:
    """Opaque physical session reference returned by create/wake.

    The reference is not an Engram and carries no world lifecycle authority.
    ``recovery_evidence`` is a bounded, adapter-neutral observation used by a
    caller to decide whether it may continue; it is never a provider prompt.
    """

    scope: HarnessScope
    session_id: str
    cursor: int = 0
    recovery_evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.scope, HarnessScope):
            raise TypeError("scope must be a HarnessScope")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if type(self.cursor) is not int or self.cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        if not isinstance(self.recovery_evidence, Mapping):
            raise TypeError("recovery_evidence must be a mapping")
        object.__setattr__(self, "recovery_evidence", dict(self.recovery_evidence))


@runtime_checkable
class HarnessHandle(Protocol):
    """Opaque adapter handle for one durable Harness session."""

    @property
    def adapter_kind(self) -> str:
        ...

    @property
    def session_ref(self) -> HarnessSessionRef:
        ...

    def run_turn(
        self,
        prompt: str,
        *,
        timeout_sec: float | None = None,
        turn_id: str | None = None,
    ) -> Any:
        ...

    def snapshot(self) -> Mapping[str, Any]:
        ...

    def abort(self) -> None:
        ...

    def steer(self, content: str) -> None:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class HarnessRuntimePort(Protocol):
    """Provider-neutral host holder consumed by Pulse L3.

    This is the narrow world-level compatibility port required by the
    existing Pulse lifecycle.  It deliberately describes lifecycle and
    control operations only; Pi RPC/JSONL, session files, and extension
    objects are not part of this ABI.  A factory may expose this port as its
    host bootstrap result while ``create``/``wake`` remain the canonical
    session-handle surface.
    """

    def preflight(self) -> None:
        ...

    def run_turn(
        self,
        engram_id: str,
        prompt: str,
        *,
        timeout_sec: float | None = None,
        bootstrap_text: str | None = None,
        turn_id: str | None = None,
    ) -> Any:
        ...

    def snapshot(self, engram_id: str) -> Mapping[str, Any]:
        ...

    def abort(self, engram_id: str) -> None:
        ...

    def steer(self, engram_id: str, content: str) -> None:
        ...

    def succeed(
        self,
        old_engram_id: str,
        new_engram_id: str,
        *,
        capacity_timeout_sec: float | None = None,
    ) -> None:
        ...

    def close_session(self, engram_id: str) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class HarnessRuntimeBootstrap:
    """The result of bootstrapping one host-level Harness runtime.

    ``capability`` is intentionally an opaque process-local value.  The core
    does not interpret it or serialize it; a host adapter may use identity
    checks to prove that the runtime came from the factory that owns it.  The
    bootstrap result is separate from ``HarnessHandle`` because one Pulse
    world owns one host runtime while sessions remain per-Engram handles.
    """

    runtime: HarnessRuntimePort
    kind: str
    capability: object
    owner_id: str
    epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("bootstrap kind must be a non-empty string")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("bootstrap owner_id must be a non-empty string")
        if type(self.epoch) is not int or self.epoch < 0:
            raise ValueError("bootstrap epoch must be a non-negative integer")


@runtime_checkable
class HarnessRuntimeFactory(Protocol):
    """Provider-neutral host runtime bootstrap protocol.

    RuntimeService consumes this protocol only.  Adapter-specific constructor
    kwargs belong inside the selected adapter's ``config`` namespace and are
    never part of this ABI.
    """

    @property
    def kind(self) -> str:
        ...

    def bootstrap(
        self,
        *,
        workspace: object,
        config: Mapping[str, Any],
        owner_id: str,
        epoch: int,
    ) -> HarnessRuntimeBootstrap:
        ...


@runtime_checkable
class HarnessFactory(Protocol):
    """Create or wake a session without owning Pulse L3 lifecycle."""

    @property
    def kind(self) -> str:
        ...

    def create(
        self,
        scope: HarnessScope,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        ...

    def wake(
        self,
        scope: HarnessScope,
        session_id: str,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        ...
