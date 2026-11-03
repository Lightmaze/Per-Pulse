"""Fit a long conversation into the model's context without editing history.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Every model round sends a session's transcript. A long-lived page would
eventually exceed any model's context window, and from then on every message
in it would fail. This projection keeps the page's opening message and the
most recent whole turns that fit an estimated budget, and says that earlier
messages can be looked up with the history tools. The durable transcript is
never changed.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

# A deliberately generous estimate: English averages about four UTF-8 bytes
# per token and CJK text about three, so this rarely undercounts.
BYTES_PER_TOKEN = 3
MESSAGE_OVERHEAD_TOKENS = 4

OMITTED_NOTE = (
    "Earlier messages in this conversation are left out here to fit the model's "
    "context. When you need them, look them up with search_history or read_history."
)


def estimate_tokens(message: Mapping[str, Any]) -> int:
    content = message.get("content")
    size = len(content.encode("utf-8")) if isinstance(content, str) else 0
    return size // BYTES_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS


def _final_assistant(message: Mapping[str, Any]) -> bool:
    """An assistant message that ends a turn (no tool calls follow it)."""
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    try:
        payload = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return True
    return not (isinstance(payload, dict) and set(payload) == {"text", "tool_calls"}
                and payload.get("tool_calls"))


def _boundaries(messages: Sequence[Mapping[str, Any]]) -> list[int]:
    """Indices where a window may start without splitting a tool exchange."""
    starts = []
    for index, message in enumerate(messages):
        if index == 0 or message.get("role") == "tool":
            continue
        if message.get("role") == "user" or _final_assistant(messages[index - 1]):
            starts.append(index)
    return starts


def fit_context(messages: Sequence[Mapping[str, Any]], max_tokens: int) -> tuple[Mapping[str, Any], ...]:
    """Return ``messages`` or a shorter projection whose estimate fits ``max_tokens``.

    The first message is kept when it is the conversation's opening user
    message. The window always starts at a turn or user-message boundary, so
    an assistant tool call is never separated from its results. If even the
    latest turn is larger than the budget, the projection starts at that turn;
    nothing inside the current exchange is dropped.
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    messages = tuple(messages)
    total = sum(estimate_tokens(message) for message in messages)
    if total <= max_tokens:
        return messages
    starts = _boundaries(messages)
    if not starts:
        return messages
    pinned = (messages[0],) if messages[0].get("role") == "user" else ()
    note = {"role": "system", "content": OMITTED_NOTE, "item_id": "context-window:omitted"}
    fixed = sum(estimate_tokens(message) for message in pinned) + estimate_tokens(note)
    suffix = [0] * (len(messages) + 1)
    for index in range(len(messages) - 1, -1, -1):
        suffix[index] = suffix[index + 1] + estimate_tokens(messages[index])
    start = next((index for index in starts if fixed + suffix[index] <= max_tokens), starts[-1])
    if start <= len(pinned):
        return messages
    return (note, *pinned, *messages[start:])
