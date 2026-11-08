"""Notice effects on the actual World, Kernel and HTTP request projection.

Model choices are explicit synthetic fixtures; no external model is contacted.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from dataclasses import replace
import json
import threading

import httpx
import pytest

from per_pulse.service import PageService, PulseSettings
from per_pulse.execution import PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core import HarnessScope
from pulse_harness.core.context_window import estimate_tokens
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.provider import CancellationRequested, ModelRequest
from pulse_system.service import native_notice, native_replay
from test_pages import client_for, new_page, send, wait_for


PRIVATE_REASONING = "PRIVATE_REASONING_MUST_NOT_ENTER_NOTICE_NEIGHBORHOOD"


def response(delta, finish="stop"):
    chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(
        "data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")


class NoticeFixture:
    def __init__(self, monkeypatch):
        monkeypatch.setenv("PER_PULSE_NOTICE_TEST_KEY", "explicit-synthetic-only")
        self.requests, self.results, self.number = [], [], 0
        self.reasoning = False
        self.fail_after_action = False
        self.pause_after_action = None

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        start = max(i for i, message in enumerate(messages) if message["role"] == "user")
        task = messages[start]["content"]
        if "NOTICE_REQUEST " not in task:
            return response({"content": "Ready. [Explicit synthetic fixture]"})
        values = json.loads(task[task.rfind("NOTICE_REQUEST ") + len("NOTICE_REQUEST "):])
        results = [json.loads(message["content"]) for message in messages[start + 1:] if message["role"] == "tool"]
        if not results:
            delta = {"tool_calls": [{"index": 0, "id": "notice-" + str(values["number"]), "type": "function",
                      "function": {"name": "notice", "arguments": json.dumps(values["arguments"])}}]}
            if self.reasoning:
                delta["reasoning_content"] = PRIVATE_REASONING
            return response(delta, "tool_calls")
        self.results.append(results[-1])
        if self.pause_after_action:
            entered, release = self.pause_after_action
            entered.set()
            assert release.wait(10), "release the explicit synthetic model pause"
        if self.fail_after_action:
            raise httpx.ReadError("explicit synthetic provider failure after a committed action")
        return response({"content": f"Notice request {values['number']} complete. [Explicit synthetic fixture]"})

    def factory(self, _scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "notice-test",
            credential_env="PER_PULSE_NOTICE_TEST_KEY"), transport=httpx.MockTransport(self.handler))

    def call(self, client, page, **arguments):
        self.number += 1
        number = self.number
        send(client, page, "NOTICE_REQUEST " + json.dumps({"number": number, "arguments": arguments}))
        wait_for(client, page, lambda view: any(message["role"] == "assistant" and
            message["content"].startswith(f"Notice request {number} complete.") for message in view["messages"]))
        return self.results[-1]


def envelope(request):
    for message in request["messages"]:
        try:
            payload = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == native_notice.KIND:
            assert message["role"] == "assistant"
            return payload
    return None


@pytest.mark.parametrize("page_kind", ["home", "work"])
def test_immediate_memory_restart_source_and_close(tmp_path, monkeypatch, page_kind):
    fixture = NoticeFixture(monkeypatch)
    fixture.reasoning = True
    options = {"model_factory": fixture.factory, "pulse": PulseSettings(enabled=False)}
    with client_for(tmp_path, **options) as client:
        page = "home" if page_kind == "home" else new_page(client)["page"]["id"]
        recorded = fixture.call(client, page, op="record", content="Return to the real source after reading.")
        assert recorded["status"] == "succeeded", recorded
        note = recorded["output"]["notice"]
        assert note["status"] == "pending" and note["source"]["registration_message_id"]
        assert note["source_outcome"]["world_turn"] == "running"
        assert note["source_outcome"]["kernel_action"] == "running"
        assert envelope(fixture.requests[-1])["notices"][0]["id"] == note["id"]
        assert not any(note["content"] in message.get("content", "") for message in fixture.requests[-1]["messages"]
                       if message["role"] == "system")
        read = fixture.call(client, page, op="read", id=note["id"], max_chars=2000)["output"]
        assert read["source_passage"]["available"]
        assert read["source_passage"]["source"]["message_id"] == note["source"]["registration_message_id"]
        assert '"action_request_id"' in read["source_passage"]["content"]
        assert PRIVATE_REASONING not in json.dumps(read)
        previous = fixture.call(client, page, op="read", id=note["id"], relative_message=-1)["output"]
        assert previous["source_passage"]["source"]["role"] == "user"
        assert PRIVATE_REASONING not in json.dumps(previous)
        assert read["notice"]["source_outcome"]["world_turn"] == "settled"
        assert read["notice"]["source_outcome"]["kernel_action"] == "succeeded"
        assert read["notice"]["source_outcome"]["result_delivered"] is True
        assert client.app.state.pages.runtime.storage.list_delegations() == []
        assert client.app.state.pages.runtime.context_pulse.config.enabled is False
    with client_for(tmp_path, **options) as client:
        listed = fixture.call(client, page, op="list")["output"]
        assert listed["notices"][0]["source"] == note["source"]
        assert fixture.call(client, page, op="close", id=note["id"], expected_revision=2)["status"] == "failed"
        assert envelope(fixture.requests[-1])["notices"][0]["id"] == note["id"]
        closed = fixture.call(client, page, op="close", id=note["id"], expected_revision=1)["output"]["notice"]
        assert closed["status"] == "closed" and closed["revision"] == 2 and closed["closed_at"]
        assert envelope(fixture.requests[-1]) is None
    with client_for(tmp_path, **options) as client:
        assert fixture.call(client, page, op="list")["output"]["notices"] == []
        assert fixture.call(client, page, op="read", id=note["id"])["output"]["notice"]["status"] == "closed"


def test_context_isolation_pagination_and_replay_boundary(tmp_path, monkeypatch):
    fixture = NoticeFixture(monkeypatch)
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        first = new_page(client)["page"]["id"]
        sibling = new_page(client, title="Sibling")["page"]["id"]
        notes = []
        for i in range(5):
            note = fixture.call(client, first, op="record", content=f"Private local intention {i}")["output"]["notice"]
            notes.append(note)
            # A newly recorded intention takes precedence over old backlog.
            summary = envelope(fixture.requests[-1])
            assert summary["notices"][0]["id"] == note["id"]
            assert len({item["id"] for item in summary["notices"]}) == len(summary["notices"])
        page = fixture.call(client, first, op="list", limit=1)["output"]
        assert page["total"] == 5 and page["next_cursor"]
        next_page = fixture.call(client, first, op="list", limit=1, cursor=page["next_cursor"])["output"]
        assert next_page["notices"][0]["id"] != page["notices"][0]["id"]
        assert fixture.call(client, sibling, op="list")["output"]["notices"] == []
        for operation in ({"op": "read", "id": notes[0]["id"]},
                          {"op": "close", "id": notes[0]["id"], "expected_revision": 1},
                          {"op": "list", "cursor": page["next_cursor"]}):
            assert fixture.call(client, sibling, **operation)["status"] == "failed"
            assert envelope(fixture.requests[-1]) is None
        r = client.app.state.pages.runtime
        scope = HarnessScope.from_dict(notes[0]["source"]["scope"])
        tool_provider = r.kernel_service._provider()
        for entry in ({"message_ids": ["selected"]}, {"focus": "Selected sources only"}):
            monkeypatch.setattr(native_replay, "mode", lambda *_, selected=entry: selected)
            assert tool_provider.model_context_messages(scope, 512) == ()
            assert [tool.name for tool in tool_provider.descriptors_for_scope(scope)] == ["replay"]
        bad_scope = replace(scope, world_id="different-world")
        with pytest.raises(ScopeConflict):
            r._native_notice.model_context_messages(bad_scope, 512)


def test_lost_receipt_is_durable_truthful_and_same_action_is_idempotent(tmp_path, monkeypatch):
    fixture = NoticeFixture(monkeypatch)
    captured = []
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        r = client.app.state.pages.runtime
        original = r.storage.apply_local_notice
        def lose_receipt(record, subjects, **kwargs):
            saved = original(record, subjects, **kwargs)
            captured.append((record, subjects, kwargs))
            raise RuntimeError("explicit fixture: durable memory committed, tool receipt lost")
        monkeypatch.setattr(r.storage, "apply_local_notice", lose_receipt)
        result = fixture.call(client, "home", op="record", content="Preserve this even if the receipt is lost.")
        assert result["status"] == "failed"
        assert envelope(fixture.requests[-1])["total_pending"] == 1
        monkeypatch.setattr(r.storage, "apply_local_notice", original)
        record, subjects, kwargs = captured[0]
        first = original(record, subjects, **kwargs)
        assert original(record, subjects, **kwargs) == first
        listed = fixture.call(client, "home", op="list")["output"]
        assert listed["total"] == 1
        note = listed["notices"][0]
        assert note["source_outcome"]["kernel_action"] == "failed"
        assert note["source_outcome"]["world_turn"] == "settled"
        with pytest.raises(ValueError, match="replay differs"):
            original({**record, "arguments": {"op": "record", "content": "changed replay"}}, subjects, **kwargs)
        fixture.call(client, "home", op="close", id=note["id"], expected_revision=1)
        # Replaying an earlier registration never reopens a later close.
        assert original(record, subjects, **kwargs) == first
        assert fixture.call(client, "home", op="list")["output"]["notices"] == []


def test_cancelled_action_after_commit_retains_memory_and_reports_actual_outcome(tmp_path, monkeypatch):
    fixture = NoticeFixture(monkeypatch)
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        r = client.app.state.pages.runtime
        original = r.storage.apply_local_notice
        def cancel_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            raise CancellationRequested("explicit synthetic cancel after durable memory")
        monkeypatch.setattr(r.storage, "apply_local_notice", cancel_after_commit)
        fixture.number = 1
        send(client, "home", "NOTICE_REQUEST " + json.dumps({"number": 1, "arguments": {"op": "record", "content": "Survive a cancelled turn."}}))
        wait_for(client, "home", lambda _: r.storage._conn.execute("SELECT COUNT(*) FROM local_notices").fetchone()[0] == 1)
        scope = r.harness.scope_for(r.continuity_engram_id)
        subjects = r.causal_ledger.committed_history_lineage(r.world_id, scope.engram_id)
        # Wait for the existing cancellation path to publish its actual terminal state.
        wait_for(client, "home", lambda _: r.storage.local_notices(r.world_id, "", subjects)[0]
                 and r._native_notice._view(r.storage.local_notices(r.world_id, "", subjects)[0][0])["source_outcome"]["kernel_action"] == "cancelled")
        item = r.storage.local_notices(r.world_id, "", subjects)[0][0]
        assert r._native_notice._view(item)["source_outcome"]["kernel_action"] == "cancelled"
        monkeypatch.setattr(r.storage, "apply_local_notice", original)
        assert fixture.call(client, "home", op="list")["output"]["total"] == 1
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        retained = fixture.call(client, "home", op="list")["output"]["notices"][0]
        assert retained["source_outcome"]["kernel_action"] == "cancelled"


def test_stop_original_turn_after_successful_record_survives_restart(tmp_path, monkeypatch):
    fixture = NoticeFixture(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    fixture.pause_after_action = (entered, release)
    fixture.number = 1
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        r = client.app.state.pages.runtime
        execution = PageExecution(r)
        send(client, "home", "NOTICE_REQUEST " + json.dumps({"number": 1, "arguments": {
            "op": "record", "content": "Retain successful registration when its original turn is stopped."}}))
        try:
            assert entered.wait(5)
            scope = next(iter(r.harness._active.values()))
            assert execution.cancel("home", scope.turn_id)["accepted"]
            assert r.kernel_service.store.get_turn(scope).status.value == "cancelled"
        finally:
            release.set()
        wait_for(client, "home", lambda _: r.causal_ledger.get_turn_for_world(scope.turn_id, r.world_id).state.value != "running")
    fixture.pause_after_action = None
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        note = fixture.call(client, "home", op="list")["output"]["notices"][0]
        assert note["source_outcome"]["kernel_turn"] == "cancelled"
        assert note["source_outcome"]["kernel_action"] == "succeeded"
        assert note["source_outcome"]["result_delivered"] is True
        assert note["source_outcome"]["world_turn"] in {"failed", "uncertain"}
        assert fixture.call(client, "home", op="read", id=note["id"])["output"]["source_passage"]["available"]


def test_source_turn_failure_and_missing_passage_are_explicit(tmp_path, monkeypatch):
    fixture = NoticeFixture(monkeypatch)
    fixture.fail_after_action = True
    fixture.number = 1
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        r = client.app.state.pages.runtime
        send(client, "home", "NOTICE_REQUEST " + json.dumps({"number": 1, "arguments": {
            "op": "record", "content": "A committed note outlives a provider failure."}}))
        subjects = r.causal_ledger.committed_history_lineage(r.world_id, r.continuity_engram_id)
        def failed(_):
            items, _ = r.storage.local_notices(r.world_id, "", subjects)
            return items and r._native_notice._view(items[0])["source_outcome"]["world_turn"] in {"failed", "uncertain"}
        wait_for(client, "home", failed)
    fixture.fail_after_action = False
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        note = fixture.call(client, "home", op="list")["output"]["notices"][0]
        assert note["source_outcome"]["world_turn"] in {"failed", "uncertain"}
        assert note["source_outcome"]["kernel_action"] == "succeeded"
        kernel = client.app.state.pages.runtime.kernel_service.store
        original = kernel.transcript_rows
        def unavailable(scope, **kwargs):
            if scope.turn_id == note["source"]["turn_id"]:
                raise ScopeConflict("explicit synthetic missing source")
            return original(scope, **kwargs)
        monkeypatch.setattr(kernel, "transcript_rows", unavailable)
        read = fixture.call(client, "home", op="read", id=note["id"])["output"]
        assert read["notice"]["content"] == note["content"]
        assert read["source_passage"]["available"] is False
        assert read["source_passage"]["reason"] == "registration_transcript_unavailable"


def test_committed_successor_reads_the_original_registration(tmp_path, monkeypatch):
    fixture = NoticeFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False))
    r = pages.runtime
    def execute(arguments, number):
        sent = pages.send("home", "NOTICE_REQUEST " + json.dumps({"number": number, "arguments": arguments}))
        return r._mgr.pulse(r.continuity_engram_id, pulse_event=r.causal_ledger.get_event(sent["messageId"]),
                            runtime_fence=r._current_runtime_fence())
    try:
        result = execute({"op": "record", "content": "Carry the actual local source across a committed succession."}, 1)
        note = fixture.results[-1]["output"]["notice"]
        predecessor = r.continuity_engram_id
        prepared = r._mgr.prepare_succession(predecessor, parent_event_id=result.result_event_id,
                                            runtime_fence=r._current_runtime_fence())
        successor = r._mgr.commit_succession(prepared, runtime_fence=r._current_runtime_fence()).new_id
        assert successor != predecessor
        execute({"op": "read", "id": note["id"]}, 2)
        read = fixture.results[-1]["output"]
        assert read["notice"]["source"] == note["source"]
        assert read["source_passage"]["available"] is True
        assert read["source_passage"]["source"]["message_id"] == note["source"]["registration_message_id"]
        assert envelope(fixture.requests[-1])["notices"][0]["id"] == note["id"]
    finally:
        r.close()


def test_small_window_keeps_meaningful_multibyte_excerpt(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_CONTEXT_TOKENS", "1024")
    fixture = NoticeFixture(monkeypatch)
    content = "保留真正的来源🌍🧭" * 45
    with client_for(tmp_path, model_factory=fixture.factory, pulse=PulseSettings(enabled=False)) as client:
        note = fixture.call(client, "home", op="record", content=content)["output"]["notice"]
        projected = envelope(fixture.requests[-1])
        assert projected["notices"][0]["id"] == note["id"]
        excerpt = projected["notices"][0]["content"]
        assert len(excerpt) >= 8 and content.startswith(excerpt)
        assert projected["notices"][0]["content_complete"] is False
        auxiliary = [message for message in fixture.requests[-1]["messages"]
                     if message["role"] == "system" and message["content"] == native_notice.HOST_CONTEXT
                     or message["role"] == "assistant" and native_notice.KIND in (message.get("content") or "")]
        assert sum(estimate_tokens(message) for message in auxiliary) <= 512
        read = fixture.call(client, "home", op="read", id=note["id"], max_chars=64)["output"]
        assert read["notice"]["content"] == content
        assert read["source_passage"]["next_offset"] == 64
        tail = fixture.call(client, "home", op="read", id=note["id"], offset=64, max_chars=100)["output"]
        assert tail["source_passage"]["offset"] == 64


@pytest.mark.parametrize("arguments", [
    {"op": "record", "content": " "}, {"op": "record", "content": "界" * 683},
    {"op": "record", "content": "valid", "world_id": "forged"},
    {"op": "close", "id": "notice", "expected_revision": True},
    {"op": "read", "id": "notice", "relative_message": 2},
    {"op": "list", "limit": 9},
])
def test_invalid_notice_arguments(arguments):
    with pytest.raises(ValueError):
        native_notice.validate(arguments)


def test_auxiliary_memory_does_not_hide_current_life_frame():
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")
    tool_call = {"role": "assistant", "tool_call_count": 1, "content": json.dumps({"text": "", "tool_calls": [
        {"tool_call_id": "frame", "name": "life_frame", "arguments": {}}]})}
    tool_result = {"role": "tool", "content": json.dumps({"tool_call_id": "frame", "status": "succeeded",
        "output": {"life_image": {"kind": "pulse_life_image.v1"}}})}
    memory = {"role": "assistant", "content": json.dumps({"kind": native_notice.KIND, "notices": []}),
              "item_id": "auxiliary-context:notice-data"}
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "fixture"))
    resolved = []
    adapter.life_image_resolver = lambda supplied_scope, payload: resolved.append((supplied_scope, payload)) or [
        {"type": "text", "text": "Explicit synthetic image reference"}]
    try:
        body = adapter.request_body(ModelRequest(scope, (tool_call, tool_result, memory), (), 0))
        assert resolved and resolved[0][0] == scope
        assert body["messages"][-1]["content"][0]["text"] == "Explicit synthetic image reference"
    finally:
        adapter.close()
