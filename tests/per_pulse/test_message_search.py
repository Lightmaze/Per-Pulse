"""Find original conversation evidence without invoking or copying a model.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All messages are explicit authored native inputs in temporary workspaces.
"""
from dataclasses import asdict

from test_pages import client_for, new_page, send


def search(client, page_id, query, before=None):
    params = {"q": query}
    if before is not None:
        params["beforeMessageId"] = before
    response = client.get(f"/api/pages/{page_id}/messages/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_search_covers_full_conversation_with_stable_ranges_and_source_links(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client)
        work_id = work["page"]["id"]
        sibling = new_page(client, title="A private sibling")
        runtime = client.app.state.pages.runtime
        ids = []
        for index in range(650):
            content = f"Authored result {index}: telescope finding" if index % 2 else f"Unrelated observation {index}"
            event = runtime.send_task_front_message(work_id, content)
            if index % 2:
                ids.append(event)
        client.post(f"/api/pages/{work_id}/references", json={
            "targetPageId": sibling["page"]["id"], "sourceMessageId": ids[0], "label": "Related source",
        }).raise_for_status()
        source = client.get(f"/api/pages/{work_id}", params={"sourceMessageId": ids[0]}).json()["messages"][0]
        assert source["id"] == ids[0] and source["references"]
        initial = search(client, work_id, "  TELESCOPE  ")
        assert initial["query"] == "TELESCOPE" and initial["hasMore"]
        assert [message["id"] for message in initial["messages"]] == list(reversed(ids[-100:]))
        cursor = initial["beforeMessageId"]
        second = search(client, work_id, "telescope", cursor)
        send(client, work_id, "A newly arrived telescope result")
        assert search(client, work_id, "telescope", cursor) == second
        gathered = initial["messages"]
        while cursor:
            result = search(client, work_id, "telescope", cursor)
            assert 0 < len(result["messages"]) <= 100
            gathered += result["messages"]
            cursor = result["beforeMessageId"]
        assert [message["id"] for message in gathered] == list(reversed(ids))
        assert gathered[-1] == source
        assert len({message["id"] for message in gathered}) == len(ids)

    with client_for(tmp_path, model_factory=None) as client:
        assert search(client, work_id, "telescope", initial["beforeMessageId"]) == second
        assert client.app.state.pages.runtime.tick_count == 0


def test_search_is_literal_unicode_casefolded_and_read_only(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        texts = ["A 100%_literal\\path with 'quotes'.", "A 1000Xliteral decoy.",
                 "A Straße and CAFÉ observation.", "A retained 观察记录 with café."]
        ids = [send(client, "home", text)["messageId"] for text in texts]
        runtime = client.app.state.pages.runtime
        before = [asdict(event) for event in runtime.causal_ledger.list_events(limit=100)]
        for query, expected in [("%_literal\\path", [ids[0]]), ("'quotes'", [ids[0]]),
                                ("STRASSE", [ids[2]]), ("café", [ids[3], ids[2]]),
                                ("观察", [ids[3]]), ("' OR 1=1 --", [])]:
            assert [message["id"] for message in search(client, "home", query)["messages"]] == expected
        assert [asdict(event) for event in runtime.causal_ledger.list_events(limit=100)] == before
        assert not runtime.kernel_service.store.sessions_for_world(runtime.world_id)
        assert not runtime.kernel_service._runtimes and runtime.tick_count == 0


def test_search_cannot_read_other_contexts_or_accept_foreign_cursors(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client, instruction="ONLY_WORK_SECRET search fixture")
        work_id = work["page"]["id"]
        home = send(client, "home", "ONLY_HOME_SECRET search fixture")["messageId"]
        assert search(client, "home", "ONLY_WORK_SECRET")["messages"] == []
        assert search(client, work_id, "ONLY_HOME_SECRET")["messages"] == []
        for page, cursor in [("home", work["initialMessageId"]), (work_id, home), (work_id, "missing")]:
            response = client.get(f"/api/pages/{page}/messages/search", params={"q": "fixture", "beforeMessageId": cursor})
            assert response.status_code == 404
        assert client.get("/api/pages/missing/messages/search", params={"q": "fixture"}).status_code == 404
        for query in ("", "  ", "x" * 201):
            assert client.get("/api/pages/home/messages/search", params={"q": query}).status_code == 422
        assert client.get("/api/pages/home/messages/search").status_code == 422
