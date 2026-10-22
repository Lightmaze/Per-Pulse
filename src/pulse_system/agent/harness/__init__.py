"""Persistent Harness lifecycle contracts and the Pi implementation."""

from .base import (
    BINDING_COMPONENT,
    BINDING_VERSION,
    BindingState,
    HarnessError,
    HarnessRuntime,
    HarnessSession,
    HarnessState,
    HarnessTurnResult,
    SessionBinding,
)
from .actions import HarnessActionBroker, HarnessActionError, MUTABLE_PI_TOOL_NAMES
from .sandbox import CodexCliSandboxBackend, SandboxLiveGate, SandboxPreflight
from .terminal_sessions import (
    RUNTIME_LEASE_SCOPE,
    ReconciliationState,
    SessionProjectionState,
    TerminalOutputChunk,
    TerminalOutputGap,
    TerminalOutputPage,
    TerminalSessionCapabilities,
    TerminalSessionConflictError,
    TerminalSessionError,
    TerminalSessionLeaseError,
    TerminalSessionList,
    TerminalSessionMode,
    TerminalSessionNotFoundError,
    TerminalSessionScope,
    TerminalSessionService,
    TerminalSessionStartError,
    TerminalSessionStore,
    TerminalSessionSummary,
    TerminalSessionTransport,
    TerminalStopResult,
    TreeContainment,
    deterministic_output_event_id,
    deterministic_recovery_event_id,
    deterministic_start_event_id,
    deterministic_terminal_event_id,
)
# Pi is an optional compatibility adapter.  Keep it lazy so importing the
# provider-neutral Harness contracts, or any non-Pi Pulse service, does not
# execute Pi RPC/JSONL/extension imports as a package side effect.  Existing
# ``from pulse_system.agent.harness import PiHarnessRuntime`` callers keep
# their compatibility surface through ``__getattr__`` below.
_LAZY_PI_EXPORTS = frozenset({
    "PiHarnessRuntime",
    "PiProcessContext",
    "PiSession",
    "merge_pi_settings",
    "pulse_extension_asset",
})


def __getattr__(name: str):
    if name not in _LAZY_PI_EXPORTS:
        raise AttributeError(name)
    from . import pi as _pi

    value = getattr(_pi, name)
    globals()[name] = value
    return value

__all__ = [
    "BINDING_COMPONENT",
    "BINDING_VERSION",
    "HarnessActionBroker",
    "HarnessActionError",
    "BindingState",
    "HarnessError",
    "HarnessRuntime",
    "HarnessSession",
    "HarnessState",
    "HarnessTurnResult",
    "PiHarnessRuntime",
    "PiProcessContext",
    "PiSession",
    "SessionBinding",
    "MUTABLE_PI_TOOL_NAMES",
    "CodexCliSandboxBackend",
    "SandboxLiveGate",
    "SandboxPreflight",
    "RUNTIME_LEASE_SCOPE",
    "ReconciliationState",
    "SessionProjectionState",
    "TerminalOutputChunk",
    "TerminalOutputGap",
    "TerminalOutputPage",
    "TerminalSessionCapabilities",
    "TerminalSessionConflictError",
    "TerminalSessionError",
    "TerminalSessionLeaseError",
    "TerminalSessionList",
    "TerminalSessionMode",
    "TerminalSessionNotFoundError",
    "TerminalSessionScope",
    "TerminalSessionService",
    "TerminalSessionStartError",
    "TerminalSessionStore",
    "TerminalSessionSummary",
    "TerminalSessionTransport",
    "TerminalStopResult",
    "TreeContainment",
    "deterministic_output_event_id",
    "deterministic_recovery_event_id",
    "deterministic_start_event_id",
    "deterministic_terminal_event_id",
    "merge_pi_settings",
    "pulse_extension_asset",
]
