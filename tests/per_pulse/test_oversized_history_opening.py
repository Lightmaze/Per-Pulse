"""Continue a page with a large saved opening after selecting a smaller window.

World, Kernel, history and restart are real; model choices use synthetic HTTP.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import httpx
import pytest

from per_pulse.service import PageService
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.history import ConversationHistoryTools
from pulse_harness.core import ToolCall
from pulse_harness.core.context_window import OMITTED_NOTE, estimate_tokens
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.provider import CancellationToken

OPENING = "ORIGINAL_LONG_OPENING: " + "Retain the field observations. 山川 🌍\n" * 350
NOTICE = "Check the original field observations before continuing."
FOLLOWUP = "Continue the original work in this smaller window."


class OpeningFixture:
    def __init__(self, monkeypatch):
        monkeypatch.setenv("OPENING_FIXTURE_KEY", "explicit-synthetic-credential")
        self.requests = []

    def factory(self, scope):
        def handler(request):
            messages = json.loads(request.content)["messages"]
            self.requests.append((scope, messages))
            current = max(i for i, row in enumerate(messages) if row["role"] == "user")
            task = messages[current]["content"]
            results = [json.loads(row["content"]) for row in messages[current + 1:] if row["role"] == "tool"]
            operation = None
            if task == OPENING and not results:
                operation = ("notice", {"op": "record", "content": NOTICE})
            elif task == FOLLOWUP:
                if not results:
                    assert OPENING not in [row.get("content") for row in messages]
                    assert any(row.get("content") == OMITTED_NOTE for row in messages)
                    assert any(NOTICE in str(row.get("content")) for row in messages)
                    operation = ("search_history", {"query": "ORIGINAL_LONG_OPENING", "role": "user"})
                elif len(results) == 1:
                    assert results[0]["status"] == "succeeded"
                    match, = results[0]["output"]["matches"]
                    operation = ("read_history", {"message_id": match["source"]["message_id"],
                                                   "role": "user", "max_chars": 160})
                else:
                    assert results[-1]["status"] == "succeeded"
                    assert results[-1]["output"]["content"] == OPENING[:160]
            if operation:
                name, arguments = operation
                deltas = [{"tool_calls": [{"index": 0, "id": f"opening-{len(self.requests)}",
                    "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}]
            else:
                answer = "Original opening saved." if task == OPENING else "Continued from the original saved history."
                deltas = [{"content": character} for character in answer]
            events = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
            events.append({"choices": [{"index": 0, "delta": {},
                "finish_reason": "tool_calls" if operation else "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10}})
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "opening-fixture",
            credential_env="OPENING_FIXTURE_KEY"), transport=httpx.MockTransport(handler))


def activate(pages, page_id, event_id):
    runtime = pages.runtime
    scope = pages.execution._scope(page_id)
    result = runtime._mgr.pulse(runtime.continuity_engram_id,
        pulse_event=runtime.causal_ledger.get_event(event_id), execution_context=scope.context_id or "home",
        runtime_fence=runtime._current_runtime_fence())
    return pages.execution._scope(page_id).for_turn(result.turn_id)


@pytest.mark.parametrize("page_kind", ["home", "work"])
def test_large_opening_can_be_recalled_after_a_smaller_window_and_restart(tmp_path, monkeypatch, page_kind):
    monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", "16000")
    fixture = OpeningFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    try:
        if page_kind == "home":
            page_id = "home"
            event_id = pages.send(page_id, OPENING)["messageId"]
        else:
            created = pages.create_page("Field notes", OPENING)
            page_id, event_id = created["page"]["id"], created["initialMessageId"]
        original_scope = activate(pages, page_id, event_id)
        original = next(row for row in pages.runtime.kernel_service.store.transcript_rows(original_scope)
                        if row["role"] == "user")
        assert original["content"] == OPENING
        assert pages.runtime.close().clean

        monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", "2048")
        pages = PageService(tmp_path, model_factory=fixture.factory)
        start = len(fixture.requests)
        current = activate(pages, page_id, pages.send(page_id, FOLLOWUP)["messageId"])
        assert len(fixture.requests) - start == 3
        first_request = fixture.requests[start][1]
        # Provider instructions and tool schemas add transport overhead to
        # the separately configured history and Notice projection budget.
        projected = [row for row in first_request if row["role"] != "system"
                     or row.get("content") == OMITTED_NOTE
                     or "auxiliary assistant message marked pulse.local-notices.v1" in row.get("content", "")]
        assert sum(estimate_tokens(row) for row in projected) <= 2048
        assert any(row["content"] == OPENING for row in pages.runtime.kernel_service.store.transcript_rows(original_scope))

        history = ConversationHistoryTools(pages.runtime.kernel_service.store, pages.runtime.harness._history_scopes)
        parts, offset = [], 0
        while offset is not None:
            passage = history.execute_for_scope(current, ToolCall("read-original", "read_history", {
                "message_id": original["message_id"], "role": "user", "offset": offset, "max_chars": 4096,
            }), CancellationToken())
            parts.append(passage["content"])
            offset = passage["next_offset"]
        assert "".join(parts) == OPENING
        other = pages.create_page("Separate notes", "A separate conversation.")
        other_scope = activate(pages, other["page"]["id"], other["initialMessageId"])
        with pytest.raises(ScopeConflict):
            history.execute_for_scope(other_scope, ToolCall("no-cross-work", "read_history", {
                "message_id": original["message_id"], "role": "user",
            }), CancellationToken())
    finally:
        pages.runtime.close()
