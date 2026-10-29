"""Native literal find reads actual user materials without copying Work history.

Provider decisions in this file are synthetic; source bytes, action receipts,
session scope and SQLite persistence are genuine.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from material_fixture import MaterialFixture
from test_pages import client_for, new_page, wait_for
from pulse_harness.core.errors import ProviderProtocolError
from pulse_system.service.workspace_reading import _view, validate


class FindFixture(MaterialFixture):
    def __init__(self, setenv, *, query="report-alpha", case_sensitive=False, before_search=None):
        super().__init__(setenv)
        self.query = query
        self.case_sensitive = case_sensitive
        self.before_search = before_search
        self.observations = []

    def handler(self, request):
        messages = json.loads(request.content)["messages"]
        self.requests.append(messages)
        current = max(i for i, message in enumerate(messages) if message["role"] == "user")
        task = str(messages[current]["content"])
        if "FIND_RARE_LINE" not in task:
            return self.response("Ready for source work. [Explicit synthetic fixture]")
        source = json.loads(task.rsplit("[Attached workspace materials]\n", 1)[1].splitlines()[0])["files"][0]
        results = [json.loads(message["content"]) for message in messages[current + 1:] if message["role"] == "tool"]
        if not results:
            if self.before_search:
                self.before_search(source)
            return self.response(tool="reading", arguments={
                "op": "find", "path": source["path"], "expected_digest": source["sha256"],
                "query": self.query, "case_sensitive": self.case_sensitive, "max_matches": 2,
            })
        last = results[-1]
        if last["status"] != "succeeded":
            self.observations.append(last)
            return self.response("The selected source version was not available. [Synthetic fixture]")
        evidence = last["output"]
        self.observations.append(evidence)
        assert evidence["kind"] == "pulse.reading-find.v1"
        assert evidence["source"]["digest"] == source["sha256"]
        matches = evidence["matches"]
        if not matches:
            return self.response("No literal matching passage in that version. [Synthetic fixture]")
        first = matches[0]
        return self.response(
            f"Found {len(matches)} bounded source passages at character {first['start_char']}. "
            "[Explicit synthetic fixture]"
        )


def _upload(client, page_id, name, data):
    return client.post(f"/api/pages/{page_id}/materials", params={"name": name},
        content=data, headers={
            "Content-Type": "application/octet-stream",
            "X-Per-Pulse-Upload-Id": uuid4().hex,
            "X-Per-Pulse-World": client.app.state.pages.runtime.world_id,
            "X-Per-Pulse-File-SHA256": hashlib.sha256(data).hexdigest(),
        })


def test_find_distant_original_passages_without_full_file_read_or_other_work_history(tmp_path, monkeypatch):
    fixture = FindFixture(monkeypatch.setenv)
    prefix = "An unrelated note.\n" * 3000
    matching = "Consolidated REPORT-ALPHA quantity: 42 units.\n"
    body = prefix + matching + matching + matching + "Closing note.\n"
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        origin = new_page(client)["page"]["id"]
        other = new_page(client)["page"]["id"]
        material = _upload(client, origin, "Inventory notes.txt", body.encode()).json()["material"]
        sent = client.post(f"/api/pages/{origin}/messages", json={
            "content": "FIND_RARE_LINE. Locate the quantity, with original source evidence.",
            "materials": [material["id"]],
        })
        assert sent.status_code == 202, sent.text
        view = wait_for(client, origin, lambda item: item["messages"][-1]["role"] == "assistant"
                        and "bounded source passages" in item["messages"][-1]["content"])
        assert "character " in view["messages"][-1]["content"]
        assert len(fixture.observations) == 1
        observation = fixture.observations[0]
        assert observation["source"]["digest"] == material["sha256"]
        assert len(observation["matches"]) == 2 and observation["more_matches"]
        assert observation["matches"][0]["start_char"] == len(prefix) + len("Consolidated ")
        assert observation["matches"][0]["excerpt"].count("REPORT-ALPHA") >= 1
        assert observation["matches"][0]["excerpt_start_char"] > 1000
        assert len(observation["matches"][0]["excerpt"]) < 320
        assert observation["observation_only"]
        assert not observation["searched_entire_redacted_view"]
        assert "REPORT-ALPHA" not in json.dumps(client.get(f"/api/pages/{other}").json()["messages"])
        assert (tmp_path / "workspace" / material["path"]).read_bytes() == body.encode()


def test_find_respects_case_flag_and_refuses_changed_source_bytes(tmp_path, monkeypatch):
    fixture = FindFixture(monkeypatch.setenv, query="REPORT-ALPHA", case_sensitive=True)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        work = new_page(client)["page"]["id"]
        material = _upload(client, work, "notes.txt", b"report-alpha lower only.\n").json()["material"]
        assert client.post(f"/api/pages/{work}/messages", json={
            "content": "FIND_RARE_LINE", "materials": [material["id"]]}).status_code == 202
        view = wait_for(client, work, lambda v: v["messages"][-1]["role"] == "assistant"
                        and "No literal matching" in v["messages"][-1]["content"])
        assert fixture.observations[-1]["matches"] == []
        assert not fixture.observations[-1]["more_matches"]
        assert fixture.observations[-1]["searched_entire_redacted_view"]
    changed = FindFixture(monkeypatch.setenv, before_search=lambda source:
        (tmp_path / "workspace" / source["path"]).write_text("CHANGED_PRIVATE_CONTENT"))
    with client_for(tmp_path, model_factory=changed.factory) as client:
        work = new_page(client)["page"]["id"]
        material = _upload(client, work, "second.txt", b"REPORT-ALPHA source.\n").json()["material"]
        assert client.post(f"/api/pages/{work}/messages", json={
            "content": "FIND_RARE_LINE", "materials": [material["id"]]}).status_code == 202
        view = wait_for(client, work, lambda v: v["messages"][-1]["role"] == "assistant"
                        and "not available" in v["messages"][-1]["content"])
        assert changed.observations[-1]["status"] != "succeeded"
        assert "CHANGED_PRIVATE_CONTENT" not in json.dumps(changed.requests)


def test_find_redacts_before_search_and_uses_redacted_view_coordinates(tmp_path, monkeypatch):
    body = ("PUBLIC report-alpha before.\n"
            "api_key = SYNTHETIC_SECRET_MARKER_0123456789\n"
            "PUBLIC report-alpha after.\n")
    redacted, hidden = _view(body)
    assert hidden
    fixture = FindFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        work = new_page(client)["page"]["id"]
        uploaded = _upload(client, work, "redacted-notes.txt", body.encode())
        assert uploaded.status_code == 201, uploaded.text
        material = uploaded.json()["material"]
        assert client.post(f"/api/pages/{work}/messages", json={
            "content": "FIND_RARE_LINE", "materials": [material["id"]]}).status_code == 202
        wait_for(client, work, lambda item: item["messages"][-1]["role"] == "assistant"
                 and "bounded source passages" in item["messages"][-1]["content"])
        observation = fixture.observations[-1]
        assert observation["redacted"] and observation["searched_entire_redacted_view"]
        assert not observation["more_matches"]
        assert observation["source"]["view_digest"] == hashlib.sha256(redacted.encode()).hexdigest()
        assert [item["start_char"] for item in observation["matches"]] == [
            redacted.index("report-alpha"), redacted.rindex("report-alpha")]
        assert "SYNTHETIC_SECRET_MARKER" not in json.dumps(fixture.requests)
        assert (tmp_path / "workspace" / material["path"]).read_text() == body
        before_restart = client.get(f"/api/pages/{work}").json()["messages"]

    # The already-hidden marker crosses action results, event envelopes and
    # tool transcript rows. All must remain readable after cold reopening.
    with client_for(tmp_path, model_factory=None) as client:
        reopened = client.get(f"/api/pages/{work}")
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["messages"] == before_restart


@pytest.mark.parametrize("text", [
    "An earlier source said [REDACTED]; preserve that visible omission.",
    "[TRUNCATED sha256=" + "a" * 64 + " redacted=0 preview=An explicitly shortened source.]",
    "[TRUNCATED sha256=" + "b" * 64 + " redacted=1 preview=An earlier hidden source.]",
])
def test_existing_visibility_markers_survive_native_execution_and_reopen(tmp_path, monkeypatch, text):
    fixture = FindFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        work = new_page(client)["page"]["id"]
        wait_for(client, work, lambda view: view["messages"][-1]["role"] == "assistant")
        sent = client.post(f"/api/pages/{work}/messages", json={"content": text})
        assert sent.status_code == 202, sent.text
        message_id = sent.json()["messageId"]
        current = wait_for(client, work, lambda view: any(
            item["id"] == message_id and item["status"] == "settled" for item in view["messages"]))
        assert next(item["content"] for item in current["messages"] if item["id"] == message_id) == text
        assert text in json.dumps(fixture.requests)
    with client_for(tmp_path, model_factory=None) as client:
        reopened = client.get(f"/api/pages/{work}")
        assert reopened.status_code == 200, reopened.text
        assert next(item["content"] for item in reopened.json()["messages"] if item["id"] == message_id) == text


@pytest.mark.parametrize("query,options", [
    ("", {}), ("x", {}), ("X" * 121, {}), ("two\nlines", {}),
    ("good", {"case_sensitive": "false"}), ("good", {"max_matches": 0}),
    ("good", {"max_matches": True}), ("good", {"max_chars": 12}),
])
def test_find_rejects_invalid_arguments(query, options):
    with pytest.raises(ProviderProtocolError):
        validate({"op": "find", "path": "notes.txt", "query": query, **options})
