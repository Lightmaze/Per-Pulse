"""Concrete examples share the native journal without requiring an abstraction.

Transport and controlled Kernel outcomes here are explicitly synthetic. These
checks establish provenance and lifecycle, not model learning effectiveness.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from pulse_harness.core.contracts import ActionStatus, ToolCall
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.provider import CancellationToken
from pulse_harness.plugins.posture import pack_posture_materials
from pulse_system.service.native_posture import capture_example, validate
from pulse_system.service.posture import LEGACY_SCHEMA, SCHEMA, PostureMemory
from pulse_system.substrate.storage import Storage
from test_native_posture import native, remember, settle
from test_pages import client_for, http_factory, new_page, send, wait_for
from test_posture_runtime import episode


def example(**changes):
    values = {
        "subject_id": "subject", "context_id": "origin", "title": "A concrete first screen",
        "representation": "example", "excerpt": "Press Play.\nThen compare  two  attempts.",
        "note": "The spacing and order are part of this particular example.",
        "encounter": {"situation": "PRIVATE_SITUATION", "action": "PRIVATE_SURROUNDING_TEXT"},
        "source": {"kind": "explicit_unit_fixture", "feedback_evidence_class": "not_attached"},
        "record_type": "constructed_fixture",
    }
    return {**values, **changes}


def test_example_is_independent_and_transfer_keeps_the_selected_passage_not_the_episode(tmp_path):
    storage = Storage(tmp_path / "world.db")
    memory = PostureMemory(storage, "world")
    try:
        saved = memory.add_experience(**example())
        assert not {"lesson", "conditions", "counterexamples", "shift"} & set(saved)
        assert "outcome" not in saved["encounter"] and saved["feedback"] == []
        assert memory.materials(subject_id="subject", context_id="another")["episodes"] == []
        shared = memory.revise_experience(saved["id"], expected_revision=1, changes={"scope": "subject"})
        offered = memory.materials(subject_id="subject", context_id="another")["episodes"][0]
        assert offered["representation"] == "example"
        assert offered["excerpt"] == saved["excerpt"] and offered["note"] == saved["note"]
        assert "PRIVATE_" not in json.dumps(offered)
        assert "encounter" not in offered and not offered["encounter_available"]
        assert memory.materials(subject_id="other-subject", context_id="origin")["episodes"] == []
        refined = memory.revise_from_feedback(saved["id"], expected_revision=2,
                                             changes={"note": "The newer task requires a booking first."},
                                             evidence="PRIVATE_REFINEMENT", source={"kind": "operator_report"})
        assert refined["excerpt"] == saved["excerpt"] and refined["source"] == saved["source"]
        assert refined["history"][-1]["note"] == shared["note"]
        for changes in ({"excerpt": "A rewritten past"}, {"representation": "lesson"},
                        {"lesson": "A forced rule"}, {"conditions": "Any task"}):
            with pytest.raises(ValueError):
                memory.revise_experience(saved["id"], expected_revision=3, changes=changes)
        assert memory.get_experience(saved["id"])["revision"] == 3
    finally:
        storage.close()
    reopened = Storage(tmp_path / "world.db")
    try:
        memory = PostureMemory(reopened, "world")
        assert memory.get_experience(saved["id"])["excerpt"] == saved["excerpt"]
        assert "PRIVATE_REFINEMENT" not in json.dumps(memory.materials(subject_id="subject", context_id="another"))
    finally:
        reopened.close()


def test_existing_v1_lessons_upgrade_in_the_same_journal_without_rewriting_history():
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        lesson = memory.add_experience(**episode())
        state = storage.load_component_state(memory.component)
        state["schema"] = LEGACY_SCHEMA
        state["experiences"][lesson["id"]].pop("representation")
        before = deepcopy(state["experiences"][lesson["id"]])
        storage.save_component_state(memory.component, state)
        assert memory.get_experience(lesson["id"]) == before
        assert storage.load_component_state(memory.component)["schema"] == LEGACY_SCHEMA
        saved = memory.add_experience(**example())
        after = storage.load_component_state(memory.component)
        assert after["schema"] == SCHEMA
        assert after["experiences"][lesson["id"]] == before
        assert len(after["experiences"]) == 2
        offered = memory.materials(subject_id="subject", context_id="origin")["episodes"]
        assert {item["representation"] for item in offered} == {"lesson", "example"}
        assert {item["id"] for item in offered} == {lesson["id"], saved["id"]}
    finally:
        storage.close()


def test_an_example_is_offered_intact_or_explicitly_omitted_by_the_material_budget():
    record = PostureMemory.validate_experience(**example(excerpt="A line with two  spaces.\n" * 160))
    packet = pack_posture_materials({"episodes": [record]}, byte_budget=16000)
    assert packet["episodes"][0]["excerpt"] == record["excerpt"]
    assert "excerpt" not in packet["episodes"][0]["clipped_fields"]
    omitted = pack_posture_materials({"episodes": [record]}, byte_budget=1000)
    assert omitted["episodes"] == []
    assert omitted["omitted_posture_revisions"] == [{"id": record["id"], "revision": 1}]


@pytest.mark.parametrize("changes", [{"excerpt": ""}, {"excerpt": "界" * 3000},
                                     {"note": []}, {"representation": "assured_success"},
                                     {"lesson": "Do not force a rule into a concrete example."}])
def test_invalid_example_leaves_the_journal_unwritten(changes):
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        with pytest.raises(ValueError):
            memory.add_experience(**example(**changes))
        assert storage.load_component_state(memory.component) is None
    finally:
        storage.close()


def test_examples_keep_original_sources_through_committed_succession_and_cold_image(tmp_path, monkeypatch):
    from per_pulse.service import PageService
    from pulse_system.product import image
    from test_pulse_integration import TransportFixture

    home = tmp_path / "source"
    fixture = TransportFixture(monkeypatch)
    pages = PageService(home, posture=True, model_factory=fixture.factory)
    try:
        created = pages.create_page("Original concrete result", "A_LOCAL_HISTORY")
        page_id = created["page"]["id"]
        runtime = pages.runtime
        predecessor = runtime.continuity_engram_id
        context = "work:" + runtime.world.get_task_front(page_id).center_id
        original = runtime._mgr.pulse(
            predecessor, pulse_event=runtime.causal_ledger.get_event(created["initialMessageId"]),
            execution_context=context, runtime_fence=runtime._current_runtime_fence(),
        )
        original_event = runtime.causal_ledger.get_event(original.result_event_id)
        selected = {"sourceMessageId": original.result_event_id, "title": "Original example",
                    "excerpt": original_event.content, "note": "Keep the concrete fixture output."}
        first = pages.learning.create_example(page_id, selected)["experience"]
        shared = pages.learning.revise(page_id, first["id"], 1, {"scope": "subject"})["experience"]
        preparation = runtime._mgr.prepare_succession(
            predecessor, parent_event_id=original.result_event_id,
            runtime_fence=runtime._current_runtime_fence(),
        )
        with pytest.raises(ScopeConflict):
            capture_example(runtime, preparation.successor_id, context,
                            original.result_event_id, original_event.content)
        successor = runtime._mgr.commit_succession(
            preparation, runtime_fence=runtime._current_runtime_fence(),
        ).new_id
        second = pages.learning.create_example(page_id, {**selected, "title": "Selected after succession"})["experience"]
        assert second["source"]["subject_id"] == successor
        assert second["source"]["source_subject_id"] == predecessor
        assert second["sourceAnchor"]["resultMessageId"] == original.result_event_id
        assert first["source"] == shared["source"]
        assert pages.learning.view("home")["experiences"][0]["id"] == first["id"]
        assert "encounter" not in pages.learning.view("home")["experiences"][0]
        world_id = runtime.world_id
    finally:
        assert pages.runtime.close().clean
    archive, restored = tmp_path / "examples.pulse", tmp_path / "restored"
    image.save(home, archive)
    receipt = image.restore(archive, restored)
    assert receipt["world_id"] == world_id and receipt["model_calls"] == 0
    pages = PageService(restored, posture=True, model_factory=None)
    try:
        assert pages.runtime.continuity_engram_id == successor
        records = {item["id"]: item for item in pages.learning.view(page_id)["experiences"]}
        assert records == {first["id"]: shared, second["id"]: second}
        returned = pages.view(page_id, source_message_id=original.result_event_id)
        assert next(item for item in returned["messages"] if item["id"] == original.result_event_id)["content"] == original_event.content
    finally:
        assert pages.runtime.close().clean


@pytest.mark.parametrize("with_feedback", [False, True])
def test_native_example_uses_the_original_source_and_waits_for_its_turn(native, with_feedback):
    values = {"op": "remember_example", "source_message_id": native.original.id,
              "title": "An intake-first result to revisit", "excerpt": "twelve-field intake form.",
              "note": "An example of a prior choice, not an endorsement."}
    if with_feedback:
        values["feedback_message_id"] = native.feedback
    native.states.arguments = values
    receipt = remember(native, values)
    assert receipt["status"] == "prepared"
    assert remember(native, values) == receipt
    native.tool.drain()
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []
    settle(native); native.tool.drain()
    saved = native.memory.get_experience(receipt["experience_id"])
    assert saved["representation"] == "example" and saved["excerpt"] == values["excerpt"]
    assert "lesson" not in saved and saved["feedback"] == []
    assert ("outcome" in saved["encounter"]) is with_feedback
    assert saved["record_type"] == "constructed_fixture"
    assert saved["source"]["source_message_id"] == native.original.id
    selection = saved["source"]["selected_excerpt"]
    assert native.original.content[selection["start"]:selection["end"]] == values["excerpt"]
    assert selection["sha256"] == hashlib.sha256(values["excerpt"].encode()).hexdigest()
    native.tool.drain()
    assert len(native.memory.list_experiences(subject_id=native.scope.engram_id)) == 1


def test_failed_native_example_action_and_fabricated_passage_cannot_teach(native):
    values = {"op": "remember_example", "source_message_id": native.original.id,
              "title": "Concrete result", "excerpt": "twelve-field intake form."}
    with pytest.raises(ScopeConflict, match="exact passage"):
        remember(native, {**values, "excerpt": "An invented better outcome"})
    for forbidden in ({"scope": "subject"}, {"encounter": {}}, {"lesson": "A hidden rule"}):
        with pytest.raises(ValueError):
            validate({**values, **forbidden})
    native.states.arguments = values
    receipt = remember(native, values)
    native.states.action = ActionStatus.CANCELLED
    settle(native); native.tool.drain()
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "cancelled"


def test_example_source_cannot_come_from_another_context_or_a_future_result(native):
    with pytest.raises(ScopeConflict):
        capture_example(native.runtime, native.scope.engram_id, "work:somewhere-else",
                        native.original.id, "twelve-field intake form.")
    with pytest.raises(ScopeConflict, match="not available"):
        capture_example(native.runtime, native.scope.engram_id, "", native.original.id,
                        "twelve-field intake form.", latest_input_id=native.original.parent_event_id)


def test_native_find_and_get_search_only_eligible_example_content(native):
    saved = native.memory.add_experience(**example(subject_id=native.scope.engram_id, context_id="",
                                                   excerpt="The wintergarden has a concrete interaction."))
    private = native.memory.add_experience(**example(subject_id=native.scope.engram_id, context_id="work:private",
                                                     excerpt="The wintergarden private example."))
    def read(arguments):
        return native.tool.execute(native.scope, ToolCall("lookup", "posture", arguments),
                                   CancellationToken(), action_request_id="read-example")
    found = read({"op": "find", "query": "wintergarden"})
    assert [item["id"] for item in found["experiences"]] == [saved["id"]]
    assert found["semantic_applicability"] == "not_yet_assessed"
    assert read({"op": "find", "query": "PRIVATE_SITUATION"})["matching_count"] == 0
    assert read({"op": "get", "experience_id": saved["id"]})["experience"]["excerpt"] == saved["excerpt"]
    with pytest.raises(ScopeConflict, match="not active or accessible"):
        read({"op": "get", "experience_id": private["id"]})


def test_page_example_capture_share_report_and_restart_keep_native_sources(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        created = new_page(client, title="Original practice", instruction="PRIVATE_ORIGINAL_REQUEST")
        page_id = created["page"]["id"]
        view = wait_for(client, page_id, lambda value: any(m["role"] == "assistant" for m in value["messages"]))
        source = next(message for message in view["messages"] if message["role"] == "assistant")
        excerpt = "fixture response."
        endpoint = f"/api/pages/{page_id}/posture/examples"
        body = {"sourceMessageId": source["id"], "title": "A retained concrete response", "excerpt": excerpt}
        for invalid in ({**body, "sourceMessageId": created["initialMessageId"]},
                        {**body, "excerpt": "An invented quotation"}, {**body, "scope": "subject"}):
            assert client.post(endpoint, json=invalid).status_code == 422
        response = client.post(endpoint, json=body)
        assert response.status_code == 201, response.text
        saved = response.json()["experience"]
        assert saved["sourceAnchor"] == {"resultMessageId": source["id"]}
        assert saved["source"]["feedback_evidence_class"] == "not_attached"
        assert "outcome" not in saved["encounter"] and "lesson" not in saved
        assert saved["source"]["capture_kind"] == "operator_selected_example"
        target = new_page(client, title="Later practice")["page"]["id"]
        assert client.get(f"/api/pages/{target}/posture").json()["experiences"] == []
        assert client.post(f"/api/pages/{target}/posture/examples", json=body).status_code == 404
        update = f"/api/pages/{page_id}/posture/experiences/{saved['id']}"
        assert client.patch(update, json={"expectedRevision": 1, "changes": {"scope": "subject"}}).status_code == 200
        shared = client.get(f"/api/pages/{target}/posture").json()["experiences"][0]
        assert shared["excerpt"] == excerpt and shared["canReport"] and not shared["canEdit"]
        assert not {"encounter", "source", "feedback"} & set(shared)
        assert "PRIVATE_ORIGINAL_REQUEST" not in json.dumps(shared)
        report = client.post(f"/api/pages/{target}/posture/experiences/{saved['id']}/feedback", json={
            "expectedRevision": 2, "verdict": "not_useful", "evidence": "PRIVATE_REPORT: the new requirement differed.",
        })
        assert report.status_code == 200 and report.json()["experience"]["active"]
        assert "PRIVATE_REPORT" not in json.dumps(report.json())
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        source_view = client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]
        assert source_view["id"] == saved["id"] and source_view["revision"] == 3
        assert source_view["excerpt"] == excerpt
        assert source_view["feedback"][0]["evidence"].startswith("PRIVATE_REPORT")
        assert client.get(f"/api/pages/{page_id}?sourceMessageId={source['id']}").status_code == 200


def test_page_example_can_attach_real_ordered_followup_without_treating_it_as_success(tmp_path, monkeypatch):
    with client_for(tmp_path, posture=True, model_factory=http_factory(monkeypatch, [])) as client:
        initial = send(client, "home", "Make a small practice.")
        view = wait_for(client, "home", lambda v: any(m["role"] == "assistant" for m in v["messages"]))
        source = next(m for m in view["messages"] if m["role"] == "assistant")
        followup = send(client, "home", "This particular example did not fit the new constraint.")
        endpoint = "/api/pages/home/posture/examples"
        body = {"sourceMessageId": source["id"], "title": "A failed transfer", "excerpt": "fixture response."}
        assert client.post(endpoint, json={**body, "feedbackMessageId": initial["messageId"]}).status_code == 422
        result = client.post(endpoint, json={**body, "feedbackMessageId": followup["messageId"]})
        assert result.status_code == 201, result.text
        saved = result.json()["experience"]
        assert saved["sourceAnchor"]["feedbackMessageId"] == followup["messageId"]
        assert saved["encounter"]["outcome"].startswith("This particular example did not fit")
        assert saved["feedback"] == []
