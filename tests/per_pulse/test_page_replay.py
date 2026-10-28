"""Selected-history replay through the real World, Kernel and page API.

The provider's choices are explicitly scripted; no model learning is measured.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from copy import deepcopy
import json

from per_pulse.replay import RETURN_PREFIX
from pulse_harness.plugins.posture import pack_posture_materials
from pulse_system.service import native_replay
from test_pages import client_for, new_page, send, wait_for
from replay_fixture import ORIGINAL, RECONSIDERED, ReplayFixture


def test_native_replay_reenters_original_sources_then_restores_ordinary_tools(tmp_path, monkeypatch):
    fixture = ReplayFixture(monkeypatch.setenv, hold=True)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page_id = new_page(client, title="Design exploration")["page"]["id"]
        original = wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")["messages"][-1]
        send(client, page_id, "RECONSIDER_SAVED_DESIGN")
        try:
            assert fixture.entered.wait(8)
            running = client.get(f"/api/pages/{page_id}").json()
            assert running["execution"]["canCancel"]
            assert running["execution"]["replay"] == {"sourceCount": 1}
        finally:
            fixture.release.set()
        settled = wait_for(client, page_id, lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 2)
        replayed = settled["messages"][-1]
        assert replayed["content"].startswith("[Replay: tentative reconsideration")
        assert replayed["content"].endswith(RECONSIDERED)
        assert original["id"] != fixture.selected[0]["message_id"]
        assert replayed["replay"] == {
            "sources": [{"pageId": page_id, "messageId": original["id"], "role": "assistant"}],
            "unavailableSources": 0, "selected": False,
            "contentOffset": replayed["content"].index(RECONSIDERED),
        }
        assert settled["execution"]["replay"] is None
        runtime = client.app.state.pages.runtime
        event = runtime.causal_ledger.get_event(replayed["id"])
        assert event.metadata["execution_regime"] == "replay"
        send(client, page_id, "TRY_ORDINARY_ACTION")
        pending = wait_for(client, page_id, lambda v: bool(v["execution"]["pendingActions"]))
        action = pending["execution"]["pendingActions"][0]
        assert action["toolName"] == "mutate_file"
        assert pending["execution"]["replay"] is None
        response = client.post(f"/api/pages/{page_id}/execution/actions/{action['actionId']}", json={"decision": "allow_once"})
        assert response.status_code == 200, response.text
        wait_for(client, page_id, lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 3)
        assert (tmp_path / "workspace" / "practice.txt").read_text() == "The next ordinary turn can work.\n"


def settled_replay(client, fixture):
    page_id = new_page(client, title="Design exploration")["page"]["id"]
    original = wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")["messages"][-1]
    send(client, page_id, "RECONSIDER_SAVED_DESIGN")
    view = wait_for(client, page_id, lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 2)
    return page_id, original, view["messages"][-1]


def test_selected_interpretation_keeps_origin_when_saved_shared_and_returned(tmp_path, monkeypatch):
    fixture = ReplayFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory, posture=True) as client:
        page_id, original, replayed = settled_replay(client, fixture)
        pages = client.app.state.pages
        saved = client.post(f"/api/pages/{page_id}/posture/examples", json={
            "sourceMessageId": replayed["id"], "title": "An alternative to test", "excerpt": RECONSIDERED,
        })
        assert saved.status_code == 201, saved.text
        record = saved.json()["experience"]
        assert record["excerpt"] == RECONSIDERED
        assert record["sourceRegime"] == "replay"
        assert record["source"]["execution_regime"] == "replay"
        assert record["source"]["replay_origin"]["selected_sources"] == fixture.selected
        assert record["source"]["feedback_evidence_class"] == "not_attached"
        shared = pages.learning.revise(page_id, record["id"], 1, {"scope": "subject"})["experience"]
        receiver = new_page(client, title="Later work")["page"]["id"]
        offered = pages.learning.view(receiver)["experiences"][0]
        assert offered["sourceRegime"] == "replay"
        assert offered["sourceAnchor"] == {"resultMessageId": replayed["id"]}
        assert not {"source", "encounter", "feedback"} & set(offered)
        memory = pages.runtime.harness.posture_memory
        packed = pack_posture_materials(memory.materials(
            subject_id=pages.runtime.continuity_engram_id, context_id=pages.learning._context(receiver),
        ))
        offered_material = packed["episodes"][0]
        assert offered_material["source"]["execution_regime"] == "replay"
        assert offered_material["source"]["meaning"] == "replay_interpretation_not_new_external_observation"
        assert "selected_sources" not in json.dumps(packed)
        assert ORIGINAL not in json.dumps(packed)
        reported = pages.learning.feedback(page_id, record["id"], shared["revision"], "not_useful",
            "This remains a hypothesis to test.", result_message_id=replayed["id"])["experience"]
        assert reported["feedback"][-1]["source"]["result_execution_regime"] == "replay"

        returned = client.post(f"/api/pages/{page_id}/return", json={"content": RECONSIDERED, "messageId": replayed["id"]})
        assert returned.status_code == 201
        returned_id = returned.json()["messageId"]
        home = next(m for m in returned.json()["view"]["messages"] if m["id"] == returned_id)
        assert home["content"] == RETURN_PREFIX + RECONSIDERED
        assert home["replay"]["selected"] is True
        assert home["replay"]["sources"][0]["messageId"] == original["id"]
        wait_for(client, "home", lambda v: v["messages"][-1]["role"] == "assistant")
        home_request = next(body for body in reversed(fixture.requests) if RETURN_PREFIX in json.dumps(body["messages"]).replace("\\n", "\n"))
        assert "not a new external observation" in json.dumps(home_request)
        assert fixture.selected[0]["message_id"] not in json.dumps(home_request)

    count = len(fixture.requests)
    with client_for(tmp_path, model_factory=None, posture=True) as client:
        restored = client.get(f"/api/pages/{page_id}").json()
        assert restored["messages"][-1]["replay"] == replayed["replay"]
        home = next(m for m in client.get("/api/pages/home").json()["messages"] if m["id"] == returned_id)
        assert home["replay"]["selected"] is True
        assert client.get(f"/api/pages/{receiver}/posture").json()["experiences"][0]["sourceRegime"] == "replay"
        assert len(fixture.requests) == count
        assert not client.app.state.pages.runtime.kernel_service._runtimes


def test_replay_blocks_an_attempted_file_action_and_never_fabricates_a_result(tmp_path, monkeypatch):
    fixture = ReplayFixture(monkeypatch.setenv, forbidden_tool=True)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page_id = new_page(client)["page"]["id"]
        wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")
        send(client, page_id, "RECONSIDER_SAVED_DESIGN")
        failed = wait_for(client, page_id, lambda v: v["page"]["status"] == "error")
        assert sum(m["role"] == "assistant" for m in failed["messages"]) == 1
        assert failed["execution"]["replay"] is None
        assert not failed["execution"]["pendingActions"]
        assert not (tmp_path / "workspace" / "forbidden.txt").exists()
        assert not failed["artifacts"]


def test_stopping_replay_ends_its_visible_regime_and_discards_late_output(tmp_path, monkeypatch):
    fixture = ReplayFixture(monkeypatch.setenv, hold=True)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page_id = new_page(client)["page"]["id"]
        wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")
        send(client, page_id, "RECONSIDER_SAVED_DESIGN")
        try:
            assert fixture.entered.wait(8)
            active = client.get(f"/api/pages/{page_id}").json()["execution"]
            assert active["replay"] == {"sourceCount": 1}
            stopped = client.post(f"/api/pages/{page_id}/execution/stop", json={"turnId": active["turnId"]})
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["view"]["execution"]["replay"] is None
        finally:
            fixture.release.set()
        view = wait_for(client, page_id, lambda v: v["execution"]["status"] == "stopped")
        assert sum(m["role"] == "assistant" for m in view["messages"]) == 1


def test_written_replay_words_and_unbacked_metadata_do_not_mint_provenance(tmp_path, monkeypatch):
    fixture = ReplayFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page_id = new_page(client)["page"]["id"]
        ordinary = wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")["messages"][-1]
        pages = client.app.state.pages
        row = pages._rows(page_id, limit=1, event_id=ordinary["id"])[0]
        row["metadata"]["execution_regime"] = "replay"
        row["content"] = "[Replay: tentative reconsideration; original sources: invented]\nUnverified text."
        assert pages._messages(page_id, [row], [])[0]["replay"] is None
        assert native_replay.result_origin(pages.runtime, ordinary["id"]) is None
        returned = client.post(f"/api/pages/{page_id}/return", json={"content": "An ordinary selected reply.", "messageId": ordinary["id"]}).json()
        message = next(m for m in returned["view"]["messages"] if m["id"] == returned["messageId"])
        assert message["replay"] is None
        assert message["content"] == "An ordinary selected reply."


def test_original_sources_outside_recent_history_remain_verifiable_and_scope_bound(tmp_path, monkeypatch):
    import per_pulse.service as service_module
    from pulse_harness.adapters.history import source_identity

    fixture = ReplayFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page_id, original, replayed = settled_replay(client, fixture)
        pages = client.app.state.pages
        with monkeypatch.context() as bounded:
            bounded.setattr(service_module, "MAX_MESSAGES", 2)
            view = pages.view(page_id)
            assert original["id"] not in {m["id"] for m in view["messages"]}
            assert view["messages"][-1]["replay"]["sources"][0]["messageId"] == original["id"]
            linked = pages.view(page_id, source_message_id=original["id"])
            assert any(m["id"] == original["id"] for m in linked["messages"])
        found = pages.search_messages(page_id, "tentative alternative")["messages"][0]
        assert found["replay"] == replayed["replay"]
        send(client, page_id, "Continue an ordinary discussion.")
        latest = wait_for(client, page_id, lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 3)["messages"][-1]
        older = pages.history(page_id, before_message_id=latest["id"])
        assert next(m for m in older["messages"] if m["id"] == replayed["id"])["replay"] == replayed["replay"]

        entry = native_replay.result_origin(pages.runtime, replayed["id"])
        altered = deepcopy(entry)
        altered["sources"][0]["content_digest"] = "0" * 64
        assert pages.replay._sources(page_id, altered) == []
        sibling = new_page(client, title="Private sibling")["page"]["id"]
        wait_for(client, sibling, lambda v: v["messages"][-1]["role"] == "assistant")
        foreign = pages.runtime.kernel_service.store.search_transcript(pages.execution._scope(sibling), ORIGINAL)
        altered["sources"] = [source_identity(next(row for row in foreign if row["role"] == "assistant"))]
        assert pages.replay._sources(page_id, altered) == []


def test_replay_sources_survive_committed_succession_and_a_cold_image(tmp_path, monkeypatch):
    from per_pulse.service import PageService
    from pulse_system.product import image

    directory = tmp_path / "source"
    fixture = ReplayFixture(monkeypatch.setenv)
    pages = PageService(directory, model_factory=fixture.factory, posture=True)
    try:
        created = pages.create_page("Remembered design", "Explore the original design.")
        page_id = created["page"]["id"]
        runtime = pages.runtime
        predecessor = runtime.continuity_engram_id
        context = pages.learning._context(page_id)

        def pulse(event_id):
            return runtime._mgr.pulse(runtime.continuity_engram_id,
                pulse_event=runtime.causal_ledger.get_event(event_id), execution_context=context,
                runtime_fence=runtime._current_runtime_fence())

        original = pulse(created["initialMessageId"])
        event_id = runtime.send_task_front_message(page_id, "RECONSIDER_SAVED_DESIGN")
        replayed = pulse(event_id)
        before = pages.view(page_id)["messages"][-1]
        assert before["replay"]["sources"][0]["messageId"] == original.result_event_id
        prepared = runtime._mgr.prepare_succession(predecessor, parent_event_id=replayed.result_event_id,
                                                   runtime_fence=runtime._current_runtime_fence())
        successor = runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence()).new_id
        assert successor != predecessor
        historical = next(m for m in pages.view(page_id)["messages"] if m["id"] == replayed.result_event_id)
        assert historical["replay"] == before["replay"]
        saved = pages.learning.create_example(page_id, {"sourceMessageId": replayed.result_event_id,
            "title": "A tentative earlier interpretation", "excerpt": RECONSIDERED})["experience"]
        assert saved["source"]["source_subject_id"] == predecessor
        assert saved["sourceRegime"] == "replay"
    finally:
        assert pages.runtime.close().clean
    archive, restored = tmp_path / "replay.pulse", tmp_path / "restored"
    image.save(directory, archive)
    assert image.restore(archive, restored)["model_calls"] == 0
    pages = PageService(restored, model_factory=None, posture=True)
    try:
        assert pages.runtime.continuity_engram_id == successor
        historical = next(m for m in pages.view(page_id)["messages"] if m["id"] == replayed.result_event_id)
        assert historical["replay"] == before["replay"]
        assert pages.learning.view(page_id)["experiences"][0]["sourceRegime"] == "replay"
        assert not pages.runtime.kernel_service._runtimes
    finally:
        assert pages.runtime.close().clean
