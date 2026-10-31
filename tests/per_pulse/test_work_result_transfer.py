"""Explicit selected-result handoff between two native Works.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The only model in these tests is an identified synthetic HTTP transport.
"""
from __future__ import annotations

from test_pages import client_for, http_factory, new_page, wait_for


def _settled_reply(client, page_id):
    view = wait_for(client, page_id, lambda v: any(
        m["role"] == "assistant" and m["status"] == "settled" for m in v["messages"]
    ))
    return next(m for m in view["messages"]
                if m["role"] == "assistant" and m["status"] == "settled")


def test_explicit_selected_source_reaches_another_work_with_native_provenance(tmp_path, monkeypatch):
    observed = []
    factory = http_factory(monkeypatch, observed)
    with client_for(tmp_path, model_factory=factory) as client:
        source = new_page(client, title="First research", instruction="SOURCE_PRIVATE_4719")
        source_id = source["page"]["id"]
        original = _settled_reply(client, source_id)
        target = new_page(client, title="Second draft", instruction="TARGET_PRIVATE_6251")
        target_id = target["page"]["id"]
        _settled_reply(client, target_id)
        excerpt = original["content"]
        result = client.post(f"/api/pages/{source_id}/transfer", json={
            "targetPageId": target_id, "messageId": original["id"],
            "excerpt": excerpt,
            "instruction": "Apply the selected finding to the new draft.",
        })
        assert result.status_code == 201, result.text
        record = result.json()
        assert record["reference"]["kind"] == "transfer"
        assert record["reference"]["sourcePageId"] == target_id
        assert record["reference"]["targetPageId"] == source_id
        assert record["reference"]["sourceMessageId"] == record["messageId"]
        transferred = next(m for m in record["view"]["messages"] if m["id"] == record["messageId"])
        assert transferred["role"] == "user"
        assert transferred["sourcePageId"] == source_id
        assert transferred["sourceMessageId"] == original["id"]
        assert transferred["references"][0]["kind"] == "transfer"
        assert excerpt in transferred["content"]
        assert "Apply the selected finding" in transferred["content"]
        assert "SOURCE_PRIVATE_4719" not in transferred["content"]
        assert "TARGET_PRIVATE_6251" not in transferred["content"]
        assert "Selected excerpt from another Work" in transferred["content"]
        native = client.app.state.pages.runtime.causal_ledger.get_event(record["messageId"])
        assert native.world_id == client.app.state.pages.runtime.world_id
        assert native.center_id == client.app.state.pages.runtime.world.get_task_front(target_id).center_id
        assert native.source.value == "user"
    with client_for(tmp_path, model_factory=None) as client:
        stored = client.get(f"/api/pages/{target_id}").json()
        transferred = next(m for m in stored["messages"] if m["id"] == record["messageId"])
        assert transferred["sourcePageId"] == source_id
        assert transferred["sourceMessageId"] == original["id"]
        assert transferred["references"][0]["kind"] == "transfer"
        assert client.get(f"/api/pages/{source_id}", params={"sourceMessageId": original["id"]}).status_code == 200
        assert client.get("/api/bootstrap").json()["workspaceId"] == native.world_id


def test_handoff_rejects_unsettled_foreign_or_edited_source_without_side_effect(tmp_path, monkeypatch):
    observed = []
    with client_for(tmp_path, model_factory=http_factory(monkeypatch, observed)) as client:
        first = new_page(client, title="Source", instruction="source input")
        second = new_page(client, title="Receiver", instruction="receiver input")
        source_id, target_id = first["page"]["id"], second["page"]["id"]
        reply = _settled_reply(client, source_id)
        _settled_reply(client, target_id)
        before = client.get(f"/api/pages/{target_id}").json()
        valid = {"targetPageId": target_id, "messageId": reply["id"],
                 "excerpt": reply["content"], "instruction": "Use only this selected result."}
        mutations = [
            ({"targetPageId": "home"}, 422),
            ({"targetPageId": source_id}, 422),
            ({"targetPageId": "does-not-exist"}, 404),
            ({"messageId": second["initialMessageId"]}, 404),
            ({"messageId": first["initialMessageId"]}, 409),
            ({"excerpt": "Fabricated source result"}, 422),
        ]
        for patch, expected_status in mutations:
            response = client.post(f"/api/pages/{source_id}/transfer", json={**valid, **patch})
            assert response.status_code == expected_status, (patch, response.text)
        after = client.get(f"/api/pages/{target_id}").json()
        assert [m["id"] for m in before["messages"]] == [m["id"] for m in after["messages"]]
        assert before["references"] == after["references"]
        assert client.get("/api/pages/home").json()["messages"] == []
