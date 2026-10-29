"""Older conversation is a bounded read of the existing native causal ledger.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All inputs are explicit local fixtures; no provider is configured or called.
"""
from __future__ import annotations

from dataclasses import asdict

from test_pages import client_for, new_page, send


def history(client, page_id, before):
    response = client.get(f"/api/pages/{page_id}/messages", params={"beforeMessageId": before})
    assert response.status_code == 200, response.text
    return response.json()


def test_all_older_messages_remain_reachable_across_new_input_and_restart(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client)
        work_id = work["page"]["id"]
        returned = client.post(f"/api/pages/{work_id}/return", json={
            "messageId": work["initialMessageId"], "content": "An original sourced finding.",
        }).json()
        original = returned["view"]["messages"][0]
        ids = [original["id"]]
        runtime = client.app.state.pages.runtime
        front = runtime.world.get_task_front(work_id)
        for index in range(1106):
            ids.append(runtime.inject(runtime.continuity_engram_id, f"Home history fixture {index}", source="user"))
            if index % 30 == 0:
                runtime.inject(front.focal_engram_id, f"Private Work fixture {index}", source="user", center_id=front.center_id)
        recent = client.get("/api/pages/home").json()
        assert len(recent["messages"]) == 500
        assert recent["historyBeforeMessageId"] == ids[-500]
        first = history(client, "home", recent["historyBeforeMessageId"])
        assert len(first["messages"]) == 100 and first["hasMore"]
        assert [message["id"] for message in first["messages"]] == ids[-600:-500]
        assert first["beforeMessageId"] == ids[-600]
        # New messages cannot move a previously read keyset boundary. Native
        # sequence, rather than timestamps or offset, defines the older range.
        send(client, "home", "Arrived while the older conversation was open.")
        assert history(client, "home", recent["historyBeforeMessageId"]) == first
        anchored = client.get("/api/pages/home", params={"sourceMessageId": ids[0]}).json()
        assert anchored["earlierSourceMessageId"] == ids[0]
        assert anchored["historyBeforeMessageId"] == ids[-499]
        assert anchored["historyBeforeMessageId"] != ids[0]
        workspace_id = client.get("/api/bootstrap").json()["workspaceId"]

    with client_for(tmp_path, model_factory=None) as client:
        assert client.get("/api/bootstrap").json()["workspaceId"] == workspace_id
        assert history(client, "home", recent["historyBeforeMessageId"]) == first
        collected = recent["messages"]
        cursor = recent["historyBeforeMessageId"]
        while cursor:
            result = history(client, "home", cursor)
            assert result["pageId"] == "home"
            assert 1 <= len(result["messages"]) <= 100
            assert result["hasMore"] == (result["beforeMessageId"] is not None)
            collected = result["messages"] + collected
            cursor = result["beforeMessageId"]
        assert [message["id"] for message in collected] == ids
        assert collected[0] == original  # Source identity and references survive.
        assert len({message["id"] for message in collected}) == len(ids)
        assert all("Private Work" not in message["content"] for message in collected)
        assert history(client, "home", original["id"]) == {
            "pageId": "home", "messages": [], "hasMore": False, "beforeMessageId": None,
        }
        assert client.app.state.pages.runtime.tick_count == 0


def test_history_cursor_must_be_an_actual_message_in_the_same_page(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        home_id = send(client, "home", "Home source")["messageId"]
        first = new_page(client)
        second = new_page(client, title="A different Work")
        for page_id, cursor in [
            ("home", first["initialMessageId"]),
            (first["page"]["id"], home_id),
            (first["page"]["id"], second["initialMessageId"]),
            ("home", "missing-native-event"),
            ("missing-work", home_id),
        ]:
            response = client.get(f"/api/pages/{page_id}/messages", params={"beforeMessageId": cursor})
            assert response.status_code == 404, response.text
            assert "messages" not in response.json()
        assert client.get("/api/pages/home/messages").status_code == 422
        assert client.get("/api/pages/home/messages", params={"beforeMessageId": ""}).status_code == 422
        assert client.get("/api/pages/home").json()["historyBeforeMessageId"] is None
        assert not client.app.state.pages.runtime.running


def test_history_reads_do_not_change_native_events_or_drive_queued_turns(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        runtime = client.app.state.pages.runtime
        ids = [runtime.inject(runtime.continuity_engram_id, f"Read-only fixture {index}", source="user")
               for index in range(120)]
        before = [asdict(event) for event in runtime.causal_ledger.list_events(limit=200)]
        status = client.get("/api/pages/home").json()["page"]
        for _ in range(4):
            result = history(client, "home", ids[-1])
            assert [message["id"] for message in result["messages"]] == ids[-101:-1]
        after = [asdict(event) for event in runtime.causal_ledger.list_events(limit=200)]
        assert after == before
        assert client.get("/api/pages/home").json()["page"] == status
        assert runtime.tick_count == 0
