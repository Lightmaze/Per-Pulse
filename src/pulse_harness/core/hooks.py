"""Typed, bounded hook decisions for the independent Harness loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from queue import Queue
from threading import Thread
from typing import Callable, Iterable

from .contracts import ToolCall
from .errors import HarnessError
from .scope import HarnessScope


class HookStage(StrEnum):
    BEFORE_MODEL = "before_model"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    AFTER_MODEL = "after_model"


class HookDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class HookTrust(StrEnum):
    """Execution trust advertised by the v0.1 hook surface."""

    TRUSTED_IN_PROCESS_ADVISORY = "trusted_in_process_advisory"


class HookRejected(HarnessError):
    code = "hook_rejected"


class HookTimeout(HookRejected):
    """The Runner stopped waiting; the in-process hook may still be running."""

    code = "hook_timeout"


@dataclass(frozen=True, slots=True)
class HookContext:
    """Read-only decision input; it contains no world or capability mutator."""

    stage: HookStage
    scope: HarnessScope
    turn_revision: int
    transcript_cursor: int
    tool_call: ToolCall | None = None
    action_request_id: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class HookResult:
    decision: HookDecision
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.decision, HookDecision):
            raise ValueError("hook decision must be HookDecision")


Hook = Callable[[HookContext], HookResult]


def _invoke_bounded(hook: Hook, context: HookContext, timeout_sec: float) -> HookResult:
    """Bound caller wait only; Python cannot terminate an arbitrary hook thread.

    This is deliberately not a security or isolation boundary.  Hook authors
    are trusted in-process code.  Untrusted/process-isolated hooks require a
    separate, terminating executor, which is not part of this alpha.
    """

    result_queue: Queue[tuple[str, object]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            result_queue.put(("ok", hook(context)))
        except BaseException as exc:  # re-raised on the caller thread
            result_queue.put(("error", exc))

    thread = Thread(target=invoke, name="pulse-harness-hook", daemon=True)
    thread.start()
    thread.join(timeout_sec)
    if thread.is_alive():
        raise HookTimeout("hook decision exceeded its bounded deadline")
    try:
        kind, value = result_queue.get_nowait()
    except Exception as exc:  # pragma: no cover - defensive thread boundary
        raise HookRejected("hook returned no decision") from exc
    if kind == "error":
        raise HookRejected("hook raised an exception") from value  # type: ignore[arg-type]
    if not isinstance(value, HookResult):
        raise HookRejected("hook returned an invalid typed decision")
    return value


class HookSet:
    """Trusted in-process advisory hooks for the v0.1 Runner.

    The typed context exposes no store/model mutator, but Python callables may
    retain outside references.  ``timeout_sec`` bounds how long the Runner
    waits for a decision; it does not terminate the hook or prove that a late
    side effect stopped. Process-isolated hooks are outside this alpha boundary.
    """

    trust_model = HookTrust.TRUSTED_IN_PROCESS_ADVISORY

    def __init__(
        self,
        *,
        before_model: Iterable[Hook] = (),
        before_tool: Iterable[Hook] = (),
        after_tool: Iterable[Hook] = (),
        after_model: Iterable[Hook] = (),
        timeout_sec: float = 0.25,
    ) -> None:
        if timeout_sec <= 0:
            raise ValueError("hook timeout must be positive")
        self.timeout_sec = float(timeout_sec)
        self._hooks = {
            HookStage.BEFORE_MODEL: tuple(before_model),
            HookStage.BEFORE_TOOL: tuple(before_tool),
            HookStage.AFTER_TOOL: tuple(after_tool),
            HookStage.AFTER_MODEL: tuple(after_model),
        }

    def evaluate(self, context: HookContext) -> HookResult:
        for hook in self._hooks[context.stage]:
            result = _invoke_bounded(hook, context, self.timeout_sec)
            if result.decision is HookDecision.DENY:
                return result
        return HookResult(HookDecision.ALLOW)
