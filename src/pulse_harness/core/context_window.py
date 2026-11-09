"""Fit a long conversation into the model's context without editing history.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Every model round sends a session's transcript. A long-lived page would
eventually exceed any model's context window, and from then on every message
in it would fail. This projection keeps the most recent whole turns and, when
it fits beside them, the page's opening message. It says that earlier
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
    # This host-only count comes from the completed model round. A textual
    # JSON example must not turn separate replies into one endless exchange.
    return not message.get("tool_call_count", 0)


def _boundaries(messages: Sequence[Mapping[str, Any]]) -> list[int]:
    """Indices where a window may start without splitting a tool exchange."""
    starts = []
    for index, message in enumerate(messages):
        if index == 0 or message.get("role") == "tool":
            continue
        if message.get("role") == "user" or _final_assistant(messages[index - 1]):
            starts.append(index)
    return starts



def _project_saved_tool_result(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Keep a tool's terminal identity while paging its saved output on demand.

    Only the host's durable message_id makes the record addressable. Do not
    parse a tool's prose for an identifier or truncate a current user message.
    """
    if message.get("role") != "tool":
        return None
    message_id = message.get("message_id")
    content = message.get("content")
    if not isinstance(message_id, str) or not message_id or not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_call_id"), str) or not isinstance(payload.get("status"), str):
        return None
    reference = {
        "tool_call_id": payload["tool_call_id"],
        "action_request_id": payload.get("action_request_id"),
        "status": payload["status"],
        "output": {
            "context_window": {
                "state": "saved_result_available_by_page",
                "message_id": message_id,
                "original_chars": len(content),
                "next_step": "Call read_history with role='tool', this message_id, offset=0 and max_chars=4096. Follow next_offset to read further saved text.",
            }
        },
        "error_code": payload.get("error_code"),
    }
    projected = {**message, "content": json.dumps(reference, ensure_ascii=False, separators=(",", ":"))}
    return projected if estimate_tokens(projected) < estimate_tokens(message) else None


def fit_context(messages: Sequence[Mapping[str, Any]], max_tokens: int) -> tuple[Mapping[str, Any], ...]:
    """Return ``messages`` or a shorter projection whose estimate fits ``max_tokens``.

    The opening user message is kept when it fits beside the latest complete
    exchange. The window always starts at a turn or user-message boundary, so
    an assistant tool call is never separated from its results. If even the
    latest turn exceeds the budget, the projection may replace a large, saved
    tool result with a reference retrievable using read_history(role='tool').
    Tool calls and the current user input remain intact.
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    messages = tuple(messages)
    total = sum(estimate_tokens(message) for message in messages)
    if total <= max_tokens:
        return messages
    starts = _boundaries(messages)
    original = messages
    # A long tool result in the *current* exchange can by itself make every
    # subsequent provider request too large. Keep the tool-call/result pair
    # and the real terminal status, but replace large result text in this
    # model-request projection with a durable, page-readable reference.
    # Neither the original transcript nor the action is touched or replayed.
    note = {"role": "system", "content": OMITTED_NOTE, "item_id": "context-window:omitted"}
    latest = starts[-1] if starts else 0
    latest_cost = sum(estimate_tokens(message) for message in messages[latest:])
    if latest_cost + estimate_tokens(note) > max_tokens:
        projected = list(messages)
        for index in sorted(range(latest, len(projected)),
                            key=lambda i: estimate_tokens(projected[i]), reverse=True):
            replacement = _project_saved_tool_result(projected[index])
            if replacement is None:
                continue
            latest_cost += estimate_tokens(replacement) - estimate_tokens(projected[index])
            projected[index] = replacement
            if latest_cost + estimate_tokens(note) <= max_tokens:
                break
        messages = tuple(projected)
    if not starts:
        # In the first tool exchange there is no later turn boundary. The
        # authorized tool result is still page-readable; add the projection
        # note without removing the current input or its tool-call envelope.
        if messages != original and sum(map(estimate_tokens, messages)) + estimate_tokens(note) <= max_tokens:
            return (note, *messages)
        return original
    pinned = (messages[0],) if messages[0].get("role") == "user" else ()
    fixed = sum(estimate_tokens(message) for message in pinned) + estimate_tokens(note)
    suffix = [0] * (len(messages) + 1)
    for index in range(len(messages) - 1, -1, -1):
        suffix[index] = suffix[index + 1] + estimate_tokens(messages[index])
    # An old opening can exceed a newly selected model's window by itself.
    # Prefer the current exchange; the original opening remains in history.
    if pinned and fixed + suffix[starts[-1]] > max_tokens:
        pinned = ()
        fixed = estimate_tokens(note)
    start = next((index for index in starts if fixed + suffix[index] <= max_tokens), starts[-1])
    if start <= len(pinned):
        return messages
    return (note, *pinned, *messages[start:])
