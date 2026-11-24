"""Actual request boundaries, intact history and ordinary return; no learning claim.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json

import pytest

from pulse_harness.core import TranscriptStore, TurnStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.response_review import RESPONSE_REVIEW_PREFIX, decode_review_envelope
from pulse_system.service import native_replay
from focused_replay_fixture import ADVICE, FOCUS, PREAMBLE, REASONING, UNRELATED, FocusedReplayFixture
from replay_fixture import ORIGINAL, RECONSIDERED
from test_pages import client_for, new_page, send, wait_for


@pytest.mark.parametrize("knowledge_review", [False, True])
def test_focused_requests_exclude_unselected_material_and_resume_context_next_turn(tmp_path, monkeypatch, knowledge_review):
    wiki_summary = "Unselected knowledge: this collection describes an older visitor survey."
    if knowledge_review:
        wiki = tmp_path / "wiki.json"
        wiki.write_text(json.dumps({"schema_version": 1, "entries": [{"id": "visitor-survey",
            "title": "Visitor survey", "summary": wiki_summary, "source_url": "https://example.test/survey",
            "aliases": [], "keywords": ["visitor"]}]}))
        monkeypatch.setenv("P_PULSE_COVERME", "1")
        monkeypatch.setenv("P_PULSE_COVERME_WIKI", str(wiki))
    fixture = FocusedReplayFixture(monkeypatch.setenv, hold=True)
    with client_for(tmp_path, model_factory=fixture.factory, posture=True, allow_workspace_actions=True) as client:
        page_id = new_page(client, instruction=UNRELATED)["page"]["id"]
        original = wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")["messages"][-1]
        note = "Private example note: the paper map has a blue border."
        response = client.post(f"/api/pages/{page_id}/posture/examples", json={
            "sourceMessageId": original["id"], "title": "An earlier example", "excerpt": ORIGINAL, "note": note,
        })
        assert response.status_code == 201, response.text
        send(client, page_id, "RECONSIDER_SAVED_DESIGN. Unselected current input: the parcel arrived yesterday.")
        try:
            assert fixture.entered.wait(8)
            active = client.get(f"/api/pages/{page_id}").json()["execution"]
            assert active["replay"] == {"sourceCount": 1, "focus": FOCUS}
            after_entry_requests = len(fixture.requests)
            before_auxiliary = len(fixture.auxiliary_requests)
            before_knowledge = len(fixture.knowledge_requests)
            assert before_auxiliary >= 1
            assert bool(before_knowledge) is knowledge_review
        finally:
            fixture.release.set()
        settled = wait_for(client, page_id, lambda v: len([m for m in v["messages"] if m["role"] == "assistant"]) == 2)
        replayed = settled["messages"][-1]
        assert replayed["content"][replayed["replay"]["contentOffset"]:] == RECONSIDERED
        assert replayed["replay"]["focus"] == FOCUS
        assert replayed["replay"]["sources"][0]["messageId"] == original["id"]
        assert len(fixture.auxiliary_requests) == before_auxiliary
        assert len(fixture.knowledge_requests) == before_knowledge
        assert len(fixture.requests) == after_entry_requests + 1  # selected read, then final; no side computation
        assert len(fixture.focused_requests) == 2
        for body in fixture.focused_requests:
            wire = json.dumps(body)
            for omitted in (UNRELATED, PREAMBLE, REASONING, ADVICE, note, wiki_summary, "the parcel arrived yesterday"):
                assert omitted not in wire
            assert FOCUS in wire
            assert set(t["function"]["name"] for t in body["tools"]) == {"replay"}
        assert ORIGINAL not in json.dumps(fixture.focused_requests[0])
        assert ORIGINAL in json.dumps(fixture.focused_requests[1])  # only after the actual selected reader
        assert note in json.dumps(fixture.auxiliary_requests)

        pages = client.app.state.pages
        store = pages.runtime.kernel_service.store
        scope = pages.execution._scope(page_id).for_turn(active["turnId"])
        retained = TranscriptStore(store).messages(scope.session_scope())
        contents = "\n".join(m.content for m in retained)
        assert all(text in contents for text in (UNRELATED, PREAMBLE, REASONING, ADVICE))
        assert not any(m.item_id.startswith(("replay-focus:", "replay-boundary:")) for m in retained)
        review = next(decode_review_envelope(m.content) for m in retained
                      if m.scope.turn_id == scope.turn_id and m.item_id.startswith(RESPONSE_REVIEW_PREFIX))
        assert review["receipt"]["reason"] == "focused_replay_material_boundary"
        entry = native_replay.result_origin(pages.runtime, replayed["id"])
        assert entry["input_boundary"] == native_replay.FOCUSED_BOUNDARY

        # Existing history readers omit tool continuations, including the entry
        # preamble. It remains in the native transcript without becoming a
        # separately selectable replay result.
        rows = store.transcript_rows(scope)
        earlier = next(row for row in rows if row["role"] == "assistant" and PREAMBLE in row["content"])
        assert store.read_transcript_message(scope.session_scope(), earlier["message_id"]) is None

        # A missing/mismatched delivered receipt must not degrade to broad history.
        with pytest.raises(ScopeConflict, match="delivered entry receipt"):
            native_replay.project_model_messages(store, scope, ({"role": "user", "content": UNRELATED},))

        send(client, page_id, "TRY_ORDINARY_ACTION")
        pending = wait_for(client, page_id, lambda v: any(
            action["canResolve"] for action in v["execution"]["pendingActions"]))
        assert pending["execution"]["replay"] is None
        body = next(b for b in reversed(fixture.requests) if b.get("tools"))
        assert UNRELATED in json.dumps(body)
        assert ADVICE in json.dumps(body)
        assert "mutate_file" in {t["function"]["name"] for t in body["tools"]}
        assert len(fixture.auxiliary_requests) > before_auxiliary
        action = next(action for action in pending["execution"]["pendingActions"] if action["canResolve"])
        allowed = client.post(f"/api/pages/{page_id}/execution/actions/{action['actionId']}", json={"decision": "allow_once"})
        assert allowed.status_code == 200, allowed.text
        wait_for(client, page_id, lambda v: len([m for m in v["messages"] if m["role"] == "assistant"]) == 3)
        assert (tmp_path / "workspace" / "practice.txt").is_file()
        if knowledge_review:
            send(client, page_id, "Continue the ordinary design conversation.")
            wait_for(client, page_id, lambda v: len([m for m in v["messages"] if m["role"] == "assistant"]) == 4)
            assert len(fixture.knowledge_requests) > before_knowledge
    with client_for(tmp_path, model_factory=None, posture=True) as client:
        restored = client.get(f"/api/pages/{page_id}").json()
        assert next(m for m in restored["messages"] if m["id"] == replayed["id"])["replay"] == replayed["replay"]


def test_focused_replay_cannot_regain_file_authority(tmp_path, monkeypatch):
    fixture = FocusedReplayFixture(monkeypatch.setenv, forbidden_tool=True)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page_id = new_page(client)["page"]["id"]
        wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")
        send(client, page_id, "RECONSIDER_SAVED_DESIGN")
        failed = wait_for(client, page_id, lambda v: v["page"]["status"] == "error")
        assert len([m for m in failed["messages"] if m["role"] == "assistant"]) == 1
        assert not failed["execution"]["pendingActions"]
        assert not (tmp_path / "workspace" / "forbidden.txt").exists()


@pytest.mark.parametrize("broken", ["missing_receipt", "invalid_projection", "projection_exception"])
def test_projection_failure_finishes_without_broadening_or_a_phantom_model_attempt(tmp_path, monkeypatch, broken):
    from pulse_system.service.kernel_runtime import _WorldWorkspace
    original_projection = _WorldWorkspace.project_model_messages

    def project(provider, scope, messages):
        if native_replay.focused(provider.store, scope):
            if broken == "missing_receipt":
                messages = tuple(m for m in messages if not m["item_id"].startswith("tool-result:"))
            elif broken == "invalid_projection":
                return ({"role": "user", "content": None},)
            else:
                raise ValueError("Explicit synthetic projection fault")
        return original_projection(provider, scope, messages)

    monkeypatch.setattr(_WorldWorkspace, "project_model_messages", project)
    fixture = FocusedReplayFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page_id = new_page(client)["page"]["id"]
        wait_for(client, page_id, lambda v: v["messages"][-1]["role"] == "assistant")
        send(client, page_id, "RECONSIDER_SAVED_DESIGN")
        failed = wait_for(client, page_id, lambda v: v["page"]["status"] == "error")
        pages = client.app.state.pages
        scope = pages.execution._scope(page_id).for_turn(failed["execution"]["turnId"])
        store = pages.runtime.kernel_service.store
        assert store.get_turn(scope).status is TurnStatus.FAILED
        assert len(store.model_rounds_for_turn(scope)) == 2  # search, entry; no broadened third request
        assert not fixture.focused_requests
        assert len([m for m in failed["messages"] if m["role"] == "assistant"]) == 1


@pytest.mark.parametrize("focus", [None, "", "  ", 5, True, "x" * 1001, "before\x00after"])
def test_invalid_focus_never_enters_a_regime(focus):
    with pytest.raises(ValueError, match="focus"):
        native_replay.validate({"op": "enter", "message_ids": ["actual-id"], "focus": focus})
