"""A large saved user message can be read in place without losing its source.

The World, Kernel, SQLite transcript, and history authorization are real.
Model replies below come only from an explicitly synthetic HTTP fixture.
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
from pulse_harness.core.context_window import estimate_tokens, fit_context
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.provider import CancellationToken


def test_saved_large_current_user_message_is_addressable_without_mutating_history():
    original = {
        "role": "user",
        "content": "OPENING GOAL\n" + ("café 🌍 details\n" * 2500) + "FINAL REQUIREMENT",
        "message_id": "current-user-message-1",
        "item_id": "saved:user:1",
    }
    fitted = fit_context([original], 1024)
    assert sum(estimate_tokens(item) for item in fitted) <= 1024
    assert len(fitted) == 1 and fitted[0]["role"] == "user"
    assert "OPENING GOAL" in fitted[0]["content"]
    assert "FINAL REQUIREMENT" not in fitted[0]["content"]
    assert "read_history" in fitted[0]["content"]
    assert "current-user-message-1" in fitted[0]["content"]
    assert original["content"].endswith("FINAL REQUIREMENT")


def test_no_saved_user_address_means_no_false_history_reference():
    original = {"role": "user", "content": "LONG INPUT " + "a" * 18_000}
    assert fit_context([original], 1024) == (original,)


def test_real_world_can_page_the_current_input_and_keeps_other_work_private(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", "2048")
    monkeypatch.setenv("LONG_INPUT_FIXTURE_KEY", "explicit-synthetic-credential")
    requests = []

    def model_factory(scope):
        def respond(request):
            body = json.loads(request.content)
            requests.append((scope, body))
            payload = {
                "choices": [{
                    "index": 0,
                    "delta": {"content": "Synthetic result; no outside model was called."},
                    "finish_reason": "stop",
                }]
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n",
            )

        return ChatHTTPModelAdapter(
            ChatHTTPConfig(
                "https://fixture.invalid/chat/completions",
                "synthetic-long-input",
                credential_env="LONG_INPUT_FIXTURE_KEY",
            ),
            transport=httpx.MockTransport(respond),
        )

    pages = PageService(tmp_path, model_factory=model_factory)
    try:
        full_input = (
            "CURRENT_LONG_INPUT_START\n"
            + "各段内容 café 🌍\n" * 2400
            + "CURRENT_LONG_INPUT_END"
        )
        created = pages.create_page("Long document request", full_input)
        page_id = created["page"]["id"]
        runtime = pages.runtime
        context_id = pages.execution._scope(page_id).context_id
        event = runtime.causal_ledger.get_event(created["initialMessageId"])
        result = runtime._mgr.pulse(
            runtime.continuity_engram_id,
            pulse_event=event,
            execution_context=context_id or "home",
            runtime_fence=runtime._current_runtime_fence(),
        )
        scope = pages.execution._scope(page_id).for_turn(result.turn_id)
        store = runtime.kernel_service.store
        user = next(row for row in store.transcript_rows(scope) if row["role"] == "user")
        assert user["content"] == full_input
        assert requests
        sent = requests[-1][1]["messages"]
        shown = next(row for row in sent if row["role"] == "user")
        assert "CURRENT_LONG_INPUT_START" in shown["content"]
        assert "CURRENT_LONG_INPUT_END" not in shown["content"]
        assert user["message_id"] in shown["content"]
        assert "read_history" in shown["content"]
        assert sum(estimate_tokens(row) for row in sent) <= 2048

        history = ConversationHistoryTools(store, runtime.harness._history_scopes)
        def read(current_scope, **args):
            return history.execute_for_scope(
                current_scope,
                ToolCall("explicit-current-user-read", "read_history", args),
                CancellationToken(),
            )

        chunks, offset = [], 0
        while offset is not None:
            response = read(
                scope, message_id=user["message_id"], role="user",
                offset=offset, max_chars=4096,
            )
            assert response["source"]["message_id"] == user["message_id"]
            assert response["source"]["role"] == "user"
            assert response["coverage"]["current_turn_excluded"] is False
            chunks.append(response["content"])
            offset = response["next_offset"]
        assert "".join(chunks) == full_input
        search = history.execute_for_scope(
            scope, ToolCall("current-search", "search_history",
                            {"role": "user", "query": "CURRENT_LONG_INPUT_END"}),
            CancellationToken(),
        )
        assert search["matches"] == []

        other = pages.create_page("Unrelated Work", "Hello")
        other_page = other["page"]["id"]
        other_event = runtime.causal_ledger.get_event(other["initialMessageId"])
        other_result = runtime._mgr.pulse(
            runtime.continuity_engram_id,
            pulse_event=other_event,
            execution_context=pages.execution._scope(other_page).context_id or "home",
            runtime_fence=runtime._current_runtime_fence(),
        )
        other_scope = pages.execution._scope(other_page).for_turn(other_result.turn_id)
        with pytest.raises(ScopeConflict):
            read(other_scope, message_id=user["message_id"], role="user")
    finally:
        pages.runtime.close()
