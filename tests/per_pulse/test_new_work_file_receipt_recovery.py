"""Recover a source-file Work after a lost HTTP Create receipt.

The model is an explicit synthetic HTTP decision; the producer's native
approval, file write, material receipt, second Work, Home return and restart
use the real World/Kernel/SQLite path.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import pytest

from test_artifacts import _approve, _client, _factory, _page


def test_file_sourced_work_recovers_same_origin_and_delivers_to_home(tmp_path, monkeypatch):
    source_text = "An approved report with original provenance.\n"
    model_requests = []
    factory = _factory(monkeypatch, {
        "WRITE_REPORT": {
            "path": "source-report.txt", "expected_digest": None,
            "content": source_text,
        },
    }, model_requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        pages = client.app.state.pages
        producer = _page(client)
        action_id = _approve(pages.execution, producer)
        artifact, = pages.artifacts.snapshot(producer)["artifacts"]
        assert action_id == artifact["id"]
        selected = {
            "id": artifact["id"], "pageId": producer,
            "path": artifact["path"], "sha256": artifact["sha256"],
        }
        request = {
            "title": "Continue from approved file",
            "instruction": "Use the exact original file as source for a finished handoff.",
            "sourcePageId": producer,
            "sourceArtifact": selected,
            "requestId": "file-create-receipt-20261121",
        }

        def lose_receipt_after_native_admission():
            raise OSError("lost HTTP create receipt after persistent native admission")

        monkeypatch.setattr(pages, "_save", lose_receipt_after_native_admission)
        with pytest.raises(OSError, match="lost HTTP create receipt"):
            pages.create_page(
                request["title"], request["instruction"], request["sourcePageId"],
                source_artifact=selected, request_id=request["requestId"],
            )
        fronts = pages.runtime.world.list_task_fronts()
        receiver, = [front for front in fronts if front.id != producer]
        first_events = pages.runtime.causal_ledger.list_events(
            center_id=receiver.center_id, limit=100,
        )
        first_inputs = [event for event in first_events if event.source.value == "user"]
        assert len(first_inputs) == 1
        first_id = first_inputs[0].id
        native_materials = pages.runtime.storage.list_page_materials(
            pages.runtime.world_id, receiver.id, 100,
        )
        assert len(native_materials) == 1
        material_id = native_materials[0]["id"]
        assert native_materials[0]["originArtifactId"] == artifact["id"]

    # The original producer and receiver are re-opened from the same SQLite.
    # The artifact is re-bound from its own native receipt, not a new file read.
    with _client(tmp_path, model_factory=None, allow_workspace_actions=True) as client:
        pages = client.app.state.pages
        identity = {
            "X-Per-Pulse-World": pages.runtime.world_id,
            "X-Per-Pulse-Service-Instance": pages.service_instance_id,
        }
        recovered = client.post("/api/pages", json=request)
        assert recovered.status_code == 201, recovered.text
        result = recovered.json()
        assert result["page"]["id"] == receiver.id
        assert result["initialMessageId"] == first_id
        assert result["reference"]["sourcePageId"] == producer
        assert result["reference"]["targetPageId"] == receiver.id
        initial, = [message for message in result["view"]["messages"]
                    if message["id"] == first_id]
        assert len(initial["materials"]) == 1
        assert initial["materials"][0]["id"] == material_id
        assert initial["materials"][0]["originArtifactId"] == action_id
        download = client.get(
            f"/api/pages/{receiver.id}/materials/{material_id}/download",
            headers=identity,
        )
        assert download.status_code == 200, download.text
        assert download.content == source_text.encode("utf-8")
        repeated = client.post("/api/pages", json=request)
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["reference"]["id"] == result["reference"]["id"]
        assert repeated.json()["initialMessageId"] == first_id
        assert len([front for front in client.app.state.pages.runtime.world.list_task_fronts()
                    if front.id == receiver.id]) == 1

        delivered = client.post(f"/api/pages/{receiver.id}/messages", json={
            "content": "The sourced handoff is complete.",
            "materials": [material_id],
        })
        assert delivered.status_code == 202, delivered.text
        returned = client.post(f"/api/pages/{receiver.id}/return", json={
            "content": "Read the attributed Work and original file.",
            "messageId": delivered.json()["messageId"],
        })
        assert returned.status_code == 201, returned.text
        home_id = returned.json()["messageId"]
        assert returned.json()["view"]["messages"][-1]["sourcePageId"] == receiver.id

        undo = client.post(f"/api/pages/{producer}/artifacts/{action_id}/undo", json={})
        assert undo.status_code == 200, undo.text
        material = client.get(f"/api/pages/{receiver.id}/materials/{material_id}", headers=identity)
        assert material.status_code == 409, material.text
        assert material.json()["error"]["code"] == "material_unavailable"

    with _client(tmp_path, model_factory=None, allow_workspace_actions=True) as client:
        pages = client.app.state.pages
        identity = {
            "X-Per-Pulse-World": pages.runtime.world_id,
            "X-Per-Pulse-Service-Instance": pages.service_instance_id,
        }
        assert len([front for front in client.app.state.pages.runtime.world.list_task_fronts()
                    if front.id == receiver.id]) == 1
        home = client.get("/api/pages/home").json()
        returned_message, = [message for message in home["messages"]
                             if message["id"] == home_id]
        assert returned_message["sourcePageId"] == receiver.id
        assert returned_message["sourceMessageId"] == delivered.json()["messageId"]
        assert client.get(f"/api/pages/{receiver.id}/materials/{material_id}").status_code == 409
