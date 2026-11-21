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


def test_work_send_retries_preserve_original_cross_work_navigation(tmp_path, monkeypatch):
    """Work B's accepted input cannot acquire Work C's link on HTTP retry."""
    with client_for(tmp_path, model_factory=None) as client:
        source = new_page(client, title="Work A source",
                          instruction="Record a useful source.")
        receiving = new_page(client, title="Work B receiver",
                             instruction="Continue the original source.")
        unrelated = new_page(client, title="Work C unrelated",
                             instruction="Do unrelated work.")
        source_id, receiver_id, other_id = (
            source["page"]["id"], receiving["page"]["id"],
            unrelated["page"]["id"],
        )
    request_id = "work-b-source-binding-20261121"
    payload = {
        "content": "Choose a tool for the next real document.",
        "references": [{"targetPageId": source_id, "label": "Work A evidence"}],
        "requestId": request_id,
    }
    with client_for(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        original_save = pages._save

        def lost_receipt():
            raise OSError("Work B receipt lost after admission")

        monkeypatch.setattr(pages, "_save", lost_receipt)
        with pytest.raises(OSError, match="Work B receipt lost"):
            pages.send(receiver_id, payload["content"], payload["references"],
                       request_id=request_id)
        monkeypatch.setattr(pages, "_save", original_save)
        event = pages.runtime.causal_ledger.find_causal_event_by_idempotency(
            pages.runtime.world_id,
            f"per-pulse-user-message:{pages.runtime.world_id}:{request_id}",
        )
        assert event is not None
        assert event.center_id == pages._front(receiver_id).center_id

    with client_for(tmp_path, model_factory=None) as client:
        changed = client.post(f"/api/pages/{receiver_id}/messages", json={
            **payload,
            "references": [{"targetPageId": other_id, "label": "Work A evidence"}],
        })
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "message_request_conflict"
        recovered = client.post(f"/api/pages/{receiver_id}/messages", json=payload)
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["messageId"] == event.id
        restored = [r for r in recovered.json()["view"]["references"]
                    if r["sourceMessageId"] == event.id]
        assert len(restored) == 1
        assert restored[0]["targetPageId"] == source_id
        assert restored[0]["label"] == "Work A evidence"
        assert [m["id"] for m in recovered.json()["view"]["messages"]].count(event.id) == 1


def test_legacy_plain_send_retry_keeps_later_manual_reference(tmp_path):
    """A previously added link is not a new source claim by an empty retry."""
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client, title="Later link", instruction="Keep existing Work.")
        pages = client.app.state.pages
        request_id = "old-send-with-later-manual-link-20261121"
        event_id = pages.runtime.inject(
            pages.runtime.continuity_engram_id, "An older message.",
            source="user",
            idempotency_key=f"per-pulse-user-message:{pages.runtime.world_id}:{request_id}",
        )
        pages.add_reference("home", work["page"]["id"], source_message_id=event_id)
        retry = client.post("/api/pages/home/messages", json={
            "content": "An older message.", "requestId": request_id,
        })
        assert retry.status_code == 202, retry.text
        assert retry.json()["messageId"] == event_id
        refs = [r for r in retry.json()["view"]["references"]
                if r["sourceMessageId"] == event_id]
        assert len(refs) == 1
        assert refs[0]["targetPageId"] == work["page"]["id"]


@pytest.mark.parametrize("changed_source", [False, True])
def test_file_bearing_work_send_recovers_after_lost_receipt_without_reusing_stale_file(
        tmp_path, monkeypatch, changed_source):
    """One admitted file-bearing Work input remains one input and one source after restart."""
    from per_pulse.materials import material_footer
    from test_materials import upload

    request_id = "file-bearing-lost-send"
    source_bytes = b"Final decision data: quantity=7\\n"
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client, title="Work A source", instruction="Study the original data.")["page"]["id"]
        uploaded = upload(client, work, "evidence.csv", source_bytes)
        assert uploaded.status_code == 201
        record = uploaded.json()["material"]
        pages = client.app.state.pages
        original_save = pages._save

        def lose_receipt():
            raise OSError("lost receipt after native file-bearing Send")

        monkeypatch.setattr(pages, "_save", lose_receipt)
        with pytest.raises(OSError, match="lost receipt"):
            pages.send(work, "Read the attached evidence before deciding.",
                       materials=[record["id"]], request_id=request_id)
        monkeypatch.setattr(pages, "_save", original_save)
        event = pages.runtime.causal_ledger.find_causal_event_by_idempotency(
            pages.runtime.world_id, f"per-pulse-user-message:{pages.runtime.world_id}:{request_id}")
        assert event is not None
        assert event.content.endswith(material_footer([record]))
        assert source_bytes.decode().strip() not in event.content

    with client_for(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        historical = next(m for m in client.get(f"/api/pages/{work}").json()["messages"]
                          if m["id"] == event.id)
        assert historical["materials"] == [], "The lost projection must really be absent."
        if changed_source:
            (tmp_path / "workspace" / record["path"]).write_bytes(b"REPLACED_NOT_ORIGINAL")
        payload = {"content": "Read the attached evidence before deciding.",
                   "materials": [record["id"]], "requestId": request_id}
        recovered = client.post(f"/api/pages/{work}/messages", json=payload)
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["messageId"] == event.id
        message = next(m for m in recovered.json()["view"]["messages"] if m["id"] == event.id)
        assert message["materials"] == [record]
        assert message["materialSuffixLength"] == len(material_footer([record]))
        assert sum(m["id"] == event.id for m in recovered.json()["view"]["messages"]) == 1

        other = upload(client, work, "different.csv", b"Different source.")
        assert other.status_code == 201
        retarget = client.post(f"/api/pages/{work}/messages", json={
            **payload, "materials": [other.json()["material"]["id"]]})
        assert retarget.status_code == 409
        assert retarget.json()["error"]["code"] == "message_request_conflict"
        if changed_source:
            # Historical attribution survives, but this file cannot be read
            # or attached to a new model turn after the bytes have changed.
            preview = client.get(f"/api/pages/{work}/materials/{record['id']}")
            assert preview.status_code == 409
            fresh = client.post(f"/api/pages/{work}/messages", json={
                **payload, "requestId": "fresh-file-send"})
            assert fresh.status_code == 409
            assert fresh.json()["error"]["code"] == "material_changed"
        else:
            preview = client.get(f"/api/pages/{work}/materials/{record['id']}")
            assert preview.status_code == 200
            assert preview.json()["content"].encode() == source_bytes

        # Continue an ordinary sourced Work B and return its chosen result Home.
        # B receives the selected historical text, never the changed file bytes.
        receiving = new_page(
            client, title="Work B final", instruction="Produce a final brief.",
            sourcePageId=work, sourceMessageId=event.id,
            sourceExcerpt="Read the attached evidence before deciding.",
        )["page"]["id"]
        result = client.post(f"/api/pages/{receiving}/messages", json={
            "content": "Final brief: keep source attribution and disclose unread files."})
        assert result.status_code == 202
        brought = client.post(f"/api/pages/{receiving}/return", json={
            "messageId": result.json()["messageId"],
            "content": "Finished brief for Home with Work B source.",
        })
        assert brought.status_code == 201
        home_id = brought.json()["messageId"]

    with client_for(tmp_path, model_factory=None) as client:
        restored = next(m for m in client.get(f"/api/pages/{work}").json()["messages"]
                        if m["id"] == event.id)
        assert restored["materials"] == [record]
        assert sum(m["id"] == event.id
                   for m in client.get(f"/api/pages/{work}").json()["messages"]) == 1
        home = next(m for m in client.get("/api/pages/home").json()["messages"]
                    if m["id"] == home_id)
        assert home["sourcePageId"] == receiving
        assert home["sourceMessageId"] == result.json()["messageId"]
        assert home["references"][0]["targetPageId"] == receiving
