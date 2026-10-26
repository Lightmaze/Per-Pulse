"""Page API checks over the real World/Kernel and explicit transport fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
These checks establish plumbing and provenance, not learned model capability.
"""
from __future__ import annotations

import json

from test_pages import client_for, http_factory, new_page, send, wait_for


def observed_pair(client, page_id="home"):
    initial_view = client.get(f"/api/pages/{page_id}").json()
    baseline = sum(m["role"] == "assistant" for m in initial_view["messages"])
    request = send(client, page_id, "Create a first-time listening experience.")
    view = wait_for(client, page_id, lambda v: sum(m["role"] == "assistant" for m in v["messages"]) > baseline)
    source = [m for m in view["messages"] if m["role"] == "assistant"][-1]
    correction = send(client, page_id, "Begin with a playable moment. A preference form does not deliver the listening experience.")
    wait_for(client, page_id, lambda v: sum(m["role"] == "assistant" for m in v["messages"]) >= baseline + 2)
    return request["messageId"], source["id"], correction["messageId"]


def lesson(source, feedback, **extra):
    return {
        "sourceMessageId": source, "feedbackMessageId": feedback,
        "title": "Deliver the first useful moment",
        "lesson": "Let a new visitor experience the central value before asking for setup.",
        "conditions": {"all": ["The task is an open-ended consumer experience", "The core value can be tried immediately"]},
        "counterexamples": ["A standard registration form with fixed fields and validation is the requested product."],
        "shift": {"understanding": "Find what the visitor came to experience.",
                  "candidates": "Consider a concrete sample alongside setup flows.",
                  "delivery": "Check that the visitor can actually try the central value."},
        **extra,
    }


def test_posture_disabled_and_example_remain_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PER_PULSE_POSTURE", "1")
    with client_for(tmp_path / "disabled", posture=False, model_factory=None) as client:
        assert client.get("/api/bootstrap").json()["postureEnabled"] is False
        assert client.get("/api/pages/home/posture").json() == {"enabled": False, "experiences": []}
        response = client.post("/api/pages/home/posture/experiences", json=lesson("missing", "missing"))
        assert response.status_code == 409
    with client_for(tmp_path / "example", example=True, posture=True) as client:
        assert client.get("/api/bootstrap").json()["postureEnabled"] is False
        assert client.app.state.pages.runtime.tick_count == 0


def test_real_source_required_and_revisions_survive_restart(tmp_path, monkeypatch):
    requests = []
    factory = http_factory(monkeypatch, requests)
    with client_for(tmp_path, posture=True, model_factory=factory) as client:
        initial, source, feedback = observed_pair(client)
        endpoint = "/api/pages/home/posture/experiences"
        bad_role = client.post(endpoint, json=lesson(initial, feedback))
        assert bad_role.status_code == 422
        bad_order = client.post(endpoint, json=lesson(source, initial))
        assert bad_order.status_code == 422
        assert client.get("/api/pages/home/posture").json()["experiences"] == []
        created = client.post(endpoint, json=lesson(source, feedback))
        assert created.status_code == 201, created.text
        saved = created.json()["experience"]
        identity = saved["id"]
        assert saved["revision"] == 1 and saved["active"]
        assert "Transport fixture response." in saved["encounter"]["action"]
        assert "preference form" in saved["encounter"]["outcome"]
        assert saved["source"]["capture_kind"] == "operator_selected_correction"
        assert saved["record_type"] == "constructed_fixture"
        assert saved["source"]["provider_evidence_class"] == "TEST_DOUBLE"
        revision_url = endpoint + "/" + identity
        pause = client.patch(revision_url, json={"expectedRevision": 1, "changes": {"active": False}})
        assert pause.status_code == 200
        conflict = client.patch(revision_url, json={"expectedRevision": 1, "changes": {"scope": "subject"}})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "experience_revision_conflict"
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        result = client.get("/api/pages/home/posture").json()["experiences"][0]
        assert result["id"] == identity and result["revision"] == 2 and not result["active"]
        assert result["source"]["capture_kind"] == "operator_selected_correction"


def test_cross_page_transfer_is_explicit_and_raw_encounter_stays_at_source(tmp_path, monkeypatch):
    factory = http_factory(monkeypatch, [])
    with client_for(tmp_path, posture=True, model_factory=factory) as client:
        _, source, feedback = observed_pair(client)
        saved = client.post("/api/pages/home/posture/experiences", json=lesson(source, feedback)).json()["experience"]
        page = new_page(client, title="New task", instruction="Maintain a registration tool.")["page"]["id"]
        assert client.get(f"/api/pages/{page}/posture").json()["experiences"] == []
        endpoint = "/api/pages/home/posture/experiences/" + saved["id"]
        promoted = client.patch(endpoint, json={"expectedRevision": 1, "changes": {"scope": "subject"}})
        assert promoted.status_code == 200
        shared = client.get(f"/api/pages/{page}/posture").json()["experiences"][0]
        assert not shared["canEdit"] and shared["sourcePageId"] == "home"
        assert "encounter" not in shared and "feedback" not in shared and "source" not in shared
        assert shared["sourceAnchor"] == {"resultMessageId": source, "feedbackMessageId": feedback}
        assert "Transport fixture response." not in json.dumps(shared)
        denied = client.patch(f"/api/pages/{page}/posture/experiences/{saved['id']}",
                              json={"expectedRevision": 2, "changes": {"active": False}})
        assert denied.status_code == 404
        negative = client.post(endpoint + "/feedback", json={
            "expectedRevision": 2, "verdict": "harmful",
            "evidence": "It added an unnecessary preview to a fixed registration workflow.",
        })
        assert negative.status_code == 200
        assert not negative.json()["experience"]["active"]
        assert negative.json()["experience"]["revision"] == 3
        assert client.app.state.pages.runtime.harness.posture_memory.materials(
            subject_id=client.app.state.pages.runtime.continuity_engram_id,
            context_id="",
        )["episodes"] == []


def test_cross_page_source_cannot_be_imported_as_local_experience(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        _, source, feedback = observed_pair(client)
        page = new_page(client)["page"]["id"]
        response = client.post(f"/api/pages/{page}/posture/experiences", json=lesson(source, feedback))
        assert response.status_code == 404
        assert client.get(f"/api/pages/{page}/posture").json()["experiences"] == []


def test_work_origin_remains_editable_at_its_source_and_locatable_from_home(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        page = new_page(client, title="Listening prototype")["page"]["id"]
        wait_for(client, page, lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        _, source, feedback = observed_pair(client, page)
        endpoint = f"/api/pages/{page}/posture/experiences"
        response = client.post(endpoint, json=lesson(source, feedback))
        assert response.status_code == 201, response.text
        saved = response.json()["experience"]
        assert saved["sourcePageId"] == page and saved["canEdit"]
        assert client.get("/api/pages/home/posture").json()["experiences"] == []
        shared = client.patch(endpoint + "/" + saved["id"], json={
            "expectedRevision": 1, "changes": {"scope": "subject"},
        })
        assert shared.status_code == 200
        at_home = client.get("/api/pages/home/posture").json()["experiences"][0]
        assert at_home["sourcePageId"] == page and not at_home["canEdit"]
        assert at_home["sourceAnchor"] == {"resultMessageId": source, "feedbackMessageId": feedback}
        assert "encounter" not in at_home and "source" not in at_home
        retract = client.patch(endpoint + "/" + saved["id"], json={
            "expectedRevision": 2, "changes": {"scope": "context"},
        })
        assert retract.status_code == 200
        assert client.get("/api/pages/home/posture").json()["experiences"] == []


def test_invalid_mutations_preserve_revision_and_never_echo_credentials(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        _, source, feedback = observed_pair(client)
        endpoint = "/api/pages/home/posture/experiences"
        saved = client.post(endpoint, json=lesson(source, feedback)).json()["experience"]
        identity = saved["id"]
        target = endpoint + "/" + identity
        invalid_changes = [
            {"subject_id": "another-subject"}, {"context_id": "another-page"},
            {"record_type": "observed_episode"}, {"active": "false"},
            {"conditions": {"any": []}}, {"lesson": "sk-invalidcredentialexample"},
        ]
        for changes in invalid_changes:
            response = client.patch(target, json={"expectedRevision": 1, "changes": changes})
            assert response.status_code == 422, response.text
            assert "sk-invalidcredentialexample" not in response.text
        wrong_origin = client.patch(target, json={"expectedRevision": 1, "changes": {"active": False}},
                                    headers={"Origin": "https://external.invalid"})
        assert wrong_origin.status_code == 403
        wrong_type = client.patch(target, content='{"expectedRevision":1,"changes":{"active":false}}',
                                  headers={"Content-Type": "text/plain"})
        assert wrong_type.status_code == 415
        bad_feedback = client.post(target + "/feedback", json={
            "expectedRevision": 1, "verdict": "useful", "evidence": "sk-invalidcredentialexample",
        })
        assert bad_feedback.status_code == 422 and "sk-invalidcredentialexample" not in bad_feedback.text
        current = client.get("/api/pages/home/posture").json()["experiences"][0]
        assert current["revision"] == 1 and current["active"]
        assert current["lesson"] == saved["lesson"] and current["feedback"] == []


def test_lesson_source_remains_reachable_after_recent_window_without_exposing_other_pages(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        _, source, feedback = observed_pair(client)
        result = client.post("/api/pages/home/posture/experiences", json=lesson(source, feedback))
        assert result.status_code == 201
        original = next(message for message in client.get("/api/pages/home").json()["messages"] if message["id"] == source)
        other_page = new_page(client)["page"]["id"]
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        runtime = client.app.state.pages.runtime
        for index in range(505):
            runtime.inject(runtime.continuity_engram_id, f"Later queued fixture message {index}.", source="user")
        recent = client.get("/api/pages/home").json()
        assert len(recent["messages"]) == 500 and recent["hasMore"]
        assert source not in {message["id"] for message in recent["messages"]}
        anchored = client.get("/api/pages/home", params={"sourceMessageId": source})
        assert anchored.status_code == 200
        view = anchored.json()
        assert view["earlierSourceMessageId"] == source
        assert view["messages"][0] == original
        assert view["messages"][1:] == recent["messages"]
        assert view["page"] == recent["page"]
        current = client.get("/api/pages/home", params={"sourceMessageId": recent["messages"][-1]["id"]}).json()
        assert current["earlierSourceMessageId"] is None and len(current["messages"]) == 500
        assert client.get(f"/api/pages/{other_page}", params={"sourceMessageId": source}).status_code == 404
        assert client.get("/api/pages/home", params={"sourceMessageId": "missing-event"}).status_code == 404
