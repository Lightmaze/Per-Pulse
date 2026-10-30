"""Original reading examples travel only by the existing explicit sharing path.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Provider decisions are synthetic, including the choice to remember an example.
"""
import hashlib
import json

import pytest

from reading_example_fixture import ReadingExampleFixture
from pulse_system.service.native_posture import validate
from test_pages import client_for, new_page, send, wait_for
from test_reading_continuity import work


def choose(client, page_id, fixture, *, timeout=8, **command):
    count = len(fixture.outcomes)
    sent = send(client, page_id, "READING_EXAMPLE_FIXTURE:" + json.dumps(command))
    wait_for(client, page_id, lambda v: any(m["id"] == sent["messageId"] and m["status"] == "settled" for m in v["messages"]), timeout=timeout)
    assert len(fixture.outcomes) == count + 1, json.dumps([r["messages"][-1] for r in fixture.requests[-2:]])
    last = fixture.outcomes[-1][-1]
    if last.get("output", {}).get("status") == "prepared":
        identity = last["output"]["experience_id"]
        wait_for(client, page_id, lambda _: any(e["id"] == identity for e in client.get(
            f"/api/pages/{page_id}/posture").json()["experiences"]))
    return fixture.outcomes[-1]


def test_reading_example_preserves_observed_text_and_source_after_change_share_and_restart(tmp_path, monkeypatch):
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    passage = "First  example.\nCafé 🌍 and e\u0301.\nSecond example."
    selected = "Café 🌍 and e\u0301."
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = work(client)
        path = tmp_path / "workspace" / "original.txt"
        path.write_text(passage)
        results = choose(client, page_id, fixture, op="open", path=path.name,
                         retain={"title": "Source punctuation", "excerpt": selected, "note": "Preserve exact spacing."})
        assert [r["status"] for r in results] == ["succeeded", "succeeded"]
        reading, receipt = results[0]["output"], results[1]["output"]
        view = wait_for(client, page_id, lambda _: len(client.get(f"/api/pages/{page_id}/posture").json()["experiences"]) == 1)
        saved = client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]
        assert saved["id"] == receipt["experience_id"] and saved["excerpt"] == selected
        assert saved["sourceKind"] == "reading" and saved["record_type"] == "constructed_fixture"
        assert saved["source"]["selector_evidence_class"] == "TEST_DOUBLE"
        assert saved["source"]["reading_source"] == reading["source"]
        assert not saved["source"]["current_file_checked"]
        assert "outcome" not in saved["encounter"] and saved["feedback"] == []
        assert "First  example" not in json.dumps(saved)  # no surrounding-text transfer
        anchor = saved["sourceAnchor"]["resultMessageId"]
        assert any(m["id"] == anchor and m["role"] == "assistant" for m in view["messages"])
        selection = saved["source"]["selected_excerpt"]
        assert reading["content"][selection["start"]:selection["end"]] == selected
        assert selection["sha256"] == hashlib.sha256(selected.encode()).hexdigest()

        path.write_text("A later version.")
        # Remembering an actual past observation is useful even after deletion.
        path.unlink()
        again = choose(client, page_id, fixture, op="remember_reading", receipt_id=reading["receipt_id"],
                       title="A retained observation", excerpt=selected)
        assert again[-1]["status"] == "succeeded"
        receiver = work(client)
        assert client.get(f"/api/pages/{receiver}/posture").json()["experiences"] == []
        shared = client.patch(f"/api/pages/{page_id}/posture/experiences/{saved['id']}",
                             json={"expectedRevision": 1, "changes": {"scope": "subject"}}).json()["experience"]
        offered = client.get(f"/api/pages/{receiver}/posture").json()["experiences"][0]
        assert offered["sourceKind"] == "reading" and offered["excerpt"] == selected
        assert not {"source", "readingSource", "encounter", "feedback"} & set(offered)
        assert path.name not in json.dumps(offered) and reading["receipt_id"] not in json.dumps(offered)
        pages = client.app.state.pages
        packet = pages.learning._memory().materials(subject_id=pages.learning._subject(receiver),
                                                     context_id=pages.learning._context(receiver))
        assert packet["episodes"][0]["source"]["kind"] == "native_reading_example"
        assert path.name not in json.dumps(packet)
        pages.runtime._native_posture.drain()
        assert len(pages.learning._memory().list_experiences(subject_id=pages.learning._subject(page_id))) == 2

    with client_for(tmp_path, posture=True, model_factory=None) as client:
        restored = client.get(f"/api/pages/{page_id}/posture").json()["experiences"]
        assert next(x for x in restored if x["id"] == saved["id"]) == shared
        assert client.get(f"/api/pages/{receiver}/posture").json()["experiences"][0] == offered


def test_unseen_foreign_search_and_nonverbatim_sources_cannot_create_examples(tmp_path, monkeypatch):
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = work(client)
        (tmp_path / "workspace" / "source.txt").write_text("Seen first. Unseen later.")
        observed = choose(client, page_id, fixture, op="open", path="source.txt", max_chars=11)[0]["output"]
        choose(client, page_id, fixture, op="find", path="source.txt", query="Seen")
        pages = client.app.state.pages
        scope = pages.runtime.harness.scope_for(pages.learning._subject(page_id), pages.learning._context(page_id))
        actions = pages.runtime.kernel_service.store.actions_for_session(scope, limit=20)
        search_id = next(a.action_request_id for a in actions if a.tool_name == "reading" and a.arguments["op"] == "find")
        other = work(client)
        attempts = [
            (page_id, observed["receipt_id"], "Unseen later."),
            (page_id, observed["receipt_id"], "seen first."),
            (other, observed["receipt_id"], "Seen first."),
            (page_id, search_id, "Seen first."),
            (page_id, "fabricated-reading", "Seen first."),
        ]
        for target, receipt, excerpt in attempts:
            result = choose(client, target, fixture, op="remember_reading", receipt_id=receipt,
                            title="Not an available observation", excerpt=excerpt)[0]
            assert result["status"] == "failed", result
            assert client.get(f"/api/pages/{target}/posture").json()["experiences"] == []


def test_source_search_can_be_followed_into_a_retained_original_example(tmp_path, monkeypatch):
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page = work(client)
        text = "Introductory context.\n" * 500 + "NEEDLE: an exact example with two  spaces."
        (tmp_path / "workspace" / "long.txt").write_text(text)
        found = choose(client, page, fixture, op="find", path="long.txt", query="NEEDLE")[0]["output"]
        followed = choose(client, page, fixture, op="follow", receipt_id=found["receipt_id"], match_index=0,
                          retain={"title": "A distant example", "excerpt": "an exact example with two  spaces."})
        assert followed[-1]["status"] == "succeeded"
        saved = client.get(f"/api/pages/{page}/posture").json()["experiences"][0]
        assert saved["source"]["reading_receipt_id"] == followed[0]["output"]["receipt_id"]
        assert saved["source"]["reading_source"]["digest"] == hashlib.sha256(text.encode()).hexdigest()
        assert saved["source"]["reading_position"]["start_char"] == text.index("NEEDLE")
        assert "Introductory" not in json.dumps(saved)


def test_casefold_example_correction_changes_next_work_material_without_rewriting_source(tmp_path, monkeypatch):
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        source, receiver = work(client), work(client)
        # This long native posture journey can finish its Kernel round just
        # after the short default wait on shared CI. Still wait for World
        # settlement, with one bounded allowance for every step.
        def step(page, **command):
            return choose(client, page, fixture, timeout=30, **command)

        text = "Use Straße as an example of expansion."
        (tmp_path / "workspace" / "case.txt").write_text(text)
        found = step(source, op="find", path="case.txt", query="STRASSE", casefold=True)[0]["output"]
        step(source, op="follow", receipt_id=found["receipt_id"], match_index=0,
               retain={"title": "Expansion", "excerpt": "Straße", "note": "Use folded offsets to select the original text."})
        original = client.get(f"/api/pages/{source}/posture").json()["experiences"][0]
        endpoint = f"/api/pages/{source}/posture/experiences/{original['id']}"
        assert client.patch(endpoint, json={"expectedRevision": 1, "changes": {"scope": "subject"}}).status_code == 200
        before = step(receiver, op="get", experience_id=original["id"])[0]["output"]["experience"]
        assert before["note"] == original["note"]
        feedback = send(client, source, "The folded string changes length. Keep the original passage and map whole-character boundaries before selecting it.")
        wait_for(client, source, lambda v: any(m["id"] == feedback["messageId"] and m["status"] == "settled" for m in v["messages"]), timeout=30)
        corrected = "Map complete original-character boundaries; do not use folded offsets on the original source."
        response = step(source, op="revise", experience_id=original["id"], expected_revision=2,
                          feedback_message_id=feedback["messageId"], changes={"note": corrected})
        assert response[0]["status"] == "succeeded"
        wait_for(client, source, lambda _: client.get(f"/api/pages/{source}/posture").json()["experiences"][0]["revision"] == 3, timeout=30)
        saved = client.get(f"/api/pages/{source}/posture").json()["experiences"][0]
        assert saved["source"] == original["source"] and saved["excerpt"] == "Straße"
        assert saved["feedback"][-1]["source"]["feedback_message_id"] == feedback["messageId"]
        after = step(receiver, op="get", experience_id=original["id"])[0]["output"]["experience"]
        assert after["note"] == corrected and after["excerpt"] == "Straße"
        assert "The folded string changes length" not in json.dumps(after)  # no private feedback transfer
        stale = step(source, op="revise", experience_id=original["id"], expected_revision=2,
                       feedback_message_id=feedback["messageId"], changes={"note": "Stale interpretation"})
        assert stale[0]["status"] == "failed"
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        retained = client.get(f"/api/pages/{source}/posture").json()["experiences"][0]
        assert retained == saved


@pytest.mark.parametrize("extra", [{"feedback_message_id": "invented"}, {"scope": "subject"},
                                    {"source_message_id": "wrong-kind"}, {"receipt_id": "\n"},
                                    {"excerpt": "界" * 3000}])
def test_reading_example_arguments_cannot_invent_feedback_share_or_exceed_bytes(extra):
    with pytest.raises(ValueError):
        validate({"op": "remember_reading", "receipt_id": "actual", "title": "Example",
                  "excerpt": "Seen text.", **extra})


def test_failed_selecting_turn_does_not_admit_prepared_reading_memory(tmp_path, monkeypatch):
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = work(client)
        (tmp_path / "workspace" / "source.txt").write_text("A real observation with no successful selector turn.")
        fixture.fail_after_prepare = True
        sent = send(client, page_id, "READING_EXAMPLE_FIXTURE:" + json.dumps({
            "op": "open", "path": "source.txt", "retain": {"title": "Not admitted", "excerpt": "real observation"}}))
        wait_for(client, page_id, lambda v: any(m["id"] == sent["messageId"] and m["status"] in {"failed", "uncertain"} for m in v["messages"]))
        assert fixture.prepared["output"]["status"] == "prepared"
        client.app.state.pages.runtime._native_posture.drain()
        assert client.get(f"/api/pages/{page_id}/posture").json()["experiences"] == []


def test_redacted_receipt_coordinates_are_not_claimed_as_original_file_offsets(tmp_path, monkeypatch):
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    text = "password=synthetic-private-credential\nKeep  two spaces.\r\n"
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page_id = work(client)
        (tmp_path / "workspace" / "redacted.txt").write_bytes(text.encode())
        results = choose(client, page_id, fixture, op="open", path="redacted.txt",
                         retain={"title": "Spacing", "excerpt": "Keep  two spaces."})
        assert results[-1]["status"] == "succeeded"
        saved = client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]
        selection = saved["source"]["selected_excerpt"]
        assert selection["offset_unit"] == "unicode_code_points_in_exposed_receipt_content"
        assert results[0]["output"]["content"][selection["start"]:selection["end"]] == saved["excerpt"]
        assert selection["start"] != text.index(saved["excerpt"])
        assert "synthetic-private-credential" not in json.dumps(saved)


def test_committed_generation_and_cold_copy_keep_a_reading_example(tmp_path, monkeypatch):
    from pulse_system.product import image
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    original, restored = tmp_path / "original", tmp_path / "restored"
    with client_for(original, posture=True, model_factory=fixture.factory) as client:
        page_id = work(client)
        (original / "workspace" / "source.txt").write_text("Retained across a committed generation.")
        reading = choose(client, page_id, fixture, op="open", path="source.txt")[0]["output"]
        pages = client.app.state.pages
        runtime = pages.runtime
        old = runtime.continuity_engram_id
        final = pages.view(page_id)["messages"][-1]
        prepared = runtime._mgr.prepare_succession(old, parent_event_id=final["id"],
                                                   runtime_fence=runtime._current_runtime_fence())
        successor = runtime._mgr.commit_succession(prepared,
            runtime_fence=runtime._current_runtime_fence()).new_id
        result = choose(client, page_id, fixture, op="remember_reading", receipt_id=reading["receipt_id"],
                        title="Original observation", excerpt="Retained across a committed generation.")
        assert result[-1]["status"] == "succeeded"
        saved = client.get(f"/api/pages/{page_id}/posture").json()["experiences"][0]
        assert saved["source"]["source_subject_id"] == old
        assert saved["source"]["subject_id"] == successor
    archive = tmp_path / "reading.pulse"
    image.save(original, archive)
    image.restore(archive, restored)
    with client_for(restored, posture=True, model_factory=None) as client:
        assert client.get(f"/api/pages/{page_id}/posture").json()["experiences"] == [saved]
