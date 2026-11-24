"""The product HTTP boundary controls its existing Kernel without new authority."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient
import pytest

from per_pulse.app import create_app
from test_execution import _factory


def _wait(client, page_id, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/pages/{page_id}/execution")
        assert response.status_code == 200, response.text
        state = response.json()
        if predicate(state):
            return state
        time.sleep(0.025)
    pytest.fail(f"Execution did not reach the expected state: {state}")


def test_actions_require_explicit_profile_and_example_stays_read_only(tmp_path):
    for example, enabled in [(False, False), (False, True), (True, True)]:
        with TestClient(create_app(tmp_path / f"{example}-{enabled}", example=example,
                                  allow_workspace_actions=enabled, model_factory=None),
                        base_url="http://localhost") as client:
            bootstrap = client.get("/api/bootstrap").json()
            assert bootstrap["provider"]["workspaceActions"] is (enabled and not example)
            assert bootstrap["provider"]["configured"] is False
            state = client.get("/api/pages/home/execution").json()
            assert state["sessionId"] is None
            assert not state["canCancel"]


def test_http_approval_scope_retry_and_output(tmp_path, monkeypatch):
    requests = []
    with TestClient(create_app(tmp_path, model_factory=_factory(monkeypatch, requests),
                              allow_workspace_actions=True), base_url="http://localhost") as client:
        created = client.post("/api/pages", json={"title": "A reviewed note", "instruction": "WRITE_ONE"})
        assert created.status_code == 201
        page_id = created.json()["page"]["id"]
        state = _wait(client, page_id, lambda s: bool(s["pendingActions"]) and s["pendingActions"][0]["canResolve"])
        action = state["pendingActions"][0]
        assert client.get(f"/api/pages/{page_id}").json()["page"]["status"] == "needs_attention"
        wrong = client.post(f"/api/pages/home/execution/actions/{action['actionId']}", json={"decision": "allow_once"})
        assert wrong.status_code == 409
        assert not (tmp_path / "workspace/one.txt").exists()
        path = f"/api/pages/{page_id}/execution/actions/{action['actionId']}"
        allowed = client.post(path, json={"decision": "allow_once"})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["accepted"]
        assert allowed.json()["view"]["page"]["id"] == page_id
        _wait(client, page_id, lambda s: s["status"] == "idle")
        assert (tmp_path / "workspace/one.txt").read_text() == "Written by one.txt.\n"
        calls = len(requests)
        repeated = client.post(path, json={"decision": "allow_once"})
        assert repeated.json()["repeated"] is True
        assert len(requests) == calls


def test_control_requests_keep_local_origin_and_typed_identity_boundary(tmp_path):
    with TestClient(create_app(tmp_path, model_factory=None), base_url="http://localhost") as client:
        stop = "/api/pages/home/execution/stop"
        assert client.post(stop, json={}).status_code == 422
        assert client.post(stop, json={"turnId": "old", "scope": "another"}).status_code == 422
        assert client.post(stop, json={"turnId": "old"}, headers={"origin": "https://unrelated.invalid"}).status_code == 403
        assert client.post(stop, content="turnId=old").status_code == 415
        assert client.post("/api/pages/home/execution/actions/unknown", json={"decision": "allow_forever"}).status_code == 422
        assert client.get("/api/pages/missing/execution").status_code == 404
