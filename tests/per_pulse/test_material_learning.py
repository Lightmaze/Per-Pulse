"""Experience review preserves raw material sources and projects verified links.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Provider choices are explicitly synthetic; native message provenance is real.
"""
from copy import deepcopy
import json

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
        projected = pages.learning._public(changed, pages.learning._context(page_id), page_id)
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
        assert "encounterSources" not in pages.learning._public(changed, pages.learning._context(page_id), page_id)

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


class RevisionFixture(MaterialFixture):
    """Explicit choices; the existing Kernel admits the actual later message."""
    def handler(self, request):
        body = json.loads(request.content)
        messages = body["messages"]
        if "WORK_MATERIAL_JSON" in json.dumps(messages):
            return super().handler(request)
        current = max(i for i, message in enumerate(messages) if message["role"] == "user")
        if not str(messages[current]["content"]).startswith("REVISE_SOURCE"):
            return super().handler(request)
        self.requests.append(body)
        results = [json.loads(m["content"]) for m in messages[current + 1:] if m["role"] == "tool"]
        if not results:
            return self.response(tool="posture", arguments={"op": "list"})
        assert results[-1]["status"] == "succeeded"
        if len(results) == 1:
            listed = results[0]["output"]
            saved = next(item for item in listed["experiences"] if item["representation"] == "example")
            followup = next(item for item in reversed(listed["recent_sources"]) if item["source"] == "user")
            return self.response(tool="posture", sequence=1, arguments={
                "op": "revise", "experience_id": saved["id"], "expected_revision": saved["revision"],
                "feedback_message_id": followup["message_id"],
                "changes": {"note": "Check the supplied later source before reusing this example."},
            })
        return self.response("The example refinement is prepared. [Explicit synthetic fixture]")


def revise_with_source(client, page_id, text, material_ids=()):
    response = client.post(f"/api/pages/{page_id}/messages", json={"content": text, "materials": list(material_ids)})
    assert response.status_code == 202, response.text
    wait_for(client, page_id, lambda view: view["messages"][-1]["role"] == "assistant"
        and client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]["revision"] == 2)
    return response.json()["messageId"], client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]


def test_native_revision_feedback_has_original_message_and_file_links_after_restart(tmp_path, monkeypatch):
    fixture = RevisionFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = new_page(client)["page"]["id"]
        result = wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")["messages"][-1]
        capture(client, page_id, result["id"])
        material = upload(client, page_id, "Later evidence.md", b"# Actual later source\n").json()["material"]
        text = "REVISE_SOURCE: this observed outcome changes the interpretation. 🌍"
        message_id, saved = revise_with_source(client, page_id, text, [material["id"]])
        entry = saved["feedback"][0]
        assert entry["source"]["kind"] == "model_interpreted_user_revision"
        assert entry["evidence"] == text + material_footer([material])
        expected = {"pageId": page_id, "messageId": message_id, "materials": [material],
                    "materialSuffixLength": len(material_footer([material]))}
        assert entry["evidenceSource"] == expected
        pages = client.app.state.pages
        record = pages.learning._memory().get_experience(saved["id"])
        assert "evidenceSource" not in record["feedback"][0]
        assert record["feedback"][0]["evidence"] == entry["evidence"]

        # A known native message is necessary; altered, bounded or foreign
        # attribution must never turn arbitrary report text into hidden markup.
        for changes in [
            {"world_id": "another-world"}, {"context_id": ""},
            {"feedback_subject_id": "another-subject"}, {"feedback_message_id": result["id"]},
            {"bounded_evidence": True}, {"kind": "operator_described_refinement"},
        ]:
            changed = deepcopy(record)
            changed["feedback"][0]["source"].update(changes)
            projected = pages.learning._public(changed, pages.learning._context(page_id), page_id)
            assert "evidenceSource" not in projected["feedback"][0]
            assert projected["feedback"][0]["evidence"] == entry["evidence"]
        changed = deepcopy(record)
        changed["feedback"][0]["evidence"] += " Not the original message."
        assert "evidenceSource" not in pages.learning._public(changed, pages.learning._context(page_id), page_id)["feedback"][0]

        assert client.patch(f"/api/pages/{page_id}/posture/experiences/{saved['id']}",
            json={"expectedRevision": 2, "changes": {"scope": "subject"}}).status_code == 200
        other = new_page(client, title="Receiving context")["page"]["id"]
        shared = client.get(f"/api/pages/{other}/posture").json()["experiences"][0]
        assert "feedback" not in shared and material["sha256"] not in json.dumps(shared)

    with client_for(tmp_path, posture=True, model_factory=None) as client:
        restored = client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]
        assert restored["feedback"][0]["evidenceSource"] == expected
        assert restored["feedback"][0]["evidence"] == entry["evidence"]


def test_pasted_attachment_envelope_and_operator_report_remain_literal_feedback(tmp_path, monkeypatch):
    fixture = RevisionFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = new_page(client)["page"]["id"]
        result = wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")["messages"][-1]
        capture(client, page_id, result["id"])
        material = upload(client, page_id, "Quoted source.txt", b"A real file, not attached to the feedback.\n").json()["material"]
        text = "REVISE_SOURCE: explain this quoted envelope literally." + material_footer([material])
        message_id, saved = revise_with_source(client, page_id, text)
        assert saved["feedback"][0]["evidence"] == text
        assert saved["feedback"][0]["evidenceSource"] == {
            "pageId": page_id, "messageId": message_id, "materials": [], "materialSuffixLength": 0}
        revised = client.patch(f"/api/pages/{page_id}/posture/experiences/{saved['id']}", json={
            "expectedRevision": 2, "changes": {"note": "Operator-authored refinement."}, "evidence": text})
        assert revised.status_code == 200, revised.text
        entry = revised.json()["experience"]["feedback"][-1]
        assert entry["evidence"] == text and "evidenceSource" not in entry
