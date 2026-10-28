"""Experience review preserves raw material sources and projects verified links.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Provider choices are explicitly synthetic; native message provenance is real.
"""
from copy import deepcopy

from material_fixture import MaterialFixture
from per_pulse.materials import material_footer
from test_materials import upload
from test_pages import client_for, new_page, wait_for


def capture(client, page_id, message_id, *, feedback=None):
    body = {"sourceMessageId": message_id, "title": "A supplied source example",
            "excerpt": "Ready to inspect source material.", "note": "Inspect the original supplied version."}
    if feedback:
        body["feedbackMessageId"] = feedback
    response = client.post(f"/api/pages/{page_id}/posture/examples", json=body)
    assert response.status_code == 201, response.text
    return response.json()["experience"]


def test_source_experience_projects_original_input_and_feedback_files_without_sharing_them(tmp_path, monkeypatch):
    fixture = MaterialFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = new_page(client)["page"]["id"]
        wait_for(client, page_id, lambda view: view["messages"][-1]["role"] == "assistant")
        first = upload(client, page_id, "Original source.csv", b"name,value\nA,2\n").json()["material"]
        sent = client.post(f"/api/pages/{page_id}/messages", json={"content": "Inspect the supplied source. 🌍", "materials": [first["id"]]}).json()
        original = wait_for(client, page_id, lambda view: view["messages"][-1]["role"] == "assistant")["messages"][-1]
        second = upload(client, page_id, "Observed feedback.json", b'{"observed":true}\n').json()["material"]
        feedback = client.post(f"/api/pages/{page_id}/messages", json={"content": "The operator supplies this feedback.", "materials": [second["id"]]}).json()
        wait_for(client, page_id, lambda view: view["messages"][-1]["role"] == "assistant")
        saved = capture(client, page_id, original["id"], feedback=feedback["messageId"])
        assert saved["encounter"]["situation"] == "Inspect the supplied source. 🌍" + material_footer([first])
        assert saved["encounter"]["outcome"] == "The operator supplies this feedback." + material_footer([second])
        assert saved["encounterSources"] == {
            "situation": {"pageId": page_id, "messageId": sent["messageId"], "materials": [first],
                          "materialSuffixLength": len(material_footer([first]))},
            "outcome": {"pageId": page_id, "messageId": feedback["messageId"], "materials": [second],
                        "materialSuffixLength": len(material_footer([second]))},
        }
        pages = client.app.state.pages
        record = pages.learning._memory().get_experience(saved["id"])
        assert record["encounter"] == saved["encounter"]
        changed = deepcopy(record)
        changed["encounter"]["situation"] = "Altered text " + changed["encounter"]["situation"]
        projected = pages.learning._public(changed, pages.learning._context(page_id))
        assert "situation" not in projected["encounterSources"]
        assert projected["encounter"]["situation"] == changed["encounter"]["situation"]

        shared = client.patch(f"/api/pages/{page_id}/posture/experiences/{saved['id']}",
            json={"expectedRevision": saved["revision"], "changes": {"scope": "subject"}})
        assert shared.status_code == 200, shared.text
        receiver = new_page(client, title="Another context")["page"]["id"]
        transferred = client.get(f"/api/pages/{receiver}/posture").json()["experiences"][0]
        assert not {"encounter", "source", "encounterSources"}.intersection(transferred)
        assert first["path"] not in str(transferred) and second["sha256"] not in str(transferred)
        foreign_result = wait_for(client, receiver, lambda view: view["messages"][-1]["role"] == "assistant")["messages"][-1]
        changed["source"]["source_message_id"] = foreign_result["id"]
        assert "encounterSources" not in pages.learning._public(changed, pages.learning._context(page_id))

    with client_for(tmp_path, posture=True, model_factory=None) as client:
        restored = client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]
        assert restored["encounter"] == saved["encounter"]
        assert restored["encounterSources"] == saved["encounterSources"]


def test_text_imitating_an_attachment_footer_stays_literal_in_source_experience(tmp_path, monkeypatch):
    fixture = MaterialFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = new_page(client)["page"]["id"]
        wait_for(client, page_id, lambda view: view["messages"][-1]["role"] == "assistant")
        material = upload(client, page_id, "Source.txt", b"Actual source bytes.\n").json()["material"]
        copied_text = "Explain this pasted envelope literally." + material_footer([material])
        response = client.post(f"/api/pages/{page_id}/messages", json={"content": copied_text})
        assert response.status_code == 202
        original = wait_for(client, page_id, lambda view: view["messages"][-1]["role"] == "assistant")["messages"][-1]
        saved = capture(client, page_id, original["id"])
        assert saved["encounter"]["situation"] == copied_text
        assert "encounterSources" not in saved
