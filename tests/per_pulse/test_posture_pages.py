"""Page API checks over the real World/Kernel and explicit transport fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
These checks establish plumbing and provenance, not learned model capability.
"""
from __future__ import annotations

import json
import hashlib

import pytest

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


def test_operator_refinement_keeps_described_evidence_and_source_private(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        _, source, feedback = observed_pair(client)
        endpoint = "/api/pages/home/posture/experiences"
        initial = client.post(endpoint, json=lesson(source, feedback)).json()["experience"]
        target = endpoint + "/" + initial["id"]
        shared = client.patch(target, json={"expectedRevision": 1, "changes": {"scope": "subject"}})
        assert shared.status_code == 200
        receiver = new_page(client, title="Different task", instruction="Build a registration form.")["page"]["id"]
        evidence = "A later registration job needed only fixed fields, not an interactive preview."
        rejected = client.patch(target, json={"expectedRevision": 2, "changes": {"scope": "context"},
                                               "evidence": evidence})
        assert rejected.status_code == 422
        assert client.get("/api/pages/home/posture").json()["experiences"][0]["revision"] == 2
        revised = client.patch(target, json={"expectedRevision": 2, "changes": {
            "lesson": "Give an immediate trial only when the user requests an experience.",
            "conditions": {"all": ["An interactive experience is requested", {
                "any": ["No booking is required", {"not": "A required booking is unconfirmed"}],
            }]},
            "counterexamples": ["A fixed registration form is requested."],
        }, "evidence": evidence})
        assert revised.status_code == 200, revised.text
        current = revised.json()["experience"]
        assert current["id"] == initial["id"] and current["revision"] == 3
        assert current["source"] == initial["source"] and current["encounter"] == initial["encounter"]
        assert len(current["feedback"]) == 1
        entry = current["feedback"][0]
        assert entry["verdict"] == "revised" and entry["evidence"] == evidence
        assert entry["source"]["kind"] == "operator_described_refinement"
        assert entry["source"]["page_id"] == "home"
        exposed = client.get(f"/api/pages/{receiver}/posture").json()["experiences"][0]
        assert exposed["lesson"] == current["lesson"] and exposed["revision"] == 3
        assert exposed["conditions"] == current["conditions"]
        assert not exposed["canEdit"]
        assert all(field not in exposed for field in ("source", "encounter", "feedback"))
        assert evidence not in json.dumps(exposed)
        stale = client.patch(target, json={"expectedRevision": 2, "changes": {
            "lesson": "Stale change",
        }, "evidence": "An obsolete observation."})
        assert stale.status_code == 409
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        persisted = client.get("/api/pages/home/posture").json()["experiences"][0]
        assert persisted["source"] == initial["source"] and persisted["encounter"] == initial["encounter"]
        assert persisted["feedback"] == current["feedback"]
        assert persisted["conditions"] == current["conditions"]
        assert persisted["revision"] == 3

def test_receiving_work_can_report_shared_lesson_outcome_without_importing_private_history(tmp_path, monkeypatch):
    """Real native World/Kernel and SQLite; model replies are explicit HTTP fixtures."""
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        source_work = new_page(client, title="Original encounter")["page"]["id"]
        wait_for(client, source_work, lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        _, original, correction = observed_pair(client, source_work)
        source_endpoint = f"/api/pages/{source_work}/posture/experiences"
        saved = client.post(source_endpoint, json=lesson(original, correction)).json()["experience"]
        receiving_work = new_page(client, title="Later task", instruction="Fix a registration page.")["page"]["id"]
        wait_for(client, receiving_work, lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        receiving_endpoint = f"/api/pages/{receiving_work}/posture/experiences/{saved['id']}"

        # Work-local lessons are not open for other Works to rate or edit.
        assert client.post(receiving_endpoint + "/feedback", json={
            "expectedRevision": 1, "verdict": "useful", "evidence": "No shared lesson yet.",
        }).status_code == 404
        shared = client.patch(source_endpoint + "/" + saved["id"], json={
            "expectedRevision": 1, "changes": {"scope": "subject"},
        }).json()["experience"]
        assert shared["revision"] == 2
        receiving = client.get(f"/api/pages/{receiving_work}/posture").json()["experiences"][0]
        assert receiving["canReport"] and not receiving["canEdit"]
        assert receiving["sourceAnchor"] == {
            "resultMessageId": original, "feedbackMessageId": correction,
        }
        assert all(field not in receiving for field in ("source", "encounter", "feedback"))
        assert client.patch(receiving_endpoint, json={
            "expectedRevision": 2, "changes": {"lesson": "Unauthorized edit"},
        }).status_code == 404

        helpful = client.post(receiving_endpoint + "/feedback", json={
            "expectedRevision": 2, "verdict": "useful",
            "evidence": "The shared guidance prompted a useful first interaction.",
        })
        assert helpful.status_code == 200, helpful.text
        projected = helpful.json()["experience"]
        assert projected["revision"] == 3 and projected["active"] and projected["canReport"]
        assert not projected["canEdit"]
        assert all(field not in projected for field in ("source", "encounter", "feedback"))
        assert client.post(receiving_endpoint + "/feedback", json={
            "expectedRevision": 2, "verdict": "harmful", "evidence": "Stale observation.",
        }).status_code == 409

        harmful = client.post(receiving_endpoint + "/feedback", json={
            "expectedRevision": 3, "verdict": "harmful",
            "evidence": "It led to an unnecessary onboarding preview in this Work.",
        })
        assert harmful.status_code == 200, harmful.text
        after = harmful.json()["experience"]
        assert after["revision"] == 4 and not after["active"] and not after["canReport"]
        assert all(field not in after for field in ("source", "encounter", "feedback"))
        assert client.post(receiving_endpoint + "/feedback", json={
            "expectedRevision": 4, "verdict": "useful", "evidence": "No longer shared.",
        }).status_code == 404

        # The original owner retains full private records and native feedback
        # attribution; no receiving Work gets the original correction content.
        owner = client.get(f"/api/pages/{source_work}/posture").json()["experiences"][0]
        assert owner["id"] == saved["id"] and owner["revision"] == 4
        assert owner["source"] == saved["source"] and owner["encounter"] == saved["encounter"]
        assert [item["verdict"] for item in owner["feedback"]] == ["useful", "harmful"]
        assert all(item["source"] == {
            "kind": "operator_report", "page_id": receiving_work,
            "relation": "shared_recipient",
        } for item in owner["feedback"])
        runtime = client.app.state.pages.runtime
        assert runtime.harness.posture_memory.materials(
            subject_id=runtime.continuity_engram_id, context_id="",
        )["episodes"] == []
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        owner = client.get(f"/api/pages/{source_work}/posture").json()["experiences"][0]
        assert owner["revision"] == 4 and not owner["active"]
        assert owner["feedback"][-1]["source"]["page_id"] == receiving_work


@pytest.mark.parametrize("from_source", [False, True])
def test_outcome_report_links_a_native_result_without_copying_it(tmp_path, monkeypatch, from_source):
    """An explicit result anchor survives restart and the recent-history window."""
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        _, original, correction = observed_pair(client)
        saved = client.post("/api/pages/home/posture/experiences", json=lesson(
            original, correction, scope="subject",
        )).json()["experience"]
        report_page = "home" if from_source else new_page(client, title="Later result")["page"]["id"]
        result_view = wait_for(client, report_page, lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        result = next(m for m in result_view["messages"] if m["role"] == "assistant")
        endpoint = f"/api/pages/{report_page}/posture/experiences/{saved['id']}/feedback"
        response = client.post(endpoint, json={
            "expectedRevision": 1, "verdict": "useful", "evidence": "The first interaction was useful.",
            "resultMessageId": result["id"],
        })
        assert response.status_code == 200, response.text
        report = client.get("/api/pages/home/posture").json()["experiences"][0]["feedback"][-1]
        assert report["source"]["result_message_id"] == result["id"]
        assert report["source"]["result_content_sha256"] == hashlib.sha256(result["content"].encode()).hexdigest()
        assert report["source"]["result_evidence_class"] == "TEST_DOUBLE"
        assert report["source"]["kind"] == "operator_report"
        assert report["source"]["page_id"] == report_page
        assert report["source"]["relation"] == ("source" if from_source else "shared_recipient")
        assert result["content"] not in json.dumps(report)
        if not from_source:
            assert all(key not in response.json()["experience"] for key in ("source", "encounter", "feedback"))
        # The same optimistic revision check still applies to anchored reports.
        assert client.post(endpoint, json={
            "expectedRevision": 1, "verdict": "harmful", "evidence": "A stale report.",
            "resultMessageId": result["id"],
        }).status_code == 409
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        for _ in range(501):
            send(client, report_page, "Explicit queued history fixture.")
        recent = client.get(f"/api/pages/{report_page}").json()
        assert all(m["id"] != result["id"] for m in recent["messages"])
        owner = client.get("/api/pages/home/posture").json()["experiences"][0]
        assert owner["feedback"][-1] == report
        anchored = client.get(f"/api/pages/{report_page}", params={"sourceMessageId": result["id"]}).json()
        assert anchored["earlierSourceMessageId"] == result["id"]
        assert anchored["messages"][0]["content"] == result["content"]
        assert len(anchored["messages"]) == 501
        # Submitting an old result also resolves against durable history, not
        # just the most recent messages supplied to the current browser.
        older = client.post(endpoint, json={
            "expectedRevision": 2, "verdict": "harmful", "evidence": "The later review found a problem.",
            "resultMessageId": result["id"],
        })
        assert older.status_code == 200 and not older.json()["experience"]["active"]


def test_outcome_anchor_keeps_original_result_across_native_succession(tmp_path, monkeypatch):
    """A successor can report an original Work result without copying its history."""
    from per_pulse.service import PageService
    from test_pulse_integration import TransportFixture

    fixture = TransportFixture(monkeypatch)
    pages = PageService(tmp_path, posture=True, model_factory=fixture.factory)

    def execute(page_id, message_id):
        runtime = pages.runtime
        context = "work:" + runtime.world.get_task_front(page_id).center_id
        return runtime._mgr.pulse(
            runtime.continuity_engram_id,
            pulse_event=runtime.causal_ledger.get_event(message_id),
            execution_context=context, runtime_fence=runtime._current_runtime_fence(),
        )

    try:
        source = pages.create_page("Original lesson", "A_LOCAL_HISTORY")
        source_id = source["page"]["id"]
        original = execute(source_id, source["initialMessageId"])
        correction = pages.send(source_id, "Offer a playable moment before optional setup.")
        execute(source_id, correction["messageId"])
        saved = pages.learning.create(source_id, lesson(
            original.result_event_id, correction["messageId"], scope="subject",
        ))["experience"]
        receiver = pages.create_page("Later result", "B_LOCAL_HISTORY")
        receiver_id = receiver["page"]["id"]
        result = execute(receiver_id, receiver["initialMessageId"])
        runtime = pages.runtime
        predecessor = runtime.continuity_engram_id
        preparation = runtime._mgr.prepare_succession(
            predecessor, parent_event_id=result.result_event_id,
            runtime_fence=runtime._current_runtime_fence(),
        )
        successor = runtime._mgr.commit_succession(
            preparation, runtime_fence=runtime._current_runtime_fence(),
        ).new_id
        assert successor != predecessor
        execute(source_id, pages.send(source_id, "Continue in the original lesson Work.")["messageId"])
        response = pages.learning.feedback(
            receiver_id, saved["id"], 1, "useful", "The earlier result provided a usable first step.",
            result_message_id=result.result_event_id,
        )["experience"]
        assert all(key not in response for key in ("source", "encounter", "feedback"))
        owner = pages.learning.view(source_id)["experiences"][0]
        report = owner["feedback"][-1]
        assert report["source"]["result_message_id"] == result.result_event_id
        assert report["source"]["result_turn_id"] == result.turn_id
        original_result = runtime.causal_ledger.get_event(result.result_event_id)
        assert original_result.engram_id == predecessor
        assert report["source"]["result_content_sha256"] == hashlib.sha256(original_result.content.encode()).hexdigest()
        assert owner["source"] == saved["source"]
        assert owner["encounter"] == saved["encounter"]

        assert pages.runtime.close().clean
        pages = PageService(tmp_path, posture=True, model_factory=None)
        assert pages.runtime.continuity_engram_id == successor
        assert pages.learning.view(source_id)["experiences"][0]["feedback"][-1] == report
        repeated = pages.learning.feedback(
            receiver_id, saved["id"], 2, "not_useful", "A later review found no clear benefit.",
            result_message_id=result.result_event_id,
        )["experience"]
        assert repeated["revision"] == 3 and repeated["active"]
        assert all(key not in repeated for key in ("source", "encounter", "feedback"))
    finally:
        pages.runtime.close()


def test_invalid_outcome_anchors_leave_the_lesson_unchanged(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        request, original, correction = observed_pair(client)
        saved = client.post("/api/pages/home/posture/experiences", json=lesson(
            original, correction, scope="subject",
        )).json()["experience"]
        page_id = new_page(client, title="Receiving Work")["page"]["id"]
        view = wait_for(client, page_id, lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        result_id = next(m["id"] for m in view["messages"] if m["role"] == "assistant")
        endpoint = f"/api/pages/{page_id}/posture/experiences/{saved['id']}/feedback"

        def submit(result_id):
            return client.post(endpoint, json={
                "expectedRevision": 1, "verdict": "harmful", "evidence": "A reported problem.",
                "resultMessageId": result_id,
            })

        for invalid in ("not-a-real-message", original, request):
            assert submit(invalid).status_code == 404
        local_input = next(m["id"] for m in view["messages"] if m["role"] == "user")
        assert submit(local_input).status_code == 422
        for invalid in ("", "x" * 161, {}, 42, False):
            assert submit(invalid).status_code == 422

        # Even a same-page assistant event must have a completed accepted native
        # turn. Simulate corrupt/incomplete persisted provenance, then restore it.
        storage = client.app.state.pages.runtime.storage
        with storage._lock:
            storage._conn.execute("UPDATE harness_turns SET prompt_accepted=0 WHERE result_event_id=?", (result_id,))
            storage._conn.commit()
        assert submit(result_id).status_code == 422
        with storage._lock:
            storage._conn.execute("UPDATE harness_turns SET prompt_accepted=1 WHERE result_event_id=?", (result_id,))
            storage._conn.commit()
        current = client.get("/api/pages/home/posture").json()["experiences"][0]
        assert current == saved, "Rejected anchors must not pause or revise the lesson."
