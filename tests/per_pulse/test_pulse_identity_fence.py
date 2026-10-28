"""Native Activity operator writes must target the observed World and process.

This uses actual World/SQLite without a model, not synthetic learning quality.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from per_pulse.service import PulseSettings
from test_pulse_integration import client_for, page


def _headers(observation):
    return {
        "X-Per-Pulse-World": observation["workspaceId"],
        "X-Per-Pulse-Service-Instance": observation["serviceInstanceId"],
    }


def test_pulse_activity_write_fence_across_service_restart(tmp_path):
    pulse = PulseSettings(enabled=True, spontaneous_rate=0)
    with client_for(tmp_path, pulse=pulse, model_factory=None) as client:
        observation = client.get("/api/pulse/observation").json()
        assert observation["pulse"]["enabled"] is True
        valid = _headers(observation)
        assert client.patch("/api/pulse", json={"paused": True}, headers=valid).status_code == 200
        source = page(client, "Source", "Read an original local source.")
        target = page(client, "Receiver", "Continue in a distinct local Work.")
        invalid = (
            {**valid, "X-Per-Pulse-World": "another-world"},
            {**valid, "X-Per-Pulse-Service-Instance": "restarted-service"},
            {"X-Per-Pulse-World": observation["workspaceId"]},
            {"X-Per-Pulse-Service-Instance": observation["serviceInstanceId"]},
        )
        operations = (
            ("PATCH", "/api/pulse", {"paused": False}),
            ("PATCH", f"/api/pulse/contexts/{source}", {"paused": True}),
            ("POST", "/api/pulse/connections", {
                "sourcePageId": source, "targetPageId": target,
                "weight": 0.8, "kind": "excitatory",
            }),
            ("DELETE", "/api/pulse/connections", {
                "sourcePageId": source, "targetPageId": target,
            }),
        )
        for method, path, body in operations:
            for stale in invalid:
                response = client.request(method, path, json=body, headers=stale)
                assert response.status_code == 409, (method, path, response.text)
                assert response.json()["error"]["code"] == "pulse_workspace_changed"
        before = client.get("/api/pulse").json()
        assert before["paused"] is True
        assert before["heldContexts"] == []
        assert before["connections"] == []

        assert client.patch(f"/api/pulse/contexts/{source}", json={"paused": True},
                            headers=valid).status_code == 200
        assert client.post("/api/pulse/connections", json={
            "sourcePageId": source, "targetPageId": target,
            "weight": 0.8, "kind": "excitatory",
        }, headers=valid).status_code == 201
        saved = client.get("/api/pulse").json()
        assert any(held["pageId"] == source for held in saved["heldContexts"])
        assert len(saved["connections"]) == 1

    with client_for(tmp_path, pulse=pulse, model_factory=None) as client:
        reopened = client.get("/api/pulse/observation").json()
        assert reopened["workspaceId"] == observation["workspaceId"]
        assert reopened["serviceInstanceId"] != observation["serviceInstanceId"]
        assert client.patch("/api/pulse", json={"paused": False},
                            headers=valid).status_code == 409
        assert client.request("DELETE", "/api/pulse/connections", json={
            "sourcePageId": source, "targetPageId": target,
        }, headers=valid).status_code == 409
        retained = client.get("/api/pulse").json()
        assert retained["paused"] is True
        assert retained["connections"][0]["sourcePageId"] == source
        fresh = _headers(reopened)
        assert client.patch("/api/pulse", json={"paused": False},
                            headers=fresh).status_code == 200
        assert client.request("DELETE", "/api/pulse/connections", json={
            "sourcePageId": source, "targetPageId": target,
        }, headers=fresh).status_code == 200
        final = client.get("/api/pulse").json()
        assert final["paused"] is False
        assert final["connections"] == []
