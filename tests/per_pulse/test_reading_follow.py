"""Follow an exposed source match through native, version-bound reading.

Model choices are explicit fixtures. Files, receipts, context isolation and
continuation use the real World and Kernel.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
import os

import pytest

from pulse_system.service import workspace_reading
from pulse_system.product import image
from pulse_harness.core.errors import ProviderProtocolError
from test_materials import upload
from test_pages import client_for
from test_reading_continuity import ReadingFixture, read, work


def source(client, page, name, data):
    response = upload(client, page, name, data)
    assert response.status_code == 201, response.text
    return response.json()["material"]


def test_follow_distant_unicode_match_then_continue_and_reopen(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    prefix = "\ufeffOriginal source.\r\n" + "A distant 🌍 note about café.\r\n" * 3000
    selected = "NEEDLE second section: use the original source coordinates.\r\n" + "Context after the match.\n" * 30
    text = prefix + "NEEDLE first section.\n" + selected
    start = text.index("NEEDLE second")
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page = work(client)
        material = source(client, page, "long-source.txt", text.encode())
        found = read(client, page, fixture, op="find", path=material["path"], query="needle",
                     expected_digest=material["sha256"])["output"]
        assert len(found["matches"]) == 2
        assert found["matches"][1]["start_char"] == start
        following = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"],
                         match_index=1, max_chars=217)["output"]
        assert following["content"] == text[start:start + 217]
        assert following["position"] == {"start_char": start, "end_char": start + 217,
                                          "total_chars": len(text), "unit": "unicode_characters_in_redacted_view"}
        assert following["source"] == found["source"]
        assert following["followed_from"]["receipt_id"] == found["receipt_id"]
        assert following["followed_from"]["match_index"] == 1
        assert prefix not in json.dumps(fixture.requests, ensure_ascii=False)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        recent = read(client, page, fixture, op="recent")["output"]["passages"][0]
        assert recent["receipt_id"] == following["receipt_id"]
        assert recent["followed_from"] == following["followed_from"]
        continued = read(client, page, fixture, **following["next"], max_chars=149)["output"]
        assert continued["content"] == text[start + 217:start + 366]
        assert continued["source"] == found["source"]
        assert "followed_from" not in continued
    assert (tmp_path / "workspace" / material["path"]).read_bytes() == text.encode()


@pytest.mark.parametrize("changed_part", ["visible", "redacted", "view_format"])
def test_follow_rechecks_the_complete_source_before_exposing_text(tmp_path, monkeypatch, changed_part):
    fixture = ReadingFixture(monkeypatch.setenv)
    text = "password=synthetic-first-value\nA visible NEEDLE followed by original context.\n"
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page = work(client)
        # Pre-existing workspace files may need line redaction. The user upload
        # API separately refuses credential-shaped documents; do not weaken it.
        path = tmp_path / "workspace" / "source.txt"
        path.write_text(text)
        material = {"path": path.name}
        found = read(client, page, fixture, op="find", path=material["path"], query="NEEDLE")["output"]
        assert found["redacted"]
        if changed_part == "view_format":
            monkeypatch.setattr(workspace_reading, "VIEW", "explicit-follow-test.v2")
        else:
            changed = text.replace("original", "REPLACEMENT_PRIVATE") if changed_part == "visible" else text.replace("first", "other")
            (tmp_path / "workspace" / material["path"]).write_text(changed)
            if changed_part == "redacted":
                assert workspace_reading._view(changed) == workspace_reading._view(text)
        result = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"], match_index=0)
        assert result["status"] == "failed" and result["error_code"] == "reading_source_changed"
        assert "content" not in result.get("output", {})
        assert "REPLACEMENT_PRIVATE" not in json.dumps(fixture.requests)


def test_follow_uses_redacted_view_coordinates_and_rejects_unexposed_match(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    original = "password=synthetic-hidden-value\r\n🌍 Heading.\r\nNEEDLE first.\r\nNEEDLE second.\r\n"
    visible = "[REDACTED]\r\n🌍 Heading.\r\nNEEDLE first.\r\nNEEDLE second.\r\n"
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page = work(client)
        path = tmp_path / "workspace" / "redacted.txt"
        path.write_bytes(original.encode())
        material = {"path": path.name}
        found = read(client, page, fixture, op="find", path=material["path"], query="NEEDLE", max_matches=1)["output"]
        assert found["more_matches"]
        assert found["source"]["digest"] == hashlib.sha256(original.encode()).hexdigest()
        assert found["source"]["view_digest"] == hashlib.sha256(visible.encode()).hexdigest()
        missing = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"], match_index=1)
        assert missing["status"] == "failed" and missing["error_code"] == "scope_conflict"
        followed = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"], match_index=0)["output"]
        assert followed["content"] == "NEEDLE first.\r\nNEEDLE second.\r\n"
        assert followed["position"]["start_char"] == visible.index("NEEDLE")
        assert "synthetic-hidden-value" not in json.dumps(fixture.requests)


def test_follow_cannot_borrow_another_works_receipt_or_a_passage(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        first, other = work(client), work(client)
        material = source(client, first, "source.txt", b"A NEEDLE in the shared native workspace.")
        found = read(client, first, fixture, op="find", path=material["path"], query="needle")["output"]
        foreign = read(client, other, fixture, op="follow", receipt_id=found["receipt_id"], match_index=0)
        assert foreign["status"] == "failed" and foreign["error_code"] == "scope_conflict"
        passage = read(client, first, fixture, op="open", path=material["path"], max_chars=4)["output"]
        wrong_kind = read(client, first, fixture, op="follow", receipt_id=passage["receipt_id"], match_index=0)
        assert wrong_kind["status"] == "failed" and wrong_kind["error_code"] == "scope_conflict"
        # The shared file remains readable; only private receipt authority is
        # scoped to the original Work. This is not filesystem access isolation.
        own = read(client, other, fixture, op="find", path=material["path"], query="needle")["output"]
        followed = read(client, other, fixture, op="follow", receipt_id=own["receipt_id"], match_index=0)["output"]
        assert followed["content"] == "NEEDLE in the shared native workspace."


@pytest.mark.skipif(os.name != "posix", reason="native stopped Image requires POSIX")
def test_follow_requires_a_fresh_find_after_relocation_and_its_passage_can_resume(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    original, copied, archive = tmp_path / "source", tmp_path / "copy", tmp_path / "private.zip"
    text = "Introduction.\nNEEDLE: a long explanation that continues in the next passage.\n"
    with client_for(original, model_factory=fixture.factory) as client:
        page = work(client)
        material = source(client, page, "relocated.txt", text.encode())
        found = read(client, page, fixture, op="find", path=material["path"], query="needle")["output"]
        followed = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"],
                        match_index=0, max_chars=15)["output"]
    image.save(original, archive)
    image.restore(archive, copied)
    with client_for(copied, model_factory=fixture.factory) as client:
        refused = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"], match_index=0)
        assert refused["status"] == "failed" and refused["error_code"] == "reading_workspace_changed"
        resumed = read(client, page, fixture, op="resume", receipt_id=followed["receipt_id"], max_chars=19)["output"]
        start = followed["position"]["end_char"]
        assert resumed["content"] == text[start:start + 19]
        fresh = read(client, page, fixture, op="find", path=material["path"], query="needle",
                     expected_digest=material["sha256"])["output"]
        selected = read(client, page, fixture, op="follow", receipt_id=fresh["receipt_id"], match_index=0)["output"]
        assert selected["content"] == text[text.index("NEEDLE"):]
        assert selected["source"]["workspace_digest"] != followed["source"]["workspace_digest"]


@pytest.mark.parametrize("fault", ["not_delivered", "clipped"])
def test_follow_refuses_unexposed_or_clipped_receipts(tmp_path, monkeypatch, fault):
    fixture = ReadingFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page = work(client)
        material = source(client, page, "receipt.txt", b"A NEEDLE with real context.")
        found = read(client, page, fixture, op="find", path=material["path"], query="needle")["output"]
        store = client.app.state.pages.runtime.kernel_service.store
        get_action = store.get_action

        def incomplete(scope, identity):
            record = get_action(scope, identity)
            if identity != found["receipt_id"]:
                return record
            # Inject an observation fault at the native store reader boundary;
            # the durable source record and file remain unchanged.
            return replace(record, result_delivered=False) if fault == "not_delivered" else replace(
                record, result={**record.result, "_truncated": True})

        monkeypatch.setattr(store, "get_action", incomplete)
        refused = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"], match_index=0)
        assert refused["status"] == "failed" and refused["error_code"] == "scope_conflict"
        assert "content" not in refused.get("output", {})


@pytest.mark.parametrize("arguments", [
    {"match_index": -1}, {"match_index": 8}, {"match_index": True}, {"match_index": "0"},
    {"match_index": 0, "start_char": 100}, {"match_index": 0, "path": "invented.txt"},
    {"match_index": 0, "max_chars": 0}, {},
])
def test_follow_rejects_invented_positions_and_invalid_selection(arguments):
    with pytest.raises(ProviderProtocolError):
        workspace_reading.validate({"op": "follow", "receipt_id": "observed-find", **arguments})
