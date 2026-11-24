"""Agent execution surfaces.

The Pi implementation remains a compatibility adapter.  Keep its import lazy
so provider-neutral Harness contracts and non-Pi modules do not execute Pi's
RPC/JSONL stack as a package side effect.  The historical explicit Pi exports
remain available through ``__getattr__``.
"""

from .harness import (
    BindingState,
    HarnessError,
    HarnessRuntime,
    HarnessSession,
    HarnessState,
    HarnessTurnResult,
    SessionBinding,
)

_LAZY_PI_EXPORTS = frozenset({"PiHarnessRuntime", "PiSession"})


def __getattr__(name: str):
    if name not in _LAZY_PI_EXPORTS:
        raise AttributeError(name)
    from .harness import PiHarnessRuntime, PiSession

    value = {"PiHarnessRuntime": PiHarnessRuntime, "PiSession": PiSession}[name]
    globals()[name] = value
    return value

__all__ = [
    "BindingState",
    "HarnessError",
    "HarnessRuntime",
    "HarnessSession",
    "HarnessState",
    "HarnessTurnResult",
    "PiHarnessRuntime",
    "PiSession",
    "SessionBinding",
]
