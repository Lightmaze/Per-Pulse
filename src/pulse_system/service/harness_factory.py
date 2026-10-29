"""Pulse's narrow Harness factory seam.

The service selects an explicit adapter here and keeps the shared
PulseWorld/Engram lifecycle outside the independent Harness core.  Runtime
bootstrapping is provider-neutral: Pi constructor details are packed into an
adapter-owned configuration namespace and unpacked only by the Pi adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pulse_harness.adapters.pi import PiCompatibilityAdapter
from pulse_harness.core.lifecycle import (
    HarnessHandle,
    HarnessRuntimeBootstrap,
    HarnessRuntimePort,
)
from pulse_harness.core.scope import HarnessScope
from pulse_system.agent.harness.pi import PiHarnessRuntime


class UnsupportedHarnessKind(ValueError):
    """The operator selected an adapter with no registered implementation."""

    def __init__(self, kind: object) -> None:
        self.kind = kind
        super().__init__(f"unsupported Harness kind: {kind!r}")


class _HarnessFactoryCapability:
    """Process-local factory capability with no serializable representation."""

    __slots__ = ("_nonce",)

    def __init__(self) -> None:
        self._nonce = object()

    def __reduce__(self) -> object:  # pragma: no cover - defensive boundary
        raise TypeError("Harness factory capabilities are process-local")


@dataclass(frozen=True, slots=True)
class _RuntimeAuthority:
    runtime: object
    factory: object
    capability: object
    owner_id: str
    epoch: int


# A marker on a Python object is useful for observation but is not an
# authority.  The registry stores the exact object identities produced by a
# factory for the lifetime of this process.  It is intentionally not
# persisted or exposed as a capability issuer.  This is an in-process trust
# boundary, not a claim against a local administrator with reflection access.
_PI_RUNTIME_AUTHORITIES: list[_RuntimeAuthority] = []


def _register_pi_runtime_authority(
    runtime: object,
    factory: object,
    capability: object,
    *,
    owner_id: str,
    epoch: int,
) -> None:
    for existing in _PI_RUNTIME_AUTHORITIES:
        if existing.runtime is runtime:
            if (
                existing.factory is not factory
                or existing.capability is not capability
                or existing.owner_id != owner_id
                or existing.epoch != epoch
            ):
                raise RuntimeError("Pi runtime authority identity is already bound")
            return
    _PI_RUNTIME_AUTHORITIES.append(
        _RuntimeAuthority(runtime, factory, capability, owner_id, epoch)
    )


def has_pi_runtime_authority(
    runtime: object,
    *,
    factory: object | None = None,
    capability: object | None = None,
    owner_id: str | None = None,
    epoch: int | None = None,
) -> bool:
    """Check the process-local factory/runtime/capability identity binding."""

    # A Pi recovery/convergence decision is never allowed to infer authority
    # from the concrete runtime, its marker, or a partial registry lookup.
    # Every caller must carry the complete bootstrap tuple.  The registry is
    # an in-process trust boundary, not a tamper-proof claim against a local
    # administrator with reflection access.
    if (
        factory is None
        or capability is None
        or owner_id is None
        or type(epoch) is not int
    ):
        return False

    for authority in _PI_RUNTIME_AUTHORITIES:
        if authority.runtime is not runtime:
            continue
        if authority.factory is not factory:
            continue
        if authority.capability is not capability:
            continue
        if authority.owner_id != owner_id:
            continue
        if authority.epoch != epoch:
            continue
        return True
    return False


class PulseHarnessFactory:
    """Provider-neutral Runtime factory over one shared Pi adapter.

    ``create`` and ``wake`` are the public per-session surface.  Runtime
    bootstrap is a separate protocol and returns a capability-bearing result;
    RuntimeService consumes that protocol rather than a Pi constructor.
    """

    _ADAPTERS = frozenset({"pi"})

    def __init__(
        self,
        kind: str = "pi",
        *,
        pi_runtime_factory: Callable[..., HarnessRuntimePort] | None = None,
        owner_id: str | None = None,
        epoch: int | None = None,
    ) -> None:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("Harness kind must be a non-empty string")
        kind = kind.strip().lower()
        if kind not in self._ADAPTERS:
            raise UnsupportedHarnessKind(kind)
        if (owner_id is None) != (epoch is None):
            raise ValueError("expected owner and epoch must appear together")
        if owner_id is not None and (
            not isinstance(owner_id, str)
            or not owner_id.strip()
            or type(epoch) is not int
            or epoch < 0
        ):
            raise ValueError("expected owner and epoch must be valid")
        self.kind = kind
        adapter_kwargs: dict[str, Any] = {}
        if pi_runtime_factory is not None:
            adapter_kwargs["runtime_factory"] = pi_runtime_factory
        adapter_kwargs["expected_owner_id"] = owner_id
        adapter_kwargs["expected_epoch"] = epoch
        self._adapter = PiCompatibilityAdapter(**adapter_kwargs)
        self._capability = _HarnessFactoryCapability()
        self._bootstrap: HarnessRuntimeBootstrap | None = None
        self._expected_owner_id = owner_id
        self._expected_epoch = epoch

    def bootstrap(
        self,
        *,
        workspace: object,
        config: Mapping[str, Any],
        owner_id: str,
        epoch: int,
    ) -> HarnessRuntimeBootstrap:
        """Bootstrap one shared host runtime under the publication fence."""

        if not isinstance(config, Mapping):
            raise TypeError("Harness bootstrap config must be a mapping")
        self._check_bootstrap_fence(owner_id, epoch)
        if self._bootstrap is not None:
            raise RuntimeError("one Harness factory cannot replace its Runtime")
        runtime = self._adapter.bootstrap(workspace=workspace, config=config)
        result = HarnessRuntimeBootstrap(
            runtime=runtime,
            kind=self.kind,
            capability=self._capability,
            owner_id=owner_id,
            epoch=epoch,
        )
        _register_pi_runtime_authority(
            runtime,
            self,
            self._capability,
            owner_id=owner_id,
            epoch=epoch,
        )
        self._bootstrap = result
        return result

    def create(
        self,
        scope: HarnessScope,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        return self._adapter.create(scope, config)

    def wake(
        self,
        scope: HarnessScope,
        session_id: str,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        return self._adapter.wake(scope, session_id, config)

    def _check_bootstrap_fence(self, owner_id: str, epoch: int) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("bootstrap owner_id must be non-empty")
        if type(epoch) is not int or epoch < 0:
            raise ValueError("bootstrap epoch must be non-negative")
        if self._expected_owner_id is not None and (
            owner_id != self._expected_owner_id or epoch != self._expected_epoch
        ):
            raise RuntimeError("Harness bootstrap owner or epoch is stale")


class LegacyHarnessRuntimeFactory:
    """Explicit compatibility wrapper for historical injected callables.

    This wrapper is constructed only for a caller that explicitly injects a
    legacy callable into RuntimeService.  The callable is never consumed
    directly by RuntimeService: owner/epoch are checked here, one opaque
    capability is minted here, and the result is returned through the same
    ``HarnessRuntimeBootstrap`` ABI.  New production code must implement
    ``HarnessRuntimeFactory`` instead.
    """

    def __init__(
        self,
        legacy_factory: Callable[..., HarnessRuntimePort],
        *,
        legacy_config: Mapping[str, Any],
        owner_id: str,
        epoch: int,
    ) -> None:
        if not callable(legacy_factory):
            raise TypeError("legacy Harness factory must be callable")
        self._legacy_factory = legacy_factory
        self._legacy_config = dict(legacy_config)
        self._owner_id = owner_id
        self._epoch = epoch
        self._capability = _HarnessFactoryCapability()
        self._bootstrap: HarnessRuntimeBootstrap | None = None
        self._session_adapter: PiCompatibilityAdapter | None = None
        self.kind = "legacy"

    def bootstrap(
        self,
        *,
        workspace: object,
        config: Mapping[str, Any],
        owner_id: str,
        epoch: int,
    ) -> HarnessRuntimeBootstrap:
        del config
        if owner_id != self._owner_id or epoch != self._epoch:
            raise RuntimeError("legacy Harness bootstrap owner or epoch is stale")
        if self._bootstrap is not None:
            raise RuntimeError("one legacy Harness factory cannot replace its Runtime")
        runtime = self._legacy_factory(workspace, **self._legacy_config)
        if not callable(getattr(runtime, "preflight", None)) or not callable(
            getattr(runtime, "close", None)
        ):
            raise TypeError(
                "legacy Harness factory did not return a bootstrap-compatible runtime"
            )
        # The marker is not a credential.  Historical Pi callables are
        # admitted as Pi only when they return the canonical concrete Pi
        # runtime; a foreign object that merely copies the marker remains
        # generic and cannot enter the Pi recovery registry.
        if type(runtime) is PiHarnessRuntime and callable(
            getattr(runtime, "binding_snapshot", None)
        ):
            self.kind = "pi"
            session_adapter = PiCompatibilityAdapter(
                runtime_factory=lambda _workspace, **_kwargs: runtime,
                expected_owner_id=owner_id,
                expected_epoch=epoch,
            )
            session_adapter.bootstrap(
                workspace=workspace,
                config={"adapter": {"pi": {}}},
            )
            self._session_adapter = session_adapter
            _register_pi_runtime_authority(
                runtime,
                self,
                self._capability,
                owner_id=owner_id,
                epoch=epoch,
            )
        result = HarnessRuntimeBootstrap(
            runtime=runtime,
            kind=self.kind,
            capability=self._capability,
            owner_id=owner_id,
            epoch=epoch,
        )
        self._bootstrap = result
        return result

    def create(
        self,
        scope: HarnessScope,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        adapter = self._session_adapter
        if adapter is None:
            raise RuntimeError(
                "legacy Harness factory has no Pi session create capability"
            )
        return adapter.create(scope, config)

    def wake(
        self,
        scope: HarnessScope,
        session_id: str,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        adapter = self._session_adapter
        if adapter is None:
            raise RuntimeError(
                "legacy Harness factory has no Pi session wake capability"
            )
        return adapter.wake(scope, session_id, config)


__all__ = [
    "LegacyHarnessRuntimeFactory",
    "PulseHarnessFactory",
    "UnsupportedHarnessKind",
    "has_pi_runtime_authority",
]
