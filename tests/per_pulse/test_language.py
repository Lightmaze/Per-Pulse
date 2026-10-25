"""Reply-language integration through the real World and Harness Kernel.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Provider requests use an explicitly injected transport fixture, never a live
model. Assertions cover the request policy, not a model's language ability.
"""
from __future__ import annotations

import json
from threading import Event

import httpx
import pytest

from per_pulse.language import LANGUAGE_POLICY, LanguageTurnAdvisor
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core import HarnessScope
from pulse_harness.core.errors import LeaseFenced, ScopeConflict, StoreUnavailable
from pulse_harness.core.provider import CancellationRequested, ModelRequest
from pulse_harness.core.transcript import TranscriptStore
from pulse_harness.core.turn_advice import TURN_ADVICE_PREFIX, TurnAdvice, decode_advice

from test_pages import client_for, http_factory, new_page, send, wait_for


def choose(client, language):
    response = client.patch("/api/settings", json={"responseLanguage": language})
    assert response.status_code == 200, response.text
    assert response.json() == {"responseLanguage": language}


def language_messages(request):
    return [message for message in request["messages"]
            if message["role"] == "system"
            and "Response language preference for this turn:" in message["content"]]


def test_settings_persist_without_changing_conversations_and_are_workspace_scoped(tmp_path):
    with client_for(tmp_path / "ordinary", model_factory=None) as client:
        assert client.get("/api/settings").json() == {"responseLanguage": "auto"}
        assert client.get("/api/bootstrap").json()["settings"] == {"responseLanguage": "auto"}
        send(client, "home", "Keep this English source and 中文原文 unchanged.")
        page = new_page(client, title="双语计划", instruction="保留原文。")
        page_id = page["page"]["id"]
        before_home = client.get("/api/pages/home").json()
        before_page = client.get(f"/api/pages/{page_id}").json()
        workspace_id = client.get("/api/bootstrap").json()["workspaceId"]
        choose(client, "zh-CN")
        assert client.get("/api/bootstrap").json()["settings"] == {"responseLanguage": "zh-CN"}
        assert client.get("/api/pages/home").json() == before_home
        assert client.get(f"/api/pages/{page_id}").json() == before_page
        assert client.app.state.pages.runtime.tick_count == 0
    with client_for(tmp_path / "ordinary", model_factory=None) as client:
        assert client.get("/api/settings").json() == {"responseLanguage": "zh-CN"}
        assert client.get("/api/bootstrap").json()["workspaceId"] == workspace_id
        assert client.get("/api/pages/home").json() == before_home
        assert client.get(f"/api/pages/{page_id}").json() == before_page
    with client_for(tmp_path / "other", model_factory=None) as client:
        assert client.get("/api/settings").json() == {"responseLanguage": "auto"}
        assert client.get("/api/bootstrap").json()["workspaceId"] != workspace_id
    with client_for(tmp_path / "example", example=True) as client:
        assert client.get("/api/settings").json() == {"responseLanguage": "auto"}
        choose(client, "en")
        assert client.app.state.pages.runtime.tick_count == 0


@pytest.mark.parametrize("payload", [
    {}, {"responseLanguage": "fr"}, {"responseLanguage": "zh"},
    {"responseLanguage": None}, {"responseLanguage": True},
    {"responseLanguage": 1}, {"responseLanguage": ["en"]},
    {"responseLanguage": "en", "interfaceLanguage": "zh-CN"},
])
def test_settings_reject_invalid_requests_without_writing(tmp_path, payload):
    with client_for(tmp_path, model_factory=None) as client:
        choose(client, "zh-CN")
        response = client.patch("/api/settings", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert client.get("/api/settings").json() == {"responseLanguage": "zh-CN"}
        assert not client.get("/api/pages/home").json()["messages"]


def test_settings_use_the_existing_local_browser_boundary(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        blocked = client.patch("/api/settings", json={"responseLanguage": "en"},
                               headers={"Origin": "https://elsewhere.invalid"})
        assert blocked.status_code == 403
        form = client.patch("/api/settings", data={"responseLanguage": "en"})
        assert form.status_code == 415
        assert client.get("/api/settings").json() == {"responseLanguage": "auto"}


def test_home_and_work_apply_new_preferences_with_native_receipts(tmp_path, monkeypatch):
    requests = []
    factory = http_factory(monkeypatch, requests)
    with client_for(tmp_path, model_factory=factory) as client:
        choose(client, "zh-CN")
        send(client, "home", "HOME_LANGUAGE_FIRST")
        wait_for(client, "home", lambda view: len(view["messages"]) == 2)
        first = json.dumps(requests[0], ensure_ascii=False)
        assert len(language_messages(requests[0])) == 1
        assert "Simplified Chinese" in language_messages(requests[0])[0]["content"]
        assert "translation target" in language_messages(requests[0])[0]["content"]

        runtime = client.app.state.pages.runtime
        home_scope = runtime._harness.scope_for(runtime.continuity_engram_id)
        transcript = TranscriptStore(runtime.kernel_service.store)
        receipts = [decode_advice(message.content)
                    for message in transcript.messages(home_scope)
                    if message.item_id.startswith(TURN_ADVICE_PREFIX)]
        assert len(receipts) == 1
        assert receipts[0]["receipt"]["per_pulse_language"] == {
            "policy": LANGUAGE_POLICY, "responseLanguage": "zh-CN",
        }
        # Public chat remains just the real request and real adapter result.
        assert [message["role"] for message in client.get("/api/pages/home").json()["messages"]] == ["user", "assistant"]

        choose(client, "en")
        page = new_page(client, instruction="WORK_LANGUAGE_FIRST")
        page_id = page["page"]["id"]
        wait_for(client, page_id, lambda view: len(view["messages"]) == 2)
        assert "English" in language_messages(requests[1])[0]["content"]
        assert "HOME_LANGUAGE_FIRST" not in json.dumps(requests[1]["messages"])

        send(client, "home", "HOME_LANGUAGE_SECOND; translate the answer into French.")
        wait_for(client, "home", lambda view: len(view["messages"]) == 4)
        assert len(language_messages(requests[2])) == 1
        assert "English" in language_messages(requests[2])[0]["content"]
        assert "Simplified Chinese" not in json.dumps(requests[2]["messages"])
        assert "translate the answer into French" in json.dumps(requests[2]["messages"])
        assert json.dumps(requests[0], ensure_ascii=False) == first
        receipts = [decode_advice(message.content)
                    for message in transcript.messages(home_scope)
                    if message.item_id.startswith(TURN_ADVICE_PREFIX)]
        assert [r["receipt"]["per_pulse_language"]["responseLanguage"] for r in receipts] == ["zh-CN", "en"]
        assert receipts[0]["scope_digest"] != receipts[1]["scope_digest"]

    # Restart keeps the selection and native history; obsolete advice does
    # not leak into the next turn even when the session runner is reconstructed.
    with client_for(tmp_path, model_factory=factory) as client:
        assert client.get("/api/settings").json() == {"responseLanguage": "en"}
        choose(client, "zh-CN")
        send(client, page_id, "WORK_LANGUAGE_SECOND")
        wait_for(client, page_id, lambda view: len(view["messages"]) == 4)
        assert len(language_messages(requests[3])) == 1
        assert "Simplified Chinese" in language_messages(requests[3])[0]["content"]
        assert "WORK_LANGUAGE_FIRST" in json.dumps(requests[3]["messages"])
        assert "HOME_LANGUAGE_FIRST" not in json.dumps(requests[3]["messages"])
        choose(client, "auto")
        send(client, "home", "HOME_LANGUAGE_AUTO")
        wait_for(client, "home", lambda view: len(view["messages"]) == 6)
        assert language_messages(requests[4]) == []
        assert len(requests) == 5  # Settings never trigger a model request.


def test_composition_keeps_existing_advisor_receipts_and_does_not_resample_mid_turn():
    class Store:
        index = 0

        def next_model_round_index(self, scope):
            return self.index

    state = {"language": "en"}
    seen = []
    previous_receipt = {"plugin": "expert_computation", "mode": "semantic", "fork": {"rounds": []}}

    class Previous:
        continues = True

        def advise(self, request, *, cancel_event):
            seen.append((request, cancel_event))
            # Simulate a setting change while an existing advisor computes.
            state["language"] = "zh-CN"
            return TurnAdvice(context="Existing advice.", receipt=previous_receipt)

    store = Store()
    advisor = LanguageTurnAdvisor(store, lambda: state["language"], Previous())
    request = ModelRequest(HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn"), (), (), 0)
    cancelled = Event()
    initial = advisor.advise(request, cancel_event=cancelled)
    assert advisor.continues
    assert initial.receipt["plugin"] == "expert_computation"
    assert initial.receipt["fork"] == previous_receipt["fork"]
    assert initial.receipt["per_pulse_language"]["responseLanguage"] == "en"
    assert "Existing advice." in initial.context
    assert "English" in initial.context
    assert "per_pulse_language" not in previous_receipt
    store.index = 1
    continuation = advisor.advise(request, cancel_event=cancelled)
    assert continuation == TurnAdvice(context="Existing advice.", receipt=previous_receipt)
    assert seen == [(request, cancelled), (request, cancelled)]


def test_inflight_language_change_applies_after_native_tool_rounds_finish(tmp_path, monkeypatch):
    requests = []
    first_request_started, release_first_request = Event(), Event()
    monkeypatch.setenv("PER_PULSE_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            first_request_started.set()
            assert release_first_request.wait(5), "Test did not release the first provider request."
            chunks = [
                {"choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": 0, "id": "language-read", "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"language-note.txt"}'},
                }]}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ]
        else:
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "Transport fixture response."}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
        stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "transport-fixture", credential_env="PER_PULSE_TEST_KEY"),
            transport=httpx.MockTransport(handler),
        )

    with client_for(tmp_path, model_factory=factory) as client:
        (tmp_path / "workspace" / "language-note.txt").write_text("Original source.", encoding="utf-8")
        choose(client, "zh-CN")
        send(client, "home", "Read language-note.txt and explain it.")
        try:
            assert first_request_started.wait(5)
            choose(client, "en")
        finally:
            release_first_request.set()
        wait_for(client, "home", lambda view: len(view["messages"]) == 2)
        assert len(requests) == 2
        assert any(message["role"] == "tool" for message in requests[1]["messages"])
        for request in requests:
            assert len(language_messages(request)) == 1
            assert "Simplified Chinese" in language_messages(request)[0]["content"]
        send(client, "home", "Continue with the updated preference.")
        wait_for(client, "home", lambda view: len(view["messages"]) == 4)
        assert len(requests) == 3
        assert len(language_messages(requests[2])) == 1
        assert "English" in language_messages(requests[2])[0]["content"]
        assert "Simplified Chinese" not in json.dumps(requests[2]["messages"])


@pytest.mark.parametrize("failure_mode", ["raises", "wrong_type", "invalid_context", "invalid_receipt"])
def test_optional_advisor_failure_preserves_language_and_a_skip_receipt(failure_mode):
    class Store:
        def next_model_round_index(self, scope):
            return 0

    class FailedAdvisor:
        def advise(self, request, *, cancel_event):
            if failure_mode == "wrong_type":
                return "invalid advice"
            if failure_mode == "invalid_context":
                return TurnAdvice(context=42)
            if failure_mode == "invalid_receipt":
                return TurnAdvice(receipt={"not_json": object()})
            raise RuntimeError("Private detail must not enter a receipt.")

    advisor = LanguageTurnAdvisor(Store(), lambda: "zh-CN", FailedAdvisor())
    request = ModelRequest(HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn"), (), (), 0)
    advice = advisor.advise(request, cancel_event=Event())
    assert "Simplified Chinese" in advice.context
    assert advice.receipt == {
        "status": "skipped", "reason": "advisor_failed_or_invalid",
        "per_pulse_language": {"policy": LANGUAGE_POLICY, "responseLanguage": "zh-CN"},
    }


@pytest.mark.parametrize("failure", [CancellationRequested, LeaseFenced, ScopeConflict, StoreUnavailable])
def test_language_never_swallows_native_control_failures(failure):
    class Store:
        def next_model_round_index(self, scope):
            return 0

    class InterruptedAdvisor:
        def advise(self, request, *, cancel_event):
            raise failure("Native control failure.")

    advisor = LanguageTurnAdvisor(Store(), lambda: "en", InterruptedAdvisor())
    request = ModelRequest(HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn"), (), (), 0)
    with pytest.raises(failure):
        advisor.advise(request, cancel_event=Event())
