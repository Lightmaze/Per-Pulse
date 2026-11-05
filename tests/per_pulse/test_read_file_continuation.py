"""Addressable file passages travel through the existing World and Kernel.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Model decisions are explicit HTTP fixtures; file reads and durable actions are real.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from material_fixture import MaterialFixture
from pdf_samples import synthetic_pdf
from test_pages import client_for, send, wait_for
from pulse_harness.adapters import workspace
from pulse_harness.adapters.document_text import read_pdf
from pulse_harness.core import HarnessScope, HarnessStore, ToolCall
from pulse_harness.core.errors import ProviderProtocolError
from pulse_harness.core.provider import CancellationToken
from pulse_system.interaction.api.harness_kernel import HarnessKernelService


class ReadFileFixture(MaterialFixture):
    def __init__(self, setenv):
        super().__init__(setenv)
        self.outcomes = []
        self.delivered = []

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        current = max(i for i, item in enumerate(messages) if item["role"] == "user")
        task = json.loads(messages[current]["content"].split("EXPLICIT_READ_FILE_FIXTURE:", 1)[1])
        delivered = [item["content"] for item in messages[current + 1:] if item["role"] == "tool"]
        if not delivered:
            return self.response(tool="read_file", arguments=task["arguments"])
        result = json.loads(delivered[-1])
        self.delivered.append(delivered[-1])
        self.outcomes.append(result)
        if task["continue"] and result["status"] == "succeeded" and result["output"]["next"]:
            return self.response(tool="read_file", arguments=result["output"]["next"], sequence=len(delivered))
        return self.response("Recorded file passages. [Explicit synthetic fixture]")


def read(client, fixture, arguments, *, continuation=False):
    start = len(fixture.outcomes)
    sent = send(client, "home", "EXPLICIT_READ_FILE_FIXTURE:" + json.dumps({
        "arguments": arguments, "continue": continuation,
    }))
    wait_for(client, "home", lambda view: any(
        row["id"] == sent["messageId"] and row["status"] == "settled" for row in view["messages"]), timeout=20)
    assert len(fixture.outcomes) > start
    return fixture.outcomes[start:]


@pytest.mark.parametrize("text", [
    'A quoted line: "text" with a tab\tand slash \\\r\n' * 3500,
    "Code with café, e\u0301, 🌍 and 中文.\r\n" * 5000,
], ids=["escaped-json", "unicode"])
def test_native_read_file_continues_past_the_default_bound_to_actual_eof(tmp_path, monkeypatch, text):
    fixture = ReadFileFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        target = tmp_path / "workspace" / "long.py"
        target.write_bytes(text.encode())
        results = read(client, fixture, {"path": target.name}, continuation=True)
        assert len(results) >= 3
        assert all(result["status"] == "succeeded" for result in results)
        passages = [result["output"] for result in results]
        assert "".join(passage["content"] for passage in passages) == text
        digest = hashlib.sha256(text.encode()).hexdigest()
        cursor = 0
        runtime = client.app.state.pages.runtime
        execution = client.app.state.pages.execution
        store = runtime.kernel_service.store
        actions = store.actions_for_session(execution._scope("home"))
        assert len(actions) == len(results)
        for index, passage in enumerate(passages):
            end = cursor + len(passage["content"])
            assert passage["position"] == {"start_char": cursor, "end_char": end,
                                           "total_chars": len(text), "unit": "unicode_characters"}
            assert end > cursor and passage["digest"] == digest
            assert not passage["redacted"]
            assert passage["eof"] is (index == len(passages) - 1)
            assert passage["truncated"] is (not passage["eof"])
            assert passage["next"] == ({"path": target.name, "offset": end, "expected_digest": digest}
                                       if not passage["eof"] else None)
            action = actions[index]
            assert action.status.value == "succeeded" and action.result == {"output": passage}
            cursor = end
        transcript = store.transcript_rows(execution._scope("home"))
        assert all(len(item["content"].encode()) <= store.max_payload_bytes
                   for item in transcript if item["role"] == "tool")
        assert all(len(delivered.encode()) <= store.max_payload_bytes for delivered in fixture.delivered)
        offered = next(tool["function"] for tool in fixture.requests[0]["tools"]
                       if tool["function"]["name"] == "read_file")
        assert set(offered["parameters"]["properties"]) == {"path", "offset", "max_chars", "expected_digest"}
        assert "next" in offered["description"] and "eof" in offered["description"]
        assert target.read_bytes() == text.encode()


def test_native_read_file_uses_character_offsets_and_allows_current_file_random_access(tmp_path, monkeypatch):
    fixture = ReadFileFixture(monkeypatch.setenv)
    text = "a🌍e\u0301中文\r\nlast"
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        target = tmp_path / "workspace" / "unicode.txt"
        target.write_bytes(text.encode())
        first, = read(client, fixture, {"path": target.name, "offset": 1, "max_chars": 4})
        passage = first["output"]
        assert passage["content"] == text[1:5]
        assert passage["position"] == {"start_char": 1, "end_char": 5, "total_chars": len(text),
                                       "unit": "unicode_characters"}
        assert passage["next"] == {"path": target.name, "offset": 5, "max_chars": 4,
                                   "expected_digest": hashlib.sha256(text.encode()).hexdigest()}
        remainder = read(client, fixture, passage["next"], continuation=True)
        assert passage["content"] + "".join(result["output"]["content"] for result in remainder) == text[1:]
        eof, = read(client, fixture, {"path": target.name, "offset": len(text)})
        assert eof["output"]["content"] == "" and eof["output"]["eof"]
        assert eof["output"]["next"] is None and not eof["output"]["truncated"]
        target.write_bytes(b"")
        empty, = read(client, fixture, {"path": target.name})
        assert empty["output"]["position"] == {"start_char": 0, "end_char": 0, "total_chars": 0,
                                             "unit": "unicode_characters"}
        assert empty["output"]["content"] == "" and empty["output"]["eof"]
        assert empty["output"]["next"] is None


def test_native_continuation_refuses_changed_bytes_and_can_restart(tmp_path, monkeypatch):
    fixture = ReadFileFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        target = tmp_path / "workspace" / "changing.txt"
        target.write_text("First source. Unread original tail.")
        first, = read(client, fixture, {"path": target.name, "max_chars": 5})
        target.write_text("First source. UNREAD_REPLACEMENT_CONTENT.")
        changed, = read(client, fixture, first["output"]["next"])
        assert changed["status"] == "failed" and changed["error_code"] == "workspace_digest_conflict"
        descriptor = next(tool["function"]["description"] for tool in fixture.requests[-1]["tools"]
                          if tool["function"]["name"] == "read_file")
        assert "workspace_digest_conflict" in descriptor
        assert "offset=0" in descriptor and "omit expected_digest" in descriptor
        assert "UNREAD_REPLACEMENT_CONTENT" not in json.dumps(changed)
        restarted, = read(client, fixture, {"path": target.name})
        assert restarted["output"]["content"] == target.read_text()
        random, = read(client, fixture, {"path": target.name, "offset": 14})
        assert random["output"]["content"] == target.read_text()[14:]


def test_small_kernel_payload_continues_escaped_unicode_source(tmp_path, monkeypatch):
    fixture = ReadFileFixture(monkeypatch.setenv)
    text = '🌍 café: "quoted" \\\t\r\n' * 200
    service = HarnessKernelService(tmp_path / "kernel.db", tmp_path / "workspace", tmp_path / "checkpoints",
                                   model_adapter_factory=fixture.factory, max_payload_bytes=2048)
    scope = HarnessScope("world", "subject", "session", 0, "owner")
    try:
        service.create(scope)
        (service.workspace_root / "source.txt").write_bytes(text.encode())
        result = service.run_turn(scope.for_turn("read-long"), prompt="EXPLICIT_READ_FILE_FIXTURE:" + json.dumps({
            "arguments": {"path": "source.txt"}, "continue": True,
        }))
        assert result["outcome"]["status"] == "succeeded", result
        assert len(fixture.outcomes) >= 4
        assert all(item["status"] == "succeeded" for item in fixture.outcomes)
        passages = [item["output"] for item in fixture.outcomes]
        assert "".join(item["content"] for item in passages) == text
        cursor = 0
        for item in passages:
            assert item["position"]["start_char"] == cursor
            assert item["position"]["end_char"] > cursor
            cursor = item["position"]["end_char"]
        assert passages[-1]["eof"] and passages[-1]["next"] is None
        assert all(len(item["content"].encode()) <= 2048 for item in service.store.transcript_rows(scope)
                   if item["role"] == "tool")
    finally:
        service.close()


@pytest.fixture
def provider(tmp_path):
    store = HarnessStore(tmp_path / "kernel.db", max_payload_bytes=2048)
    scope = HarnessScope("world", "subject", "session", 0, "owner")
    store.create_session(scope)
    tool = workspace.StructuredWorkspaceToolProvider(store, tmp_path / "workspace", tmp_path / "checkpoints")
    (tool.workspace_root / "source.txt").write_text("Current file text.")
    try:
        yield tool, scope
    finally:
        store.close()


@pytest.mark.parametrize("arguments", [
    {"offset": -1}, {"offset": True}, {"offset": 1.5}, {"offset": 100},
    {"max_chars": 0}, {"max_chars": False}, {"max_chars": "2"},
    {"expected_digest": None}, {"expected_digest": "not-a-digest"}, {"extra": 1},
])
def test_invalid_ranges_and_versions_are_rejected(provider, arguments):
    tool, scope = provider
    with pytest.raises(ProviderProtocolError) as caught:
        tool.execute_for_scope(scope, ToolCall("invalid-read", "read_file", {"path": "source.txt", **arguments}),
                               CancellationToken())
    assert caught.value.code == "tool_arguments_invalid"


def test_metadata_that_cannot_fit_cannot_return_a_nonadvancing_continuation(provider):
    tool, scope = provider
    with pytest.raises(ProviderProtocolError) as caught:
        tool.execute_for_scope(scope, ToolCall("x" * tool.store.max_payload_bytes, "read_file",
                                              {"path": "source.txt"}), CancellationToken())
    assert caught.value.code == "payload_too_large"


def test_pdf_extraction_completeness_is_independent_of_text_eof(provider, monkeypatch):
    tool, scope = provider
    target = tool.workspace_root / "source.pdf"
    target.write_bytes(synthetic_pdf(["", "A page beyond the extraction limit."]))
    monkeypatch.setattr(workspace, "read_pdf", lambda data, **kwargs: read_pdf(data, max_pages=1, **kwargs))
    first = tool.read_file(scope, target.name, max_chars=10)
    assert not first["eof"] and not first["pdf_complete"]
    assert first["pdf_pages_without_selectable_text"]
    final = tool.read_file(scope, target.name, offset=first["position"]["total_chars"],
                           expected_digest=first["digest"])
    assert final["eof"] and final["next"] is None and final["content"] == ""
    assert not final["pdf_complete"] and final["pdf_pages_without_selectable_text"]
