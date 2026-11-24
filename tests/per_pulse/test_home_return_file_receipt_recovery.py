"""Recover a file-bearing Home return from the native accepted input.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Real HTTP, World/Kernel/SQLite, source receipts and workspace files; no model.
"""
from __future__ import annotations

import pytest

from test_pages import client_for, new_page
from test_materials import upload


def test_home_return_recovers_original_materials_after_lost_receipt_and_changed_file(
    tmp_path, monkeypatch,
):
    with client_for(tmp_path, model_factory=None) as client:
        work_a = new_page(client, title="Work A: read the source", instruction="Check source facts.")
        work_b = new_page(client, title="Work B: deliver the report", instruction="Prepare a sourced report.")
        a, b = work_a["page"]["id"], work_b["page"]["id"]

        uploaded = upload(client, a, "report-source.csv", b"name,value\noriginal,37\n")
        assert uploaded.status_code == 201, uploaded.text
        source = uploaded.json()["material"]
        sent_a = client.post(f"/api/pages/{a}/messages", json={
            "content": "Preserve this source for Work B.", "materials": [source["id"]],
        })
        assert sent_a.status_code == 202, sent_a.text
        inherited = client.post(f"/api/pages/{b}/materials/reference", json={
            "path": source["path"], "sha256": source["sha256"],
        }, headers={
            "X-Per-Pulse-World": client.app.state.pages.runtime.world_id,
            "X-Per-Pulse-Service-Instance": client.app.state.pages.service_instance_id,
        })
        assert inherited.status_code == 201, inherited.text
        derived = inherited.json()["material"]
        assert derived["id"] != source["id"]
        assert derived["originPageId"] == a
        assert derived["originMaterialId"] == source["id"]
        sent_b = client.post(f"/api/pages/{b}/messages", json={
            "content": "Return the completed, source-bound result to Home.",
            "materials": [derived["id"]],
        })
        assert sent_b.status_code == 202, sent_b.text
        message_a, message_b = sent_a.json()["messageId"], sent_b.json()["messageId"]

        pages = client.app.state.pages
        request_id = "home-return-materials-lost-receipt-20261121"
        key = f"per-pulse-home-return:{pages.runtime.world_id}:{request_id}"
        return_text = "The Work B report is ready with its original source."
        original_save = pages._save

        def lost_receipt():
            raise OSError("Home return accepted, but response projection lost")

        monkeypatch.setattr(pages, "_save", lost_receipt)
        with pytest.raises(OSError, match="projection lost"):
            pages.return_home(
                b, return_text, message_b,
                include_materials=True, request_id=request_id,
            )
        monkeypatch.setattr(pages, "_save", original_save)
        accepted = pages.runtime.causal_ledger.find_causal_event_by_idempotency(
            pages.runtime.world_id, key,
        )
        assert accepted is not None and accepted.center_id is None
        assert accepted.metadata["host_source_origin"] == {
            "pageId": b, "messageId": message_b,
        }
        assert derived["sha256"] in accepted.content
        assert derived["id"] in accepted.content
        accepted_id = accepted.id

    # A distinct later filesystem version must never be mistaken for the file
    # admitted in the original Home stimulus. No new reads or effects are owed.
    path = tmp_path / "workspace" / source["path"]
    path.write_bytes(b"name,value\nwrong-later-version,9000\n")

    payload = {
        "content": return_text, "messageId": message_b,
        "includeMaterials": True, "requestId": request_id,
    }
    with client_for(tmp_path, model_factory=None) as client:
        wrong_owner = client.post(f"/api/pages/{a}/return", json={
            **payload, "messageId": message_a,
        })
        assert wrong_owner.status_code == 409, wrong_owner.text
        assert wrong_owner.json()["error"]["code"] == "return_request_conflict"
        wrong_text = client.post(f"/api/pages/{b}/return", json={
            **payload, "content": "Replace the accepted report.",
        })
        assert wrong_text.status_code == 409, wrong_text.text
        assert wrong_text.json()["error"]["code"] == "return_request_conflict"
        wrong_material_mode = client.post(f"/api/pages/{b}/return", json={
            **payload, "includeMaterials": False,
        })
        assert wrong_material_mode.status_code == 409, wrong_material_mode.text

        restored = client.post(f"/api/pages/{b}/return", json=payload)
        assert restored.status_code == 201, restored.text
        receipt = restored.json()
        assert receipt["messageId"] == accepted_id
        home = receipt["view"]
        returned, = [m for m in home["messages"] if m["id"] == accepted_id]
        assert returned["sourcePageId"] == b
        assert returned["sourceMessageId"] == message_b
        assert [m["id"] for m in returned["materials"]] == [derived["id"]]
        assert returned["materials"][0]["sha256"] == source["sha256"]
        assert "wrong-later-version" not in returned["content"]
        assert returned["materialSuffixLength"] > 0
        assert sum(r["kind"] == "return" and r["sourceMessageId"] == accepted_id
                   for r in home["references"]) == 1

        again = client.post(f"/api/pages/{b}/return", json=payload)
        assert again.status_code == 201, again.text
        assert again.json()["messageId"] == accepted_id
        assert again.json()["reference"]["id"] == receipt["reference"]["id"]

        # Only an already-accepted request can recover historical attribution.
        # A fresh Home input must reject the now-modified file bytes.
        new_return = client.post(f"/api/pages/{b}/return", json={
            **payload, "requestId": "new-home-return-after-file-change",
        })
        assert new_return.status_code == 409, new_return.text
        assert new_return.json()["error"]["code"] == "material_changed"
        stale_download = client.get(f"/api/pages/{b}/materials/{derived['id']}")
        assert stale_download.status_code == 409, stale_download.text

    with client_for(tmp_path, model_factory=None) as client:
        home = client.get("/api/pages/home").json()
        returned, = [m for m in home["messages"] if m["id"] == accepted_id]
        assert returned["sourcePageId"] == b
        assert returned["sourceMessageId"] == message_b
        assert [m["id"] for m in returned["materials"]] == [derived["id"]]
        assert sum(r["kind"] == "return" and r["sourceMessageId"] == accepted_id
                   for r in home["references"]) == 1
        assert sum(m["id"] == accepted_id for m in home["messages"]) == 1
