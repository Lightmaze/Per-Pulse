"""One native Work and selected source survive lost create responses and process restart.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No model fixture or paid provider is used: these checks run the actual World,
CausalLedger, native Kernel setup and persistent SQLite files.
"""
from __future__ import annotations

import pytest

from test_pages import client_for, send


def _work_ids(client):
    return {item["id"] for item in client.get("/api/bootstrap").json()["pages"]
            if item["kind"] == "work"}


def test_lost_create_receipt_recovers_original_selected_work_and_source(tmp_path, monkeypatch):
    with client_for(tmp_path, model_factory=None) as client:
        original = send(client, "home", "Use the original result as an attributed source.")
        source_id = original["messageId"]

    payload = {
        "title": "Second Work from a selected Home source",
        "instruction": "Finish a practical document, then return it to Home.",
        "sourcePageId": "home",
        "sourceMessageId": source_id,
        "sourceExcerpt": "Use the original result as an attributed source.",
        "requestId": "create-source-20261121-a",
    }
    with client_for(tmp_path, model_factory=None) as client:
        service = client.app.state.pages
        original_save = service._save

        def lose_http_receipt():
            raise OSError("create response lost after first native Work input")

        monkeypatch.setattr(service, "_save", lose_http_receipt)
        with pytest.raises(OSError, match="create response lost"):
            service.create_page(
                payload["title"], payload["instruction"], payload["sourcePageId"],
                payload["sourceMessageId"], source_excerpt=payload["sourceExcerpt"],
                request_id=payload["requestId"],
            )
        monkeypatch.setattr(service, "_save", original_save)
        assert len(service.runtime.world.list_task_fronts()) == 1
        first = service.runtime.world.list_task_fronts()[0]
        first_events = service.runtime.causal_ledger.list_events(
            center_id=first.center_id, limit=100,
        )
        assert len(first_events) == 1
        admitted_id = first_events[0].id
        assert admitted_id

    with client_for(tmp_path, model_factory=None) as client:
        restored = client.post("/api/pages", json=payload)
        assert restored.status_code == 201, restored.text
        result = restored.json()
        assert result["page"]["id"] == first.id
        assert result["initialMessageId"] == admitted_id
        assert _work_ids(client) == {first.id}
        assert result["view"]["page"]["sourceMessageId"] == source_id
        first_message = result["view"]["messages"][0]
        assert first_message["id"] == admitted_id
        assert first_message["pageSource"]["excerpt"] == payload["sourceExcerpt"]
        assert first_message["pageSource"]["role"] == "user"
        assert result["reference"]["sourcePageId"] == "home"
        assert result["reference"]["targetPageId"] == first.id

        again = client.post("/api/pages", json=payload)
        assert again.status_code == 201, again.text
        assert again.json()["page"]["id"] == first.id
        assert again.json()["reference"]["id"] == result["reference"]["id"]
        assert len(client.get("/api/bootstrap").json()["pages"]) == 2

        changed = client.post("/api/pages", json={
            **payload, "instruction": "This is a different task and needs a fresh identity.",
        })
        assert changed.status_code == 409, changed.text
        assert changed.json()["error"]["code"] == "work_creation_conflict"
        assert _work_ids(client) == {first.id}

        separate = client.post("/api/pages", json={
            **payload, "requestId": "create-source-20261121-b",
        })
        assert separate.status_code == 201, separate.text
        assert separate.json()["page"]["id"] != first.id
        assert len(_work_ids(client)) == 2

    with client_for(tmp_path, model_factory=None) as client:
        assert len(_work_ids(client)) == 2
        recovered = client.get(f"/api/pages/{first.id}").json()
        assert recovered["messages"][0]["id"] == admitted_id
        assert recovered["messages"][0]["pageSource"]["role"] == "user"


def test_creation_resumes_original_unstarted_native_bundle(tmp_path, monkeypatch):
    payload = {
        "title": "Pending first input",
        "instruction": "Create a real Work with no second World.",
        "requestId": "create-unstarted-20261121",
    }
    with client_for(tmp_path, model_factory=None) as client:
        service = client.app.state.pages
        original_inject = service.runtime.inject

        def fail_before_first_input(*args, **kwargs):
            if kwargs.get("center_id") is not None:
                raise OSError("interrupted before native input")
            return original_inject(*args, **kwargs)

        monkeypatch.setattr(service.runtime, "inject", fail_before_first_input)
        with pytest.raises(OSError, match="interrupted before native input"):
            service.create_page(payload["title"], payload["instruction"],
                                request_id=payload["requestId"])
        assert len(service.runtime.world.list_task_fronts()) == 1
        original_front = service.runtime.world.list_task_fronts()[0]
        monkeypatch.setattr(service.runtime, "inject", original_inject)

    with client_for(tmp_path, model_factory=None) as client:
        completed = client.post("/api/pages", json=payload)
        assert completed.status_code == 201, completed.text
        assert completed.json()["page"]["id"] == original_front.id
        assert _work_ids(client) == {original_front.id}
        assert client.post("/api/pages", json=payload).json()["page"]["id"] == original_front.id
        rows = client.get(f"/api/pages/{original_front.id}").json()["messages"]
        assert len(rows) == 1
        assert rows[0]["id"] == completed.json()["initialMessageId"]
