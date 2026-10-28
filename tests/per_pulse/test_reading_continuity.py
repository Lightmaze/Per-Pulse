"""Fresh reading at a carried position uses native receipts, not a bookmark DB.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Provider choices are explicit fixtures; files, Kernel actions and Images are real.
"""
from __future__ import annotations

import json
import os

import pytest

from material_fixture import MaterialFixture
from test_materials import upload
from test_pages import client_for, new_page, send, wait_for
from pulse_system.product import image
from pulse_system.service import workspace_reading


class ReadingFixture(MaterialFixture):
    """Select one explicitly supplied reading operation per native turn."""

    def __init__(self, setenv):
        super().__init__(setenv)
        self.outcomes = []

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        current = max(i for i, item in enumerate(messages) if item["role"] == "user")
        instruction = messages[current]["content"]
        if not instruction.startswith("EXPLICIT_READING_FIXTURE:"):
            return self.response("Ready. [Explicit synthetic reading fixture]")
        results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
        if not results:
            return self.response(tool="reading", arguments=json.loads(instruction.split(":", 1)[1]))
        self.outcomes.append(results[-1])
        return self.response("Recorded reading outcome. [Explicit synthetic reading fixture]")


def read(client, page_id, fixture, **arguments):
    before = len(fixture.outcomes)
    sent = send(client, page_id, "EXPLICIT_READING_FIXTURE:" + json.dumps(arguments))
    wait_for(client, page_id, lambda view: any(
        row["id"] == sent["messageId"] and row["status"] == "settled" for row in view["messages"]))
    assert len(fixture.outcomes) == before + 1
    return fixture.outcomes[-1]


def work(client):
    page_id = new_page(client, title="Continuing a source", instruction="Prepare a source. [Explicit fixture]")["page"]["id"]
    wait_for(client, page_id, lambda view: view["messages"][-1]["role"] == "assistant")
    return page_id


@pytest.mark.skipif(os.name != "posix", reason="native stopped Image requires POSIX")
def test_explicit_resume_after_cold_image_keeps_offset_and_old_provenance(tmp_path, monkeypatch):
    original, restored, archive = tmp_path / "original", tmp_path / "restored", tmp_path / "private.pulse"
    fixture = ReadingFixture(monkeypatch.setenv)
    text = "A source with café and 🌍.\r\n" * 60
    with client_for(original, model_factory=fixture.factory) as client:
        page_id = work(client)
        material = upload(client, page_id, "source.txt", text.encode()).json()["material"]
        first = read(client, page_id, fixture, op="open", path=material["path"],
                     expected_digest=material["sha256"], max_chars=37)["output"]
        assert first["content"] == text[:37]
        world = client.get("/api/bootstrap").json()["workspaceId"]
    image.save(original, archive)
    assert image.restore(archive, restored)["world_id"] == world

    with client_for(restored, model_factory=fixture.factory) as client:
        strict = read(client, page_id, fixture, op="continue", receipt_id=first["receipt_id"], max_chars=31)
        assert strict["status"] == "failed" and strict["error_code"] == "reading_workspace_changed"
        assert "content" not in strict.get("output", {})
        resumed = read(client, page_id, fixture, op="resume", receipt_id=first["receipt_id"], max_chars=31)["output"]
        assert resumed["content"] == text[37:68]
        assert resumed["position"] == {"start_char": 37, "end_char": 68, "total_chars": len(text),
                                       "unit": "unicode_characters_in_redacted_view"}
        assert resumed["source"]["digest"] == first["source"]["digest"]
        assert resumed["source"]["view_digest"] == first["source"]["view_digest"]
        assert resumed["source"]["workspace_digest"] != first["source"]["workspace_digest"]
        assert resumed["resumed_from"] == {
            "receipt_id": first["receipt_id"], "workspace_digest": first["source"]["workspace_digest"],
            "observed_at": first["observed_at"], "meaning": "new_read_of_verified_unchanged_source_at_saved_position",
        }
        assert resumed["receipt_id"] != first["receipt_id"]
        recent = read(client, page_id, fixture, op="recent")["output"]
        assert recent["passages"][0]["resumed_from"] == resumed["resumed_from"]
        assert not recent["current_files_checked"]

    # The new receipt is the ordinary continuation anchor after another restart.
    with client_for(restored, model_factory=fixture.factory) as client:
        continued = read(client, page_id, fixture, op="continue", receipt_id=resumed["receipt_id"], max_chars=29)["output"]
        assert continued["content"] == text[68:97]
        assert "resumed_from" not in continued
        assert continued["source"] == resumed["source"]
    assert (original / "workspace" / material["path"]).read_bytes() == text.encode()


@pytest.mark.parametrize("changed_part", ["visible_bytes", "hidden_bytes", "view_format"])
def test_resume_refuses_a_changed_file_or_view_even_when_some_text_matches(tmp_path, monkeypatch, changed_part):
    fixture = ReadingFixture(monkeypatch.setenv)
    text = "Visible introduction.\npassword=synthetic-first-value\nMore source content.\n"
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page_id = work(client)
        source = tmp_path / "workspace" / "private-source.txt"
        source.write_text(text)
        first = read(client, page_id, fixture, op="open", path=source.name, max_chars=12)["output"]
        assert first["redacted"]
        if changed_part == "view_format":
            monkeypatch.setattr(workspace_reading, "VIEW", "explicit-test-view.v2")
        else:
            changed = text.replace("Visible", "Revised") if changed_part == "visible_bytes" else text.replace("first", "other")
            source.write_text(changed)
            if changed_part == "hidden_bytes":
                assert workspace_reading._view(text) == workspace_reading._view(changed)
        result = read(client, page_id, fixture, op="resume", receipt_id=first["receipt_id"], max_chars=30)
        assert result["status"] == "failed" and result["error_code"] == "reading_source_changed"
        assert "content" not in result.get("output", {})


def test_resume_cannot_borrow_another_contexts_receipt_or_invent_a_position(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page_id = work(client)
        material = upload(client, page_id, "source.txt", b"One source, two different contexts.").json()["material"]
        first = read(client, page_id, fixture, op="open", path=material["path"], max_chars=5)["output"]
        other = work(client)
        foreign = read(client, other, fixture, op="resume", receipt_id=first["receipt_id"], max_chars=10)
        assert foreign["status"] == "failed" and foreign["error_code"] == "scope_conflict"
        assert "content" not in foreign.get("output", {})
        fabricated = read(client, page_id, fixture, op="resume", receipt_id="never-exposed", max_chars=10)
        assert fabricated["status"] == "failed"
        assert "content" not in fabricated.get("output", {})
        same_root = read(client, page_id, fixture, op="resume", receipt_id=first["receipt_id"], max_chars=10)["output"]
        assert same_root["content"] == "ource, two"
        assert same_root["source"] == first["source"]


def test_resume_keeps_safe_path_checks_after_the_original_read(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page_id = work(client)
        material = upload(client, page_id, "source.txt", b"Original safe source bytes.").json()["material"]
        first = read(client, page_id, fixture, op="open", path=material["path"], max_chars=5)["output"]
        path = tmp_path / "workspace" / material["path"]
        outside = tmp_path / "outside.txt"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        result = read(client, page_id, fixture, op="resume", receipt_id=first["receipt_id"], max_chars=10)
        assert result["status"] == "failed"
        assert "content" not in result.get("output", {})
