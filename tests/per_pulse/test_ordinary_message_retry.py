"""Ordinary Home/Work composer retries recover the original native input.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
This uses the real World, CausalLedger and SQLite without any model fixture.
"""
from __future__ import annotations

import pytest

from test_pages import client_for, new_page


def test_home_composer_lost_receipt_recovers_link_and_original_input(tmp_path, monkeypatch):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client, title="Original source", instruction="Keep this Work private.")
        work_id = work["page"]["id"]

    request_id = "composed-home-20261120-a"
    payload = {
        "content": "A Home decision linked to Original source.",
        "references": [{"targetPageId": work_id, "label": "Original source"}],
        "requestId": request_id,
    }
    with client_for(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        key = f"per-pulse-user-message:{pages.runtime.world_id}:{request_id}"
        original_save = pages._save

        def lost_receipt():
            raise OSError("HTTP receipt lost after ordinary Home admission")

        monkeypatch.setattr(pages, "_save", lost_receipt)
        with pytest.raises(OSError, match="HTTP receipt lost"):
            pages.send("home", payload["content"], payload["references"],
                       request_id=request_id)
        monkeypatch.setattr(pages, "_save", original_save)
        admitted = pages.runtime.causal_ledger.find_causal_event_by_idempotency(
            pages.runtime.world_id, key)
        assert admitted is not None
        assert admitted.center_id is None
        assert admitted.source.value == "user"

    with client_for(tmp_path, model_factory=None) as client:
        retried = client.post("/api/pages/home/messages", json=payload)
        assert retried.status_code == 202, retried.text
        first = retried.json()
        assert first["messageId"] == admitted.id
        recovered = next(m for m in first["view"]["messages"] if m["id"] == admitted.id)
        assert recovered["references"][0]["targetPageId"] == work_id
        assert recovered["references"][0]["label"] == "Original source"

        repeated = client.post("/api/pages/home/messages", json=payload)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["messageId"] == admitted.id
        assert repeated.json()["view"]["messages"][-1]["id"] == admitted.id
        assert sum(m["id"] == admitted.id
                   for m in client.get("/api/pages/home").json()["messages"]) == 1

        conflict = client.post("/api/pages/home/messages", json={
            **payload, "content": "A different request must have another identity.",
        })
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "message_request_conflict"

        another_page = client.post(f"/api/pages/{work_id}/messages", json=payload)
        assert another_page.status_code == 409, another_page.text
        assert another_page.json()["error"]["code"] == "message_request_conflict"

    with client_for(tmp_path, model_factory=None) as client:
        persisted = client.get("/api/pages/home").json()
        assert sum(m["id"] == admitted.id for m in persisted["messages"]) == 1
        assert sum(r["sourceMessageId"] == admitted.id and r["targetPageId"] == work_id
                   for r in persisted["references"]) == 1


def test_work_composer_retry_is_one_native_input_but_a_new_send_remains_possible(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client, title="Independent decision", instruction="Make a real artifact.")
        work_id = work["page"]["id"]
        original_id = work["initialMessageId"]
        payload = {
            "content": "Use the original source and finish the decision.",
            "requestId": "work-composer-20261120-a",
        }
        first = client.post(f"/api/pages/{work_id}/messages", json=payload)
        assert first.status_code == 202, first.text
        first_id = first.json()["messageId"]

    with client_for(tmp_path, model_factory=None) as client:
        repeated = client.post(f"/api/pages/{work_id}/messages", json=payload)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["messageId"] == first_id
        view = client.get(f"/api/pages/{work_id}").json()
        assert [m["id"] for m in view["messages"]] == [original_id, first_id]

        revised = client.post(f"/api/pages/{work_id}/messages", json={
            **payload, "content": "This changed content requires a fresh request.",
        })
        assert revised.status_code == 409
        assert revised.json()["error"]["code"] == "message_request_conflict"

        fresh = client.post(f"/api/pages/{work_id}/messages", json={
            **payload, "requestId": "work-composer-20261120-b",
        })
        assert fresh.status_code == 202
        assert fresh.json()["messageId"] != first_id
        view = client.get(f"/api/pages/{work_id}").json()
        assert [m["id"] for m in view["messages"]] == [
            original_id, first_id, fresh.json()["messageId"],
        ]


def test_legacy_send_without_request_identity_remains_compatible(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        first = client.post("/api/pages/home/messages", json={"content": "An intentional first send."})
        second = client.post("/api/pages/home/messages", json={"content": "An intentional second send."})
        assert first.status_code == second.status_code == 202
        assert first.json()["messageId"] != second.json()["messageId"]


def test_lost_composer_receipt_cannot_retarget_original_work_reference(tmp_path, monkeypatch):
    """Real native admission, receipt failure, SQLite restart and HTTP recovery."""
    with client_for(tmp_path, model_factory=None) as client:
        original = new_page(client, title="Original Work", instruction="Keep original history.")
        other = new_page(client, title="Other Work", instruction="Keep a separate history.")
        original_id, other_id = original["page"]["id"], other["page"]["id"]

    request_id = "message-original-link-20261121"
    payload = {
        "content": "Continue the evidence from Original Work.",
        "references": [{"targetPageId": original_id}],
        "requestId": request_id,
    }
    with client_for(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        original_save = pages._save

        def lose_receipt():
            raise OSError("lost receipt after the native message was accepted")

        monkeypatch.setattr(pages, "_save", lose_receipt)
        with pytest.raises(OSError, match="lost receipt"):
            pages.send("home", payload["content"], payload["references"],
                       request_id=request_id)
        monkeypatch.setattr(pages, "_save", original_save)
        event = pages.runtime.causal_ledger.find_causal_event_by_idempotency(
            pages.runtime.world_id,
            f"per-pulse-user-message:{pages.runtime.world_id}:{request_id}",
        )
        assert event is not None
        assert event.metadata["host_message_references"] == [{
            "targetPageId": original_id,
            "label": None,
            "displayLabel": "Original Work",
        }]

    with client_for(tmp_path, model_factory=None) as client:
        changed = client.post("/api/pages/home/messages", json={
            **payload, "references": [{"targetPageId": other_id}],
        })
        assert changed.status_code == 409, changed.text
        assert changed.json()["error"]["code"] == "message_request_conflict"

        # A title change after the original admission cannot change its
        # original reference label or source identity during replay.
        client.app.state.pages.rename(original_id, "Renamed Work")
        retry = client.post("/api/pages/home/messages", json=payload)
        assert retry.status_code == 202, retry.text
        assert retry.json()["messageId"] == event.id
        refs = [ref for ref in retry.json()["view"]["references"]
                if ref["sourceMessageId"] == event.id]
        assert len(refs) == 1
        assert refs[0]["targetPageId"] == original_id
        assert refs[0]["label"] == "Original Work"

        # A second retry is a projection of the first event, never a new
        # input, reference or downstream tool/action opportunity.
        again = client.post("/api/pages/home/messages", json=payload)
        assert again.status_code == 202
        assert again.json()["messageId"] == event.id
        assert len([ref for ref in again.json()["view"]["references"]
                    if ref["sourceMessageId"] == event.id]) == 1

    with client_for(tmp_path, model_factory=None) as client:
        home = client.get("/api/pages/home").json()
        assert sum(msg["id"] == event.id for msg in home["messages"]) == 1
        assert sum(ref["sourceMessageId"] == event.id
                   for ref in home["references"]) == 1


def test_legacy_interrupted_send_cannot_claim_new_reference(tmp_path):
    """Old unbound causal messages cannot invent links during migration."""
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client, title="Not the original source",
                        instruction="Stay separate.")
        pages = client.app.state.pages
        request_id = "old-unbound-reference-20261121"
        key = f"per-pulse-user-message:{pages.runtime.world_id}:{request_id}"
        event_id = pages.runtime.inject(
            pages.runtime.continuity_engram_id, "Legacy message.", source="user",
            idempotency_key=key,
        )
        assert pages.runtime.causal_ledger.get_event(event_id).metadata.get(
            "host_message_references") is None
        conflict = client.post("/api/pages/home/messages", json={
            "content": "Legacy message.",
            "references": [{"targetPageId": work["page"]["id"]}],
            "requestId": request_id,
        })
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "message_request_conflict"
        home = client.get("/api/pages/home").json()
        assert not any(ref["sourceMessageId"] == event_id
                       for ref in home["references"])
