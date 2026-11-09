"""Original tool results stay readable after a native context window moves on.

World, Kernel, actions, files and succession are real. The HTTP model choices
are an explicit synthetic fixture and never contact an external provider.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import httpx
import pytest

from per_pulse.service import PageService
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.history import ConversationHistoryTools, RECALL_TOOL_NAMES
from pulse_harness.core import HarnessScope, HarnessStore, ToolCall, ToolDescriptor, TurnRunner
from pulse_harness.core.context_window import OMITTED_NOTE
from pulse_harness.core.errors import ProviderProtocolError, RedactionRequired, ScopeConflict, StoreUnavailable
from pulse_harness.core.provider import CancellationToken
from pulse_harness.core.replay import bound_text

PREFIX = "SYNTHETIC_TOOL_OPERATIONS:"
ORIGINAL = "ORIGINAL_TOOL_ONLY: café 🌍 " * 45


class HistoryFixture:
    def __init__(self, monkeypatch, *, call_id_prefix="original-call-"):
        monkeypatch.setenv("TOOL_HISTORY_FIXTURE_KEY", "explicit-synthetic-credential")
        self.requests = []
        self.call_id_prefix = call_id_prefix

    def factory(self, scope):
        def respond(request):
            body = json.loads(request.content)
            self.requests.append((scope, body))
            messages = body["messages"]
            current = max(i for i, row in enumerate(messages) if row["role"] == "user")
            task = messages[current]["content"]
            operations = json.loads(task[len(PREFIX):]) if task.startswith(PREFIX) else []
            count = sum(row["role"] == "tool" for row in messages[current + 1:])
            if count < len(operations):
                name, arguments = operations[count]
                delta = {"tool_calls": [{"index": 0, "id": f"{self.call_id_prefix}{count}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            else:
                delta = {"content": "Completed the explicit synthetic fixture."}
            payload = {"choices": [{"index": 0, "delta": delta,
                "finish_reason": "tool_calls" if count < len(operations) else "stop"}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n")
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions",
            "tool-history-fixture", credential_env="TOOL_HISTORY_FIXTURE_KEY"),
            transport=httpx.MockTransport(respond))


def activate(pages, page_id, *, event_id=None, operations=(), text=None):
    if event_id is None:
        event_id = pages.send(page_id, text if text is not None else PREFIX + json.dumps(operations))["messageId"]
    runtime = pages.runtime
    context = pages.execution._scope(page_id).context_id
    result = runtime._mgr.pulse(runtime.continuity_engram_id,
        pulse_event=runtime.causal_ledger.get_event(event_id), execution_context=context or "home",
        runtime_fence=runtime._current_runtime_fence())
    return pages.execution._scope(page_id).for_turn(result.turn_id), result


def create(pages, title="Source", operations=()):
    created = pages.create_page(title, PREFIX + json.dumps(operations))
    page_id = created["page"]["id"]
    scope, result = activate(pages, page_id, event_id=created["initialMessageId"])
    return page_id, scope, result


def reader(pages):
    return ConversationHistoryTools(pages.runtime.kernel_service.store, pages.runtime.harness._history_scopes)


def recall(history, scope, name="search_history", **arguments):
    return history.execute_for_scope(scope, ToolCall("explicit-history-read", name, arguments), CancellationToken())


def test_clipped_tool_result_is_read_in_original_text_pages_without_reopening_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", "2048")
    fixture = HistoryFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    try:
        source = tmp_path / "workspace" / "source.txt"
        source.write_text(ORIGINAL)
        page_id, original_scope, _ = create(pages, operations=[("read_file", {"path": "source.txt"})])
        store, history = pages.runtime.kernel_service.store, reader(pages)
        saved = next(row for row in store.transcript_rows(original_scope) if row["role"] == "tool")
        original_action = store.actions_for_turn(original_scope)[0]
        # Browsing excludes current-turn tool copies, but direct, authorized
        # lookup of its settled result is available for context continuation.
        assert recall(history, original_scope, query="", role="tool")["matches"] == []
        current = recall(history, original_scope, "read_history",
                         message_id=saved["message_id"], role="tool")
        assert current["content"] == saved["content"]
        assert current["coverage"]["current_turn_excluded"] is False
        for _ in range(4):
            current, _ = activate(pages, page_id, text="Continue the local discussion. " + "detail " * 360)
        last = fixture.requests[-1][1]["messages"]
        assert any(row.get("content") == OMITTED_NOTE for row in last)
        assert ORIGINAL not in json.dumps(last, ensure_ascii=False)
        source.write_text("REVISED_CURRENT_FILE: a different observation.")
        before = tuple(store._db.iterdump())
        request_count = len(fixture.requests)
        assert recall(history, current, query="ORIGINAL_TOOL_ONLY")["matches"] == []
        found = recall(history, current, query="ORIGINAL_TOOL_ONLY", role="tool")["matches"]
        assert len(found) == 1
        identity = found[0]["source"]
        assert identity["message_id"] == saved["message_id"]
        assert identity["tool_name"] == "read_file"
        assert identity["action_request_id"] == original_action.action_request_id
        assert identity["tool_call_id"] == "original-call-0"
        assert identity["action_status"] == "succeeded" and identity["result_delivered"] is True
        assert identity["role"] == "tool"
        assert not identity["stored_redacted"] and not identity["stored_truncated"]
        # Neither discussion reads nor replay's default source reader gain tool material.
        with pytest.raises(ScopeConflict):
            recall(history, current, "read_history", message_id=saved["message_id"])
        assert store.read_transcript_message(current.session_scope(), saved["message_id"]) is None
        parts, offset = [], 0
        while offset is not None:
            passage = recall(history, current, "read_history", message_id=saved["message_id"],
                             role="tool", offset=offset, max_chars=97)
            assert passage["source"] == identity
            assert passage["coverage"]["roles"] == ["tool"]
            parts.append(passage["content"])
            offset = passage["next_offset"]
        assert "".join(parts) == saved["content"]
        assert ORIGINAL in "".join(parts) and "REVISED_CURRENT_FILE" not in "".join(parts)
        assert tuple(store._db.iterdump()) == before
        assert len(fixture.requests) == request_count
    finally:
        pages.runtime.close()



def test_one_large_tool_result_can_be_recalled_in_the_same_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", "2048")
    fixture = HistoryFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    try:
        source = tmp_path / "workspace" / "large.txt"
        source.write_text("CURRENT_TOOL_SOURCE_MARKER:" + "z" * 18_000)
        _, scope, _ = create(pages, operations=[("read_file", {"path": "large.txt"})])
        store = pages.runtime.kernel_service.store
        saved = next(row for row in store.transcript_rows(scope) if row["role"] == "tool")
        latest = fixture.requests[-1][1]["messages"]
        projected = next(row for row in latest if row["role"] == "tool")
        reference = json.loads(projected["content"])["output"]["context_window"]
        assert reference["message_id"] == saved["message_id"]
        assert "CURRENT_TOOL_SOURCE_MARKER" not in projected["content"]
        assert "CURRENT_TOOL_SOURCE_MARKER" in saved["content"]
        history = reader(pages)
        original = recall(history, scope, "read_history",
                          message_id=saved["message_id"], role="tool", max_chars=120)
        assert "CURRENT_TOOL_SOURCE_MARKER" in original["content"]
        assert original["next_offset"] is not None
        assert original["coverage"]["current_turn_excluded"] is False
    finally:
        pages.runtime.close()



def test_model_itself_reads_a_projected_current_result_without_rerunning_file(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", "2048")
    monkeypatch.setenv("TOOL_HISTORY_FIXTURE_KEY", "explicit-synthetic-credential")
    requests = []

    def respond(request):
        messages = json.loads(request.content)["messages"]
        requests.append(messages)
        tool_results = [item for item in messages if item["role"] == "tool"]
        if not tool_results:
            name, args, text = "read_file", {"path": "large.txt"}, None
        elif len(tool_results) == 1:
            reference = json.loads(tool_results[0]["content"])["output"]["context_window"]
            name, args, text = "read_history", {
                "message_id": reference["message_id"], "role": "tool", "offset": 0, "max_chars": 1000,
            }, None
        else:
            recovered = json.loads(tool_results[-1]["content"])["output"]
            assert "CURRENT_TOOL_SOURCE_MARKER" in json.dumps(recovered)
            name, args, text = None, None, "Recovered the saved file result and continued."

        if name:
            delta = {"tool_calls": [{"index": 0, "id": f"pagination-step-{len(tool_results)}",
                     "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        else:
            delta = {"content": text}
        event = {"choices": [{"index": 0, "delta": delta,
                              "finish_reason": "tool_calls" if name else "stop"}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="data: " + json.dumps(event) + "\\n\\ndata: [DONE]\\n\\n")

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "continuation-model",
            credential_env="TOOL_HISTORY_FIXTURE_KEY"),
            transport=httpx.MockTransport(respond))

    pages = PageService(tmp_path, model_factory=factory)
    try:
        (tmp_path / "workspace" / "large.txt").write_text(
            "CURRENT_TOOL_SOURCE_MARKER:" + "q" * 18_000)
        _, scope, _ = create(pages, operations=[])
        store = pages.runtime.kernel_service.store
        actions = store.actions_for_turn(scope)
        assert [action.tool_name for action in actions] == ["read_file", "read_history"]
        saved = next(row for row in store.transcript_rows(scope)
                     if row["role"] == "tool" and "CURRENT_TOOL_SOURCE_MARKER" in row["content"])
        projected = json.loads(next(row for row in requests[1] if row["role"] == "tool")["content"])
        assert projected["output"]["context_window"]["message_id"] == saved["message_id"]
        assert len(requests) == 3
        assert any("Recovered the saved file result" in row["content"]
                   for row in store.transcript_rows(scope) if row["role"] == "assistant")
    finally:
        pages.runtime.close()


def test_tool_search_pagination_is_bound_to_role_and_excludes_recall_copies(tmp_path, monkeypatch):
    fixture = HistoryFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    try:
        (tmp_path / "workspace" / "source.txt").write_text("SAVED_TOOL_MARKER")
        page_id, _, _ = create(pages)
        originals = []
        for _ in range(5):
            scope, _ = activate(pages, page_id, operations=[("read_file", {"path": "source.txt"})])
            originals.append(next(row["message_id"] for row in pages.runtime.kernel_service.store.transcript_rows(scope)
                                  if row["role"] == "tool"))
        # Exercise the reader through the native tool route, creating real recall receipts.
        _, _ = activate(pages, page_id, operations=[("search_history", {"query": "SAVED_TOOL_MARKER", "role": "tool"})])
        current, _ = activate(pages, page_id)
        history = reader(pages)
        collected, cursor = [], None
        while True:
            args = {"query": "", "role": "tool", "limit": 2}
            if cursor:
                args["cursor"] = cursor
            page = recall(history, current, **args)
            assert page["coverage"]["excluded_tool_names"] == list(RECALL_TOOL_NAMES)
            collected.extend(row["source"]["message_id"] for row in page["matches"])
            cursor = page["next_cursor"]
            if not cursor:
                break
            for changed in (None, "assistant", "user"):
                wrong = {"query": "", "limit": 2, "cursor": cursor}
                if changed:
                    wrong["role"] = changed
                with pytest.raises(ProviderProtocolError, match="cursor"):
                    recall(history, current, **wrong)
        assert collected == originals
        recall_row = next(row for row in pages.runtime.kernel_service.store.transcript_rows(current.session_scope())
                          if row["role"] == "tool" and '"matches"' in row["content"])
        with pytest.raises(ScopeConflict):
            recall(history, current, "read_history", message_id=recall_row["message_id"], role="tool")
        for role in (None, "", "reasoning", "system", [], True, 1):
            with pytest.raises(ProviderProtocolError, match="role"):
                recall(history, current, query="", role=role)
            with pytest.raises(ProviderProtocolError, match="role"):
                recall(history, current, "read_history", message_id=originals[0], role=role)
    finally:
        pages.runtime.close()


def test_original_tool_sources_keep_work_isolation_and_committed_succession_after_restart(tmp_path, monkeypatch):
    fixture = HistoryFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    try:
        (tmp_path / "workspace" / "source.txt").write_text(ORIGINAL)
        page_id, old_scope, _ = create(pages, operations=[("read_file", {"path": "source.txt"})])
        sibling, sibling_scope, _ = create(pages, title="Independent")
        current, last = activate(pages, page_id)
        original = recall(reader(pages), current, query="ORIGINAL_TOOL_ONLY", role="tool")["matches"][0]["source"]
        assert recall(reader(pages), sibling_scope, query="", role="tool")["matches"] == []
        with pytest.raises(ScopeConflict):
            recall(reader(pages), sibling_scope, "read_history", message_id=original["message_id"], role="tool")
        runtime = pages.runtime
        prepared = runtime._mgr.prepare_succession(runtime.continuity_engram_id,
            parent_event_id=last.result_event_id, runtime_fence=runtime._current_runtime_fence())
        successor = runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence()).new_id
        current, _ = activate(pages, page_id)
        assert current.engram_id == successor != old_scope.engram_id
        assert recall(reader(pages), current, query="ORIGINAL_TOOL_ONLY", role="tool")["matches"][0]["source"] == original
        pages.runtime.close()
        pages = PageService(tmp_path, model_factory=fixture.factory)
        current, _ = activate(pages, page_id)
        passage = recall(reader(pages), current, "read_history", message_id=original["message_id"], role="tool")
        assert passage["source"] == original and "ORIGINAL_TOOL_ONLY" in passage["content"]
        sibling_scope, _ = activate(pages, sibling)
        assert recall(reader(pages), sibling_scope, query="", role="tool")["matches"] == []
    finally:
        pages.runtime.close()


def test_failed_tool_result_keeps_its_original_action_outcome(tmp_path, monkeypatch):
    fixture = HistoryFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    try:
        page_id, original, _ = create(pages, operations=[("read_file", {"path": "missing.txt"})])
        current, _ = activate(pages, page_id)
        found = recall(reader(pages), current, query="", role="tool")["matches"][0]
        assert found["source"]["action_status"] == "failed"
        passage = recall(reader(pages), current, "read_history", message_id=found["source"]["message_id"], role="tool")
        raw = json.loads(passage["content"])
        action = pages.runtime.kernel_service.store.actions_for_turn(original)[0]
        assert raw["status"] == action.status.value == "failed"
        assert raw["error_code"] == action.error_code
    finally:
        pages.runtime.close()


def test_native_long_provider_call_id_returns_a_readable_tool_source(tmp_path, monkeypatch):
    fixture = HistoryFixture(monkeypatch, call_id_prefix="L" * 179)
    pages = PageService(tmp_path, model_factory=fixture.factory)
    try:
        (tmp_path / "workspace" / "source.txt").write_text(ORIGINAL)
        page_id, original, _ = create(pages, operations=[("read_file", {"path": "source.txt"})])
        current, _ = activate(pages, page_id)
        history = reader(pages)
        found = recall(history, current, query="ORIGINAL_TOOL_ONLY", role="tool")["matches"][0]
        identity = found["source"]
        assert identity["tool_call_id"] == "L" * 179 + "0"
        assert len(identity["message_id"]) > 256
        saved = next(row for row in pages.runtime.kernel_service.store.transcript_rows(original)
                     if row["role"] == "tool")
        passage = recall(history, current, "read_history", message_id=identity["message_id"], role="tool")
        assert passage["source"] == identity
        assert passage["content"] == saved["content"]
        # Discussion and selected replay retain their established id bound.
        for role in (None, "user", "assistant"):
            args = {"message_id": identity["message_id"]}
            if role is not None:
                args["role"] = role
            with pytest.raises(ProviderProtocolError):
                recall(history, current, "read_history", **args)
        with pytest.raises(ValueError):
            pages.runtime.kernel_service.store.read_transcript_message(current.session_scope(), identity["message_id"])
        from pulse_system.service import native_replay
        with pytest.raises(ValueError):
            native_replay.validate({"op": "enter", "message_ids": [identity["message_id"]]})
    finally:
        pages.runtime.close()


@pytest.mark.parametrize("text", ["x" * 3500, "[REDACTED]" + "🌍" * 875])
def test_old_bounded_tool_text_stays_exact_after_reopening_with_a_larger_bound(tmp_path, monkeypatch, text):
    """A legacy bounded transcript must not expand from its original action."""
    fixture = HistoryFixture(monkeypatch)
    output = {"content": text}
    result = {"output": output}
    old_bound = len(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) + 20
    store = HarnessStore(tmp_path / "bounded.db", max_payload_bytes=old_bound, max_events_per_session=8)
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="source")

    class ExplicitBlob:
        def descriptors(self):
            return (ToolDescriptor("read_file", "Read an explicit synthetic blob.", source="synthetic-fixture"),)

        def execute_for_scope(self, supplied_scope, call, cancellation, **kwargs):
            return output

    runner = TurnRunner(store, model_adapter=fixture.factory(scope), tool_provider=ExplicitBlob())
    try:
        with pytest.raises((ProviderProtocolError, RedactionRequired)):
            runner.run(scope, prompt=PREFIX + json.dumps([("read_file", {"path": "synthetic-blob.txt"})]))
        action = store.actions_for_turn(scope)[0]
        assert action.status.value == "succeeded"
        assert not any(row["role"] == "tool" for row in store.transcript_rows(scope))
        raw = json.dumps({"tool_call_id": "original-call-0", "action_request_id": action.action_request_id,
            "status": "succeeded", "output": output, "error_code": None},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if "🌍" in text:
            # Preserve the real replacement character produced when the older
            # byte bound cuts after three bytes of a four-byte code point.
            old_bound = next(bound for bound in range(old_bound, old_bound + 20)
                             if bound_text(raw, bound)[0].endswith("�]"))
            store.max_payload_bytes = old_bound
        # Explicit legacy fixture: older writers could bound the transcript
        # after the action had already succeeded, retaining only its marker.
        saved = store.append_transcript(scope, role="tool", content=raw,
                                       item_id="tool-result:" + action.action_request_id)
        assert saved["truncated"] and saved["redacted"] == ("[REDACTED]" in text)
        current = scope.session_scope().for_turn("reader")
        store.create_turn(current, "Inspect the original saved result.")
        # Retire the bounded observer envelope through ordinary retention; the
        # authoritative old transcript and model/action rows stay present.
        for index in range(9):
            store.append_transcript(current, role="user", content=f"Observer retention fixture {index}.")
    finally:
        runner.close()
        store.close()
    reopened = HarnessStore(tmp_path / "bounded.db", max_payload_bytes=64 * 1024)
    try:
        history = ConversationHistoryTools(reopened, lambda supplied: (supplied.session_scope(),))
        passage = recall(history, current, "read_history", message_id=saved["message_id"], role="tool")
        chunks = [passage["content"]]
        while passage["next_offset"] is not None:
            passage = recall(history, current, "read_history", message_id=saved["message_id"], role="tool",
                             offset=passage["next_offset"])
            chunks.append(passage["content"])
        assert "".join(chunks) == saved["content"]
        assert passage["source"]["stored_truncated"] is True
        assert passage["source"]["stored_redacted"] == saved["redacted"]
        assert passage["source"]["result_delivered"] is False
        assert passage["total_chars"] == len(saved["content"]) < len(raw)
        # Matching ids and a valid saved digest do not excuse a different
        # action-result passage; the history boundary checks the exact source.
        source_row = reopened._db.execute("SELECT * FROM transcript_messages WHERE message_id = ?",
                                          (saved["message_id"],)).fetchone()
        with pytest.raises(StoreUnavailable, match="action result"):
            reopened._history_record_locked({**source_row, "content": "A fabricated tool observation."})
    finally:
        reopened.close()
