"""A returned Work result remains one native Home input across lost receipts.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No model is configured: tests use the real World, causal ledger and SQLite.
"""
from __future__ import annotations

import pytest

from test_pages import client_for, new_page


def test_home_return_retry_survives_interrupted_projection_and_restart(tmp_path, monkeypatch):
    with client_for(tmp_path, model_factory=None) as client:
        source = new_page(client, title="Work A", instruction="Original source note.")
        source_id = source["page"]["id"]
        source_message_id = source["initialMessageId"]

    request_id = "return-retry-20261119-a"
    payload = {
        "content": "Return one usable decision to Home.",
        "messageId": source_message_id,
        "requestId": request_id,
    }
    key = None
    with client_for(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        key = f"per-pulse-home-return:{pages.runtime.world_id}:{request_id}"
        original_save = pages._save

        def interrupted_save():
            raise OSError("Simulated response loss after native Home admission")

        monkeypatch.setattr(pages, "_save", interrupted_save)
        with pytest.raises(OSError, match="Simulated response loss"):
            pages.return_home(source_id, payload["content"], source_message_id,
                              request_id=request_id)
        monkeypatch.setattr(pages, "_save", original_save)
        admitted = pages.runtime.causal_ledger.find_causal_event_by_idempotency(
            pages.runtime.world_id, key)
        assert admitted is not None
        assert admitted.center_id is None
        assert admitted.source.value == "user"

    with client_for(tmp_path, model_factory=None) as client:
        restored = client.post(f"/api/pages/{source_id}/return", json=payload)
        assert restored.status_code == 201, restored.text
        first = restored.json()
        assert first["messageId"] == admitted.id
        returned = next(m for m in first["view"]["messages"] if m["id"] == admitted.id)
        assert returned["sourcePageId"] == source_id
        assert returned["sourceMessageId"] == source_message_id
        assert returned["references"][0]["kind"] == "return"

        rename = client.patch(f"/api/pages/{source_id}", json={"title": "Renamed Work A"})
        assert rename.status_code == 200, rename.text
        repeated = client.post(f"/api/pages/{source_id}/return", json=payload)
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["messageId"] == admitted.id
        assert repeated.json()["reference"]["id"] == first["reference"]["id"]
        home = client.get("/api/pages/home").json()
        assert sum(m["id"] == admitted.id for m in home["messages"]) == 1
        assert sum(r["kind"] == "return" and r["sourceMessageId"] == admitted.id
                   for r in home["references"]) == 1

        conflict = client.post(f"/api/pages/{source_id}/return", json={
            **payload, "content": "A different Home return must have a new request identity.",
        })
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "return_request_conflict"
        assert [m["id"] for m in client.get("/api/pages/home").json()["messages"]] == [
            m["id"] for m in home["messages"]
        ]

    with client_for(tmp_path, model_factory=None) as client:
        persisted = client.get("/api/pages/home").json()
        returned = next(m for m in persisted["messages"] if m["id"] == admitted.id)
        assert returned["sourcePageId"] == source_id
        assert returned["sourceMessageId"] == source_message_id
        assert len([r for r in persisted["references"]
                    if r["kind"] == "return" and r["sourceMessageId"] == admitted.id]) == 1


def test_return_request_identity_rejects_other_source_after_success(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        first = new_page(client, title="Work A", instruction="Same source text.")
        second = new_page(client, title="Work B", instruction="Same source text.")
        payload = {
            "content": "A shared sentence.",
            "messageId": first["initialMessageId"],
            "requestId": "return-reuse-other-source-a",
        }
        accepted = client.post(f"/api/pages/{first['page']['id']}/return", json=payload)
        assert accepted.status_code == 201, accepted.text
        rejected = client.post(f"/api/pages/{second['page']['id']}/return", json={
            **payload, "messageId": second["initialMessageId"],
        })
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "return_request_conflict"
        home = client.get("/api/pages/home").json()
        assert sum(m["content"] == payload["content"] for m in home["messages"]) == 1
