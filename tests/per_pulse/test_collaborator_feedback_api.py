"""Explicit original-Work collaborator feedback routes; no model or reward fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from per_pulse.app import create_app


def test_collaborator_feedback_requires_source_and_typed_operator_consent(tmp_path):
    with TestClient(create_app(tmp_path, model_factory=None), base_url="http://localhost") as client:
        created = client.post("/api/pages", json={
            "title": "Original reviewed work", "instruction": "Review the source.",
        })
        assert created.status_code == 201, created.text
        work = created.json()["page"]["id"]
        body = {
            "expectedRevision": 0,
            "content": "Check the startup evidence.",
            "scope": "work",
            "withdrawn": False,
        }
        home = "/api/pages/home/team/choices/unrelated/feedback"
        original = f"/api/pages/{work}/team/choices/unrelated/feedback"
        assert client.get(home).status_code == 404
        assert client.put(home, json=body).status_code == 404
        assert client.get(original).status_code == 404
        # The no-provider, no-workspace-actions app is explicitly read-only.
        # Mutating feedback is refused before native source checks; GET still
        # verifies the missing source without confusing 403 with a 404.
        forbidden = client.put(original, json=body)
        assert forbidden.status_code == 403, forbidden.text
        assert forbidden.json()["error"]["code"] == "feedback_readonly"
        for changes in (
            {"scope": "everyone"},
            {"expectedRevision": -1},
            {"expectedRevision": "0"},
            {"withdrawn": "false"},
            {"extra": "do not accept"},
        ):
            invalid = client.put(original, json={**body, **changes})
            assert invalid.status_code == 422, invalid.text
        denied = client.put(
            original, json=body, headers={"origin": "https://unrelated.invalid"},
        )
        assert denied.status_code == 403
        # A rating alone never creates a collaborator-scoped annotation.
        assert client.get(original).status_code == 404
