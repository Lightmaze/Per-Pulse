"""Cooperative resource exclusion for workspace effects in one host process.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The runtime's publication fence controls who may act; this lock controls which
participating writer may touch a shared workspace at a time. It is not an OS
sandbox, a cross-process lock, or a guarantee against external file writers.
Those writers remain subject only to the adapter's digest/identity checks.
"""

from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import threading
import time
from typing import Iterator
from weakref import WeakValueDictionary

from ..core.errors import HarnessError
from ..core.provider import CancellationToken


class WorkspaceResourceBusy(HarnessError):
    """No workspace effect started before the bounded resource wait elapsed."""

    code = "workspace_resource_busy"


class WorkspaceResourceUnavailable(HarnessError):
    """A previous command may still be writing until cleanup is confirmed."""

    code = "workspace_resource_unavailable"


class WorkspaceWriteResource:
    """One exclusive effect lane shared by providers with the same root."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._unresolved_commands: set[str] = set()

    @property
    def unresolved_commands(self) -> tuple[str, ...]:
        with self._state_lock:
            return tuple(sorted(self._unresolved_commands))

    def quarantine(self, invocation_id: str) -> None:
        """Fence new effects when an owned command's cleanup is unconfirmed.

        The caller still owns the writer lane while recording this condition.
        Keeping a strong reference prevents a replacement provider from losing
        this fence merely because all previous providers were collected.
        """
        if not isinstance(invocation_id, str) or not invocation_id:
            raise ValueError("an unresolved command requires its invocation ID")
        with _REGISTRY_LOCK:
            with self._state_lock:
                self._unresolved_commands.add(invocation_id)
                _QUARANTINED_RESOURCES[id(self)] = self

    def confirm_cleanup(self, invocation_id: str) -> None:
        """Remove only a command whose owning executor confirmed it stopped."""
        with _REGISTRY_LOCK:
            with self._state_lock:
                self._unresolved_commands.discard(invocation_id)
                if not self._unresolved_commands:
                    _QUARANTINED_RESOURCES.pop(id(self), None)

    def _require_available(self) -> None:
        with self._state_lock:
            if self._unresolved_commands:
                raise WorkspaceResourceUnavailable(
                    "workspace has unconfirmed command cleanup; writes require confirmed cleanup before retry"
                )

    @contextmanager
    def write(
        self,
        cancellation: CancellationToken | None = None,
        *,
        timeout: float = 30.0,
    ) -> Iterator[None]:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("workspace resource timeout must be finite and positive")
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while not acquired:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                self._require_available()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkspaceResourceBusy("workspace is busy; no file effect was started")
                acquired = self._lock.acquire(timeout=min(0.05, remaining))
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            self._require_available()
            yield
        finally:
            if acquired:
                self._lock.release()


_RESOURCES: WeakValueDictionary[tuple[str, int, int], WorkspaceWriteResource] = WeakValueDictionary()
_REGISTRY_LOCK = threading.Lock()
# Only unresolved external writers require retention across provider lifetimes.
# Confirmed cleanup removes this reference; ordinary resources remain weak.
_QUARANTINED_RESOURCES: dict[int, WorkspaceWriteResource] = {}


def workspace_write_resource(root: Path) -> WorkspaceWriteResource:
    """Resolve a stable root identity without using changing directory ctime.

    Providers keep the returned resource alive. The weak registry does not
    retain every temporary workspace a long-lived service has ever opened.
    """

    canonical = root.resolve(strict=True)
    info = canonical.stat()
    key = (os.path.normcase(str(canonical)), int(info.st_dev), int(info.st_ino))
    with _REGISTRY_LOCK:
        resource = _RESOURCES.get(key)
        if resource is None:
            resource = WorkspaceWriteResource()
            _RESOURCES[key] = resource
        return resource
