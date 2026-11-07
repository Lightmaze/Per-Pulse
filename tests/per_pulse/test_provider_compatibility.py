"""Provider wire regressions using explicitly synthetic HTTP responses.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from pulse_harness.adapters.chat_http import (
    ChatHTTPConfig, ChatHTTPModelAdapter, WORKSPACE_SCHEMAS, model_factory_from_env,
)
from pulse_harness.core import HarnessScope
from pulse_harness.core.contracts import ToolDescriptor
from pulse_harness.core.errors import ProviderProtocolError
from pulse_harness.core.provider import ModelDeltaKind, ModelRequest


SCOPE = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")
USAGE = {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}
SIGNATURE = "opaque-synthetic-google-signature=="


def _request(*, messages=None, tools=()):
    return ModelRequest(scope=SCOPE, messages=messages or ({"role": "user", "content": "Write a note."},),
                        tools=tools, cursor=0)


def _sse(events):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n")


def _chunk(delta, finish=None):
    return {"id": "synthetic-completion", "choices": [{"index": 0, "delta": delta,
             "finish_reason": finish}], "usage": None}


def test_anthropic_compatible_request_accepts_the_file_tool_catalog(monkeypatch):
    monkeypatch.setenv("PER_PULSE_COMPAT_KEY", "synthetic-credential")
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        for tool in body["tools"]:
            schema = tool["function"]["parameters"]
            if any(key in schema for key in ("oneOf", "anyOf", "allOf")):
                return httpx.Response(400, json={"error": {"message": "Unsupported root composition"}})
        return _sse([_chunk({"content": "Ready."}), _chunk({}, "stop"), {"choices": [], "usage": USAGE}])

    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://api.anthropic.com/v1/chat/completions",
        "operator-selected-claude", credential_env="PER_PULSE_COMPAT_KEY"), transport=httpx.MockTransport(respond))
    try:
        tools = tuple(ToolDescriptor(name, "Synthetic catalog entry.") for name in WORKSPACE_SCHEMAS)
        assert list(adapter.stream(_request(tools=tools)))[-1].kind is ModelDeltaKind.DONE
        mutation = next(t["function"] for t in requests[0]["tools"] if t["function"]["name"] == "mutate_file")
        assert mutation["parameters"]["required"] == ["path", "expected_digest"]
        assert mutation["parameters"]["properties"]["expected_digest"]["type"] == ["string", "null"]
    finally:
        adapter.close()


def test_standard_null_usage_chunks_keep_streaming_until_the_final_report(monkeypatch):
    monkeypatch.setenv("PER_PULSE_COMPAT_KEY", "synthetic-credential")
    # Real services emit null usage on token-sized deltas and a final report.
    events = [_chunk({"role": "assistant", "content": ""})]
    events.extend(_chunk({"content": char}) for char in "A continuing reply.")
    events.extend([_chunk({}, "stop"), {"choices": [], "usage": USAGE}])
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://provider.example/v1/chat/completions", "model",
        credential_env="PER_PULSE_COMPAT_KEY"), transport=httpx.MockTransport(lambda _: _sse(events)))
    try:
        deltas = list(adapter.stream(_request()))
        assert "".join(d.text for d in deltas if d.kind is ModelDeltaKind.TEXT) == "A continuing reply."
        assert deltas[-1].usage.input_tokens == 120
        assert deltas[-1].usage.output_tokens == 8
    finally:
        adapter.close()


@pytest.mark.parametrize("reports", [[False], [{"prompt_tokens": 1}], [USAGE, USAGE]])
def test_invalid_or_duplicate_non_null_usage_still_fails(monkeypatch, reports):
    monkeypatch.setenv("PER_PULSE_COMPAT_KEY", "synthetic-credential")
    events = [_chunk({"content": "Reply."}), _chunk({}, "stop")]
    events.extend({"choices": [], "usage": value} for value in reports)
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://provider.example/v1/chat/completions", "model",
        credential_env="PER_PULSE_COMPAT_KEY"), transport=httpx.MockTransport(lambda _: _sse(events)))
    try:
        with pytest.raises(ProviderProtocolError):
            list(adapter.stream(_request()))
    finally:
        adapter.close()


@pytest.mark.parametrize("endpoint,profile,token_field", [
    ("https://api.openai.com/v1/chat/completions", "openai", "max_completion_tokens"),
    ("https://unit.openai.azure.com/openai/v1/chat/completions", "openai", "max_completion_tokens"),
    ("https://api.anthropic.com/v1/chat/completions", "anthropic", "max_tokens"),
    ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini", "max_tokens"),
    ("https://api.deepseek.com/chat/completions", "deepseek", "max_tokens"),
    ("https://openrouter.ai/api/v1/chat/completions", "openrouter", "max_tokens"),
    ("https://api.openai.com.proxy.example/v1/chat/completions", "compatible", "max_tokens"),
])
def test_official_endpoints_select_their_wire_profile(endpoint, profile, token_field):
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig(endpoint, "operator-selected-model", max_output_tokens=2048))
    try:
        assert adapter.config.resolved_profile == profile
        body = adapter.request_body(_request())
        assert body[token_field] == 2048
        assert ("max_tokens" in body) != ("max_completion_tokens" in body)
    finally:
        adapter.close()


def test_proxy_profile_uses_the_existing_environment_factory(monkeypatch):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://proxy.example/v1/chat/completions")
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "operator-selected-model")
    monkeypatch.setenv("P_PULSE_CHAT_PROFILE", "openai")
    adapter = model_factory_from_env()(SCOPE)
    try:
        assert adapter.request_body(_request())["max_completion_tokens"] == 4096
    finally:
        adapter.close()
    with pytest.raises(ValueError, match="profile"):
        ChatHTTPConfig("https://proxy.example/v1/chat/completions", "model", provider_profile="unknown")


def test_deepseek_returns_reasoning_with_its_original_assistant_tool_message():
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://api.deepseek.com/chat/completions", "operator-selected-model"))
    try:
        messages = (
            {"role": "user", "content": "Write a note."},
            {"role": "reasoning", "content": "Synthetic reasoning retained exactly."},
            {"role": "assistant", "content": json.dumps({"text": "", "tool_calls": [{
                "tool_call_id": "call-1", "name": "mutate_file", "arguments": {
                    "path": "note.txt", "expected_digest": None, "content": "Hello."}}]})},
            {"role": "tool", "content": json.dumps({"tool_call_id": "call-1", "status": "succeeded", "output": {}})},
        )
        wire = adapter.request_body(_request(messages=messages))["messages"]
        assert wire[1]["role"] == "assistant"
        assert wire[1]["reasoning_content"] == "Synthetic reasoning retained exactly."
        assert wire[2]["tool_call_id"] == "call-1"
    finally:
        adapter.close()


def test_gemini_tool_signature_is_carried_separately_from_executable_arguments(monkeypatch):
    monkeypatch.setenv("PER_PULSE_COMPAT_KEY", "synthetic-credential")
    fragments = [
        _chunk({"tool_calls": [{"index": 0, "id": "google-call", "type": "function", "function": {
            "name": "mutate_file", "arguments": '{"path":"note.txt",'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"expected_digest":null,"content":"Hello."}'},
            "extra_content": {"google": {"thought_signature": SIGNATURE}, "unrelated": "ignored"}}]}),
        _chunk({}, "tool_calls"), {"choices": [], "usage": USAGE},
    ]
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "operator-selected-gemini", credential_env="PER_PULSE_COMPAT_KEY"), transport=httpx.MockTransport(lambda _: _sse(fragments)))
    try:
        call = next(d.tool_call for d in adapter.stream(_request()) if d.kind is ModelDeltaKind.TOOL_CALL)
        assert call.arguments == {"path": "note.txt", "expected_digest": None, "content": "Hello."}
        assert call.provider_metadata == {"google": {"thought_signature": SIGNATURE}}
        payload = {"text": "", "tool_calls": [{"tool_call_id": call.tool_call_id, "name": call.name,
            "arguments": dict(call.arguments), "provider_metadata": dict(call.provider_metadata)}]}
        wire = adapter.request_body(_request(messages=({"role": "assistant", "content": json.dumps(payload)},)))
        assert wire["messages"][0]["tool_calls"][0]["extra_content"] == call.provider_metadata
    finally:
        adapter.close()


@pytest.mark.parametrize("arguments", [
    {"path": "invalid.txt", "expected_digest": None},
    {"path": "invalid.txt", "expected_digest": None, "content": "Unsafe", "patch": {"edits": [{"old": "A", "new": "B"}]}},
])
def test_file_argument_exclusivity_is_enforced_before_any_effect(tmp_path, monkeypatch, arguments):
    from per_pulse.execution import PageExecution
    from test_execution import _client, _factory, _new_page, _wait

    requests = []
    with _client(tmp_path, model_factory=_factory(monkeypatch, requests, tool=("mutate_file", arguments)),
                 allow_workspace_actions=True, auto_apply_edits=True, posture=False) as client:
        page = _new_page(client)
        state = _wait(PageExecution(client.app.state.pages.runtime), page,
                      lambda view: view["status"] == "idle" and len(requests) == 2)
        result = next(m for m in requests[-1]["messages"] if m["role"] == "tool")
        assert json.loads(result["content"])["error_code"] == "tool_arguments_invalid"
        assert not state["pendingActions"]
        assert not (tmp_path / "workspace" / "invalid.txt").exists()


@pytest.mark.parametrize("profile", ["gemini", "openrouter"])
def test_provider_signature_survives_native_approval_and_sqlite_restart(tmp_path, monkeypatch, profile):
    from per_pulse.execution import PageExecution
    from test_execution import _client, _new_page, _wait

    monkeypatch.setenv("PER_PULSE_COMPAT_KEY", "synthetic-credential")
    requests = []
    details = [{"type": "reasoning.encrypted", "data": SIGNATURE, "id": "opaque-block", "index": 0}]

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if any(m["role"] == "tool" for m in body["messages"]):
            assistant = next(m for m in body["messages"] if m.get("tool_calls"))
            if profile == "gemini":
                assert assistant["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": SIGNATURE}}
            else:
                assert assistant["reasoning_details"] == details
            events = [_chunk({"content": "Synthetic signed tool continuation finished."}), _chunk({}, "stop")]
        else:
            events = [_chunk({"tool_calls": [{"index": 0, "id": "signed-write", "type": "function",
                "function": {"name": "mutate_file", "arguments": json.dumps({
                    "path": "signed.txt", "expected_digest": None, "content": "A real native file.\n"})},
                "extra_content": {"google": {"thought_signature": SIGNATURE}}}]}), _chunk({}, "tool_calls")]
        if profile == "openrouter" and not any(m["role"] == "tool" for m in body["messages"]):
            events.insert(0, _chunk({"reasoning_details": details}))
        return _sse([*events, {"choices": [], "usage": USAGE}])

    def factory(_scope):
        endpoint = ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                    if profile == "gemini" else "https://openrouter.ai/api/v1/chat/completions")
        return ChatHTTPModelAdapter(ChatHTTPConfig(endpoint,
            "operator-selected-model", credential_env="PER_PULSE_COMPAT_KEY"), transport=httpx.MockTransport(respond))

    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True, posture=False) as client:
        page = _new_page(client)
        waiting = _wait(PageExecution(client.app.state.pages.runtime), page,
                        lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action = waiting["pendingActions"][0]["actionId"]
        assert not (tmp_path / "workspace" / "signed.txt").exists()
        execution = PageExecution(client.app.state.pages.runtime)
        assert execution.resolve_approval(page, action, "allow_once")["accepted"]
        _wait(execution, page, lambda view: view["status"] == "idle")
        assert (tmp_path / "workspace" / "signed.txt").read_bytes() == b"A real native file.\n"
        assert len(requests) == 2
        artifact = client.get(f"/api/pages/{page}/artifacts").json()["artifacts"][0]
        assert artifact["sha256"] == hashlib.sha256(b"A real native file.\n").hexdigest()
        modified = (tmp_path / "workspace" / "signed.txt").stat().st_mtime_ns
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True, posture=False) as client:
        assert client.post(f"/api/pages/{page}/messages", json={"content": "Continue the same Work."}).status_code == 202
        _wait(PageExecution(client.app.state.pages.runtime), page,
              lambda view: view["status"] == "idle" and len(requests) == 3)
        assert (tmp_path / "workspace" / "signed.txt").stat().st_mtime_ns == modified


@pytest.mark.parametrize("blocked", [False, True])
def test_azure_prompt_and_late_output_annotations_preserve_the_actual_response_id(monkeypatch, blocked):
    monkeypatch.setenv("PER_PULSE_COMPAT_KEY", "synthetic-credential")
    annotation = {"id": "", "choices": [{"index": 0, "finish_reason": None,
        "content_filter_results": {"violence": {"filtered": False, "severity": "safe"}}}], "usage": None}
    final_annotation = json.loads(json.dumps(annotation))
    if blocked:
        final_annotation["choices"][0]["finish_reason"] = "content_filter"
        final_annotation["choices"][0]["content_filter_results"]["violence"]["filtered"] = True
    events = [
        {"id": "", "choices": [], "usage": None, "prompt_annotations": [{"prompt_index": 0}]},
        _chunk({"content": "A reply."}), annotation, _chunk({}, "stop"), final_annotation,
    ]
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://unit.openai.azure.com/openai/v1/chat/completions",
        "deployment", credential_env="PER_PULSE_COMPAT_KEY"), transport=httpx.MockTransport(lambda _: _sse(events)))
    try:
        if blocked:
            with pytest.raises(ProviderProtocolError) as error:
                list(adapter.stream(_request()))
            assert error.value.code == "provider_content_filtered"
        else:
            deltas = list(adapter.stream(_request()))
            assert "".join(d.text for d in deltas) == "A reply."
            assert deltas[-1].provider_response_id == "synthetic-completion"
    finally:
        adapter.close()


@pytest.mark.parametrize("empty", [False, True])
def test_openrouter_reassembles_reasoning_blocks_and_keeps_encrypted_data_opaque(monkeypatch, empty):
    monkeypatch.setenv("PER_PULSE_COMPAT_KEY", "synthetic-credential")
    fragments = [] if empty else [
        {"type": "reasoning.summary", "summary": "Read ", "index": 0},
        {"type": "reasoning.summary", "summary": "the source.", "index": 0},
        {"type": "reasoning.encrypted", "data": "opaque==", "id": "block", "index": 0},
        {"type": "reasoning.text", "text": "Use the tool.", "index": 0},
        {"type": "reasoning.text", "signature": SIGNATURE, "index": 0},
        {"type": "reasoning.text", "text": "A second signed block.", "index": 0},
        {"type": "reasoning.text", "signature": "second-signature", "index": 0},
    ]
    events = [_chunk({"reasoning_details": [fragment]}) for fragment in fragments]
    if empty:
        events.append(_chunk({"reasoning_details": []}))
    events.extend([_chunk({"tool_calls": [{"index": 0, "id": "router-call", "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"source.txt"}'}}]}), _chunk({}, "tool_calls")])
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("https://openrouter.ai/api/v1/chat/completions", "operator-model",
        credential_env="PER_PULSE_COMPAT_KEY"), transport=httpx.MockTransport(lambda _: _sse(events)))
    try:
        call = next(d.tool_call for d in adapter.stream(_request()) if d.kind is ModelDeltaKind.TOOL_CALL)
        expected = [] if empty else [
            {"type": "reasoning.summary", "summary": "Read the source.", "index": 0}, fragments[2],
            {"type": "reasoning.text", "text": "Use the tool.", "index": 0, "signature": SIGNATURE},
            {"type": "reasoning.text", "text": "A second signed block.", "index": 0, "signature": "second-signature"},
        ]
        assert call.provider_metadata["openrouter"]["reasoning_details"] == expected
        content = json.dumps({"text": "", "tool_calls": [{"tool_call_id": call.tool_call_id, "name": call.name,
            "arguments": dict(call.arguments), "provider_metadata": dict(call.provider_metadata)}]})
        wire = adapter.request_body(_request(messages=({"role": "assistant", "content": content},)))
        assert wire["messages"][0]["reasoning_details"] == expected
    finally:
        adapter.close()


@pytest.mark.real
def test_configured_provider_writes_a_file_and_continues(tmp_path, monkeypatch):
    """Explicit opt-in: at most two real requests using the operator's config."""
    from dataclasses import replace
    import threading
    import time

    from fastapi.testclient import TestClient
    from per_pulse.app import create_app
    from per_pulse.service import PulseSettings
    from pulse_harness.core.errors import ProviderUnavailable

    configured = model_factory_from_env()
    if configured is None:
        pytest.skip("Configure an endpoint, model and credential before this live check.")
    initial = configured(SCOPE)
    try:
        initial.preflight_stream()
        config = replace(initial.config, max_retries=0, timeout_seconds=60, max_stream_seconds=120)
    finally:
        initial.close()
    calls = 0
    lock = threading.Lock()

    original_stream = ChatHTTPModelAdapter.stream

    def bounded_stream(self, request, *, cancel_event=None):
        nonlocal calls
        with lock:
            if calls >= 2:
                raise ProviderUnavailable("The live compatibility check reached its two-request limit.")
            calls += 1
        return original_stream(self, request, cancel_event=cancel_event)

    # Keep the native bridge's exact adapter type and its real HTTP transport.
    monkeypatch.setattr(ChatHTTPModelAdapter, "stream", bounded_stream)

    def factory(_scope):
        return ChatHTTPModelAdapter(config)

    expected = b"Provider tool round trip.\n"
    with TestClient(create_app(tmp_path, model_factory=factory, posture=False,
                    pulse=PulseSettings(enabled=True, spontaneous_rate=0), allow_workspace_actions=True,
                    auto_apply_edits=True), base_url="http://localhost") as client:
        response = client.post("/api/pages", json={"title": "Provider compatibility smoke", "instruction":
            "Create provider-smoke.txt with exactly the text 'Provider tool round trip.' followed by one newline. "
            "The file is new. Call mutate_file directly with path=provider-smoke.txt, expected_digest=null, "
            "and content. After the result, acknowledge completion in one short sentence."})
        assert response.status_code == 201
        page = response.json()["page"]["id"]
        deadline = time.monotonic() + 150
        while True:
            view = client.get(f"/api/pages/{page}").json()
            execution = view["execution"]
            if execution["turnId"] and execution["status"] in {"idle", "error", "needs_attention"}:
                break
            assert time.monotonic() < deadline, "The live provider did not finish in time."
            time.sleep(.1)
        assert execution["status"] == "idle", execution.get("failureCode")
        assert (tmp_path / "workspace" / "provider-smoke.txt").read_bytes() == expected
        print(json.dumps({"profile": config.resolved_profile, "model": config.model,
            "requests": calls, "file_sha256": hashlib.sha256(expected).hexdigest(),
            "usage": execution["usage"]}))
