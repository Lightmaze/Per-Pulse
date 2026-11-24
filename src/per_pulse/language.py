"""Workspace reply-language preferences at the native turn-advice boundary.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Language is a default for a future turn, never an edit to a conversation or
the transport's request body. Native advice receipts retain the choice with
that turn and prevent obsolete preferences from being replayed into another.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Any, Literal

from pulse_harness.core.errors import LeaseFenced, ScopeConflict, StoreUnavailable
from pulse_harness.core.provider import CancellationRequested, ModelRequest
from pulse_harness.core.turn_advice import TurnAdvice, TurnAdvisor, encode_advice

ResponseLanguage = Literal["auto", "en", "zh-CN"]
RESPONSE_LANGUAGES = ("auto", "en", "zh-CN")
LANGUAGE_POLICY = "per_pulse.response_language.v1"


def language_context(language: ResponseLanguage) -> str | None:
    if language == "auto":
        # The ordinary conversation remains the source of its language. No
        # extra instruction or model call is needed to keep baseline behavior.
        return None
    name = "Simplified Chinese" if language == "zh-CN" else "English"
    return (
        f"Response language preference for this turn: {name}. "
        f"Use {name} for ordinary explanations by default. "
        "An explicit language requirement in the current user task, including "
        "a translation target, takes precedence over this preference. "
        "Preserve quoted source text, code, commands, identifiers, file names, "
        "and structured data in their required form. This preference changes "
        "presentation only; it grants no action permissions."
    )


class LanguageTurnAdvisor:
    """Compose a preference with an existing advisor without replacing it.

    The Kernel invokes advice before recording its canonical model request.
    Sample only at the first model round: settings changed during a tool call
    or approval wait apply to the next turn, including after a process restart.
    Existing advisor receipts stay at their original keys for their readers.
    """

    def __init__(self, store: Any, read_language: Callable[[], ResponseLanguage],
                 previous: TurnAdvisor | None = None):
        self._store = store
        self._read_language = read_language
        self._previous = previous
        self.continues = bool(getattr(previous, "continues", False))

    def advise(self, request: ModelRequest, *, cancel_event: Event) -> TurnAdvice | None:
        # Snapshot before an existing advisor does any optional computation;
        # a setting changed while that computation runs belongs to a new turn.
        first_round = self._store.next_model_round_index(request.scope) == 0
        language = self._read_language() if first_round else "auto"
        context = language_context(language)
        try:
            previous = (
                self._previous.advise(request, cancel_event=cancel_event)
                if self._previous is not None else None
            )
            if previous is not None and not isinstance(previous, TurnAdvice):
                raise ValueError("invalid previous turn advice")
            if previous is not None:
                encode_advice(request.scope.digest, previous)
        except (CancellationRequested, LeaseFenced, ScopeConflict, StoreUnavailable):
            # Ownership, cancellation and storage failures retain their native
            # control semantics; this presentation preference cannot mask them.
            raise
        except Exception:
            # Match the native optional-advisor failure policy, preserving its
            # receipt without losing an independent local language preference.
            previous = TurnAdvice(receipt={
                "status": "skipped", "reason": "advisor_failed_or_invalid",
            })
        if context is None:
            return previous
        receipt = dict(previous.receipt) if previous is not None else {}
        receipt["per_pulse_language"] = {
            "policy": LANGUAGE_POLICY, "responseLanguage": language,
        }
        return TurnAdvice(
            context="\n\n".join(part for part in (
                previous.context if previous is not None else None, context,
            ) if part),
            receipt=receipt,
        )


def install_language_advisor(kernel: Any, read_language: Callable[[], ResponseLanguage]) -> None:
    """Bind before the runtime starts, retaining the native advisor factory.

    Per Pulse uses this existing host seam, shared with expert/posture advice.
    The transport, response reviewer, tool catalog, cancellation and settlement
    remain owned by the same Kernel. No sessions have run at this point.
    """
    previous_factory = kernel._turn_advisor_factory

    def factory(store, adapter, scope):
        previous = previous_factory(store, adapter, scope) if previous_factory is not None else None
        return LanguageTurnAdvisor(store, read_language, previous)

    kernel._turn_advisor_factory = factory
