"""Integration checks using the real World and Kernel with synthetic inputs.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No test contacts an external model. Online-path checks use the native HTTP
adapter with an explicitly injected httpx transport fixture.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from per_pulse.service import PageService
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core import HarnessScope
from pulse_system.agent.harness.base import HarnessError
from pulse_system.service.kernel_runtime import KernelHarnessRuntime


def client_for(path: Path, **kwargs):
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def new_page(client, title="Launch notes", instruction="Explore the launch.", **kwargs):
    response = client.post("/api/pages", json={"title": title, "instruction": instruction, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def send(client, page_id, content, **kwargs):
    response = client.post(f"/api/pages/{page_id}/messages", json={"content": content, **kwargs})
    assert response.status_code == 202, response.text
    return response.json()


def wait_for(client, page_id, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/pages/{page_id}")
        assert response.status_code == 200
        view = response.json()
        if predicate(view):
            return view
        time.sleep(0.05)
    pytest.fail(f"Page did not settle: {view}")


def test_offline_pages_links_and_selected_return_survive_restart(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        initial = client.get("/api/bootstrap").json()
        workspace_id = initial["workspaceId"]
        assert workspace_id == client.app.state.pages.runtime.world_id
        assert [p["id"] for p in initial["pages"]] == ["home"]
        assert initial["provider"]["label"] == "AI disconnected"
        home = send(client, "home", "Keep the original conversation here.")
        created = new_page(client, sourceMessageId=home["messageId"])
        page_id = created["page"]["id"]
        response = send(client, page_id, "Only this selected finding should return.")
        work_message = response["messageId"]
        returned = client.post(f"/api/pages/{page_id}/return", json={
            "content": "Selected finding: release one complete path.", "messageId": work_message,
        })
        assert returned.status_code == 201
        returned_id = returned.json()["messageId"]
        home_messages = returned.json()["view"]["messages"]
        assert [m["role"] for m in home_messages] == ["user", "user"]
        assert home_messages[-1]["sourcePageId"] == page_id
        assert home_messages[-1]["sourceMessageId"] == work_message
        assert home_messages[-1]["references"][0]["kind"] == "return"
        runtime = client.app.state.pages.runtime
        front = runtime.world.get_task_front(page_id)
        assert front.focal_engram_id == runtime.continuity_engram_id
        assert not runtime.running
        assert runtime.tick_count == 0
        assert runtime.kernel_service is not None
        home_event = runtime.causal_ledger.get_event(returned_id)
        assert home_event.center_id is None
        assert home_event.parent_event_id is None

    with client_for(tmp_path, model_factory=None) as client:
        assert client.get("/api/bootstrap").json()["workspaceId"] == workspace_id
        view = client.get(f"/api/pages/{page_id}").json()
        assert view["page"]["sourceMessageId"] == home["messageId"]
        assert view["page"]["status"] == "queued"
        assert [m["id"] for m in view["messages"]] == [created["initialMessageId"], work_message]
        assert view["breadcrumbs"] == [{"id": "home", "title": "Home"}, {"id": page_id, "title": "Launch notes"}]
        home_view = client.get("/api/pages/home").json()
        assert home_view["messages"][-1]["id"] == returned_id
        assert "Only this selected finding" not in json.dumps(home_view["messages"])
        assert all(m["role"] == "user" for m in home_view["messages"])


def test_repeated_references_are_navigation_without_context_copy(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        created = new_page(client, instruction="PRIVATE_WORK_MARKER")
        page_id = created["page"]["id"]
        first = send(client, "home", "Open Launch notes, then return to Launch notes.", references=[
            {"targetPageId": page_id, "label": "Launch notes"},
        ])
        second = send(client, "home", "Continue in the same Launch notes.", references=[
            {"targetPageId": page_id, "label": "Launch notes"},
        ])
        refs = [m["references"] for m in second["view"]["messages"]]
        assert refs[0][0]["targetPageId"] == refs[1][0]["targetPageId"] == page_id
        assert refs[0][0]["id"] != refs[1][0]["id"]
        duplicate = client.post("/api/pages/home/references", json={
            "targetPageId": page_id, "sourceMessageId": first["messageId"], "label": "Launch notes",
        })
        assert duplicate.status_code == 201
        assert duplicate.json()["reference"]["id"] == refs[0][0]["id"]
        assert "PRIVATE_WORK_MARKER" not in json.dumps(second["view"]["messages"])
        assert len(client.get(f"/api/pages/{page_id}").json()["messages"]) == 1
        assert len(client.get("/api/bootstrap").json()["pages"]) == 2
        renamed = client.patch(f"/api/pages/{page_id}", json={"title": "Launch plan"})
        assert renamed.json()["page"]["id"] == page_id
        assert renamed.json()["page"]["title"] == "Launch plan"


def test_invalid_source_or_target_never_writes_a_message_or_page(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        one = new_page(client)
        two = new_page(client, title="Reading room")
        first_id, second_id = one["page"]["id"], two["page"]["id"]
        bad_source = client.post("/api/pages", json={
            "title": "Invalid", "instruction": "Should not be created.",
            "sourcePageId": second_id, "sourceMessageId": one["initialMessageId"],
        })
        assert bad_source.status_code == 404
        assert bad_source.json()["error"]["code"] == "message_not_found"
        bad_return = client.post(f"/api/pages/{second_id}/return", json={
            "content": "Wrong source.", "messageId": one["initialMessageId"],
        })
        assert bad_return.status_code == 404
        bad_ref = client.post(f"/api/pages/{first_id}/messages", json={
            "content": "Should not be saved.", "references": [{"targetPageId": "missing"}],
        })
        assert bad_ref.status_code == 404
        assert len(client.get(f"/api/pages/{first_id}").json()["messages"]) == 1
        assert not client.get("/api/pages/home").json()["messages"]
        assert len(client.get("/api/bootstrap").json()["pages"]) == 3


def test_json_validation_and_local_browser_boundary(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        empty = client.post("/api/pages/home/messages", json={"content": "   "})
        assert empty.status_code == 422
        assert empty.json()["error"]["code"] == "invalid_request"
        external = client.post("/api/pages/home/messages", json={"content": "External write."}, headers={"Origin": "https://elsewhere.invalid"})
        assert external.status_code == 403
        form = client.post("/api/pages/home/messages", data={"content": "Form write."})
        assert form.status_code == 415
        assert not client.get("/api/pages/home").json()["messages"]
        assert client.get("/api/pages/missing").status_code == 404


def test_local_workspace_cannot_be_embedded_for_clickjacking(tmp_path):
    # A hostile site can attempt to iframe the loopback UI and trick a user
    # into clicking a native Harness approval even though it cannot read API
    # responses. Defend the page and rejected API responses alike.
    with client_for(tmp_path, model_factory=None) as client:
        attempts = (
            client.get("/"),
            client.get("/api/bootstrap"),
            client.get("/api/pages/missing"),
            client.post("/api/pages/home/messages", json={"content": "No."},
                        headers={"Origin": "https://untrusted.example"}),
            client.post("/api/pages/home/messages", data={"content": "No."}),
        )
        assert attempts[3].status_code == 403
        assert attempts[4].status_code == 415
        for response in attempts:
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
            assert response.headers["x-content-type-options"] == "nosniff"
        assert not client.get("/api/pages/home").json()["messages"]


def test_explicit_example_ignores_ambient_provider_and_never_invents_ai(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://configured.invalid/chat/completions")
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "ambient-model")

    def no_adapter(*args, **kwargs):
        pytest.fail("The example workspace must not create a model adapter.")

    monkeypatch.setattr(ChatHTTPModelAdapter, "__init__", no_adapter)
    with client_for(tmp_path / "example", example=True) as client:
        boot = client.get("/api/bootstrap").json()
        example_workspace_id = boot["workspaceId"]
        assert boot["example"] is True
        assert boot["provider"]["configured"] is False
        assert {p["title"] for p in boot["pages"]} == {"Home", "Launch notes", "Reading room", "Product ideas"}
        view = client.get("/api/pages/home").json()
        assert all(m["role"] == "user" for m in view["messages"])
        assert {r["label"] for r in view["messages"][-1]["references"]} == {"Launch notes", "Reading room"}
        send(client, "home", "A real edit to the example.")
        assert client.app.state.pages.runtime.tick_count == 0
    with client_for(tmp_path / "ordinary", model_factory=None) as client:
        ordinary = client.get("/api/bootstrap").json()
        assert ordinary["workspaceId"] != example_workspace_id
        assert len(ordinary["pages"]) == 1
        assert not client.get("/api/pages/home").json()["messages"]


def test_kernel_offline_opt_in_preserves_default_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("P_PULSE_CHAT_ENDPOINT", raising=False)
    monkeypatch.delenv("P_PULSE_CHAT_MODEL", raising=False)
    ordinary = KernelHarnessRuntime(db_path=tmp_path / "normal.db", workspace=tmp_path / "workspace")
    try:
        with pytest.raises(HarnessError) as error:
            ordinary.preflight()
        assert error.value.code == "provider_unconfigured"
    finally:
        ordinary.close()
    offline = KernelHarnessRuntime(db_path=tmp_path / "offline.db", workspace=tmp_path / "workspace", allow_unconfigured=True)
    try:
        offline.preflight()
        with pytest.raises(HarnessError) as error:
            offline._make_model(HarnessScope("world", "subject", "session", 0, "turn"))
        assert error.value.code == "provider_unconfigured"
    finally:
        offline.close()


def http_factory(monkeypatch, requests, *, status=200):
    monkeypatch.setenv("PER_PULSE_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        requests.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, text="Synthetic provider failure.")
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

    return factory


def test_native_http_turns_keep_page_contexts_separate_and_resume(tmp_path, monkeypatch):
    requests = []
    factory = http_factory(monkeypatch, requests)
    with client_for(tmp_path, model_factory=factory) as client:
        send(client, "home", "HOME_ONLY_MARKER")
        wait_for(client, "home", lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        one = new_page(client, instruction="FIRST_WORK_ONLY_MARKER")
        first_id = one["page"]["id"]
        wait_for(client, first_id, lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        two = new_page(client, title="Reading room", instruction="SECOND_WORK_ONLY_MARKER")
        second_id = two["page"]["id"]
        wait_for(client, second_id, lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        first_request = next(r for r in requests if "FIRST_WORK_ONLY_MARKER" in json.dumps(r["messages"]))
        second_request = next(r for r in requests if "SECOND_WORK_ONLY_MARKER" in json.dumps(r["messages"]))
        assert "HOME_ONLY_MARKER" not in json.dumps(first_request["messages"])
        assert "FIRST_WORK_ONLY_MARKER" not in json.dumps(second_request["messages"])
        assert "HOME_ONLY_MARKER" not in json.dumps(second_request["messages"])
        assert len(requests) == 3

    with client_for(tmp_path, model_factory=factory) as client:
        send(client, first_id, "CONTINUE_FIRST_WORK_MARKER")
        view = wait_for(client, first_id, lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 2)
        assert len(view["messages"]) == 4
        last = requests[-1]["messages"]
        assert "FIRST_WORK_ONLY_MARKER" in json.dumps(last)
        assert "CONTINUE_FIRST_WORK_MARKER" in json.dumps(last)
        assert "SECOND_WORK_ONLY_MARKER" not in json.dumps(last)
        returned = client.post(f"/api/pages/{first_id}/return", json={
            "content": "SELECTED_RETURN_ONLY_MARKER", "messageId": view["messages"][-1]["id"],
        })
        assert returned.status_code == 201
        home = wait_for(client, "home", lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 2)
        assert home["messages"][-2]["sourcePageId"] == first_id
        home_request = requests[-1]["messages"]
        assert "SELECTED_RETURN_ONLY_MARKER" in json.dumps(home_request)
        assert "HOME_ONLY_MARKER" in json.dumps(home_request)
        assert "FIRST_WORK_ONLY_MARKER" not in json.dumps(home_request)
        assert "SECOND_WORK_ONLY_MARKER" not in json.dumps(home_request)


@pytest.mark.parametrize(("history", "expected"), [
    (["queued", "failed"], "error"),
    (["queued", "uncertain"], "error"),
    (["queued", "blocked"], "needs_attention"),
    (["queued", "awaiting_approval"], "needs_attention"),
    (["failed", "queued"], "queued"),
    (["blocked", "queued"], "queued"),
    (["queued", "processing", "failed"], "working"),
    (["queued"], "queued"),
    ([], "idle"),
])
def test_latest_work_blocker_outweighs_older_queued_history(history, expected):
    """Home must not hide a newer failed/held Work behind old queued input.

    The overview consumes this same native page status and routes the person
    back to the original Work, rather than inventing an action in Home.
    """
    assert PageService._status([{"status": status} for status in history]) == expected


def test_provider_failure_stays_visible_without_fabricated_answer(tmp_path, monkeypatch):
    requests = []
    factory = http_factory(monkeypatch, requests, status=503)
    with client_for(tmp_path, model_factory=factory) as client:
        message = send(client, "home", "Keep this request even if the provider fails.")
        view = wait_for(client, "home", lambda v: v["page"]["status"] == "error")
        assert len(view["messages"]) == 1
        assert view["messages"][0]["id"] == message["messageId"]
        assert view["messages"][0]["role"] == "user"
        assert view["messages"][0]["status"] in {"failed", "uncertain"}
        assert len(requests) == 1


def test_old_source_link_resolves_in_own_page_without_exposing_other_context(tmp_path):
    """Deep links can open one older native event without loading all history."""
    with client_for(tmp_path, model_factory=None) as client:
        initial = send(client, "home", "Keep this old home source reachable.")
        source = initial["messageId"]
        original = next(row for row in client.get("/api/pages/home").json()["messages"] if row["id"] == source)
        work_id = new_page(client)["page"]["id"]

    with client_for(tmp_path, model_factory=None) as client:
        runtime = client.app.state.pages.runtime
        for index in range(505):
            runtime.inject(runtime.continuity_engram_id, f"Later home message {index}", source="user")
        recent = client.get("/api/pages/home").json()
        assert len(recent["messages"]) == 500 and recent["hasMore"]
        assert source not in {item["id"] for item in recent["messages"]}

        anchored = client.get("/api/pages/home", params={"sourceMessageId": source})
        assert anchored.status_code == 200, anchored.text
        view = anchored.json()
        assert view["earlierSourceMessageId"] == source
        assert view["messages"][0] == original
        assert view["messages"][1:] == recent["messages"]
        assert view["page"] == recent["page"]

        # A mutation on the selected old message must not drop that selection
        # from the returned projection and silently make the source disappear.
        linked = client.post("/api/pages/home/references", json={
            "targetPageId": work_id, "sourceMessageId": source, "label": "Launch notes",
        })
        assert linked.status_code == 201, linked.text
        linked_view = linked.json()["view"]
        assert linked_view["earlierSourceMessageId"] == source
        assert len(linked_view["messages"]) == 501
        assert linked_view["messages"][0]["id"] == source
        assert linked_view["messages"][0]["references"][-1]["targetPageId"] == work_id
        assert linked_view["messages"][1:] == recent["messages"]
        assert len(client.get("/api/pages/home").json()["messages"]) == 500

        latest_id = recent["messages"][-1]["id"]
        within_window = client.get("/api/pages/home", params={"sourceMessageId": latest_id}).json()
        assert within_window["earlierSourceMessageId"] is None
        assert len(within_window["messages"]) == 500
        assert client.get(f"/api/pages/{work_id}", params={"sourceMessageId": source}).status_code == 404
        assert client.get("/api/pages/home", params={"sourceMessageId": "nonexistent-id"}).status_code == 404


def test_collaboration_choice_read_is_original_work_scoped_and_offline(tmp_path):
    """No fabricated RIM preference in disconnected Home or unrelated Works."""
    with client_for(tmp_path, model_factory=None) as client:
        first = new_page(client, title="Original decision")["page"]["id"]
        second = new_page(client, title="Unrelated Work")["page"]["id"]
        assert client.get("/api/pages/home/team/choices").json() == {
            "workId": "home", "choices": [], "historyMayBeIncomplete": False,
        }
        for work in (first, second):
            response = client.get(f"/api/pages/{work}/team/choices")
            assert response.status_code == 200, response.text
            assert response.json() == {
                "workId": work, "choices": [], "historyMayBeIncomplete": False,
            }
        assert client.get("/api/pages/nonexistent-work/team/choices").status_code == 404

    # A restart cannot turn ordinary Work history into a fabricated choice.
    with client_for(tmp_path, model_factory=None) as client:
        assert client.get(f"/api/pages/{first}/team/choices").json()["choices"] == []
        assert client.get(f"/api/pages/{second}/team/choices").json()["choices"] == []
