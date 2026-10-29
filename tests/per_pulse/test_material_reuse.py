"""One native file can be explicitly referenced by two independent Works.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All authored data are synthetic; no provider or fabricated read receipt is used.
"""
from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest

from test_pages import client_for, new_page
from test_materials import upload


def identity(client):
    bootstrap = client.get("/api/bootstrap").json()
    return {"X-Per-Pulse-World": bootstrap["workspaceId"],
            "X-Per-Pulse-Service-Instance": bootstrap["serviceInstanceId"]}


def reference(client, page_id, path, digest, *, headers=None):
    return client.post(f"/api/pages/{page_id}/materials/reference",
                       json={"path": path, "sha256": digest}, headers=headers or identity(client))


def test_browser_upload_rejects_old_service_identity_before_creating_bytes(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        old = identity(client)
    with client_for(tmp_path, model_factory=None) as client:
        content = b"Selected before the service restarted."
        headers = {**old, "Content-Type": "application/octet-stream",
                   "X-Per-Pulse-Upload-Id": uuid4().hex,
                   "X-Per-Pulse-File-SHA256": hashlib.sha256(content).hexdigest()}
        stale = client.post("/api/pages/home/materials?name=Source.txt", headers=headers, content=content)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "workspace_changed"
        assert not list((tmp_path / "workspace").glob("uploads/*"))
        current = identity(client)
        saved = client.post("/api/pages/home/materials?name=Source.txt", headers={**headers, **current}, content=content)
        assert saved.status_code == 201
        assert saved.json()["workspaceId"] == current["X-Per-Pulse-World"]
        assert saved.json()["serviceInstanceId"] == current["X-Per-Pulse-Service-Instance"]
        assert (tmp_path / "workspace" / saved.json()["material"]["path"]).read_bytes() == content


def test_explicit_reuse_keeps_one_file_and_independent_native_message_ownership(tmp_path):
    content = b"name,value\r\nA,001.00\r\n"
    with client_for(tmp_path, model_factory=None) as client:
        first = new_page(client, title="First reading", instruction="FIRST_PRIVATE_HISTORY")["page"]["id"]
        second = new_page(client, title="Second reading", instruction="SECOND_PRIVATE_HISTORY")["page"]["id"]
        original = upload(client, first, "Source.csv", content).json()["material"]
        response = reference(client, second, original["path"], original["sha256"])
        assert response.status_code == 201, response.text
        selected = response.json()["material"]
        assert selected["id"] != original["id"] and selected["pageId"] == second
        assert selected["source"] == "workspace_reference"
        assert selected["name"] == original["name"]
        assert selected["path"] == original["path"]
        assert selected["sha256"] == original["sha256"]
        assert len(list((tmp_path / "workspace/uploads").iterdir())) == 1
        assert len(client.get(f"/api/pages/{second}").json()["messages"]) == 1
        repeated = reference(client, second, original["path"], original["sha256"]).json()
        assert repeated["repeated"] is True and repeated["material"] == selected
        sent = client.post(f"/api/pages/{second}/messages", json={"content": "Read this version.", "materials": [selected["id"]]})
        assert sent.status_code == 202, sent.text
        messages = sent.json()["view"]["messages"]
        assert all("FIRST_PRIVATE_HISTORY" not in item["content"] for item in messages)
        assert messages[-1]["materials"] == [selected]
        assert "expected_digest=sha256" in messages[-1]["content"]
        stale = identity(client)
    with client_for(tmp_path, model_factory=None) as client:
        assert reference(client, second, original["path"], original["sha256"], headers=stale).status_code == 409
        preview = client.get(f"/api/pages/{second}/materials/{selected['id']}").json()
        assert preview["content"].encode() == content and preview["material"] == selected
        assert client.get(f"/api/pages/{first}/materials").json()["materials"] == [original]


def test_changed_selection_requires_an_explicit_new_version_without_exposing_it_as_old(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        original = upload(client, "home", "Source.txt", b"Original version").json()["material"]
        work = new_page(client)["page"]["id"]
        old = reference(client, work, original["path"], original["sha256"]).json()["material"]
        replacement = b"LATER_BYTES_NOT_THE_SELECTED_VERSION"
        (tmp_path / "workspace" / original["path"]).write_bytes(replacement)
        rejected = reference(client, work, original["path"], original["sha256"])
        assert rejected.status_code == 409 and "LATER_BYTES" not in rejected.text
        assert len(client.get(f"/api/pages/{work}/materials").json()["materials"]) == 1
        before = client.get(f"/api/pages/{work}").json()["messages"]
        stale_send = client.post(f"/api/pages/{work}/messages", json={"content": "Keep my draft.", "materials": [old["id"]]})
        assert stale_send.status_code == 409
        assert client.get(f"/api/pages/{work}").json()["messages"] == before
        current = reference(client, work, original["path"], hashlib.sha256(replacement).hexdigest())
        assert current.status_code == 201 and current.json()["material"]["id"] != old["id"]
        assert client.get(f"/api/pages/{work}/materials/{old['id']}").status_code == 409
        assert len(list((tmp_path / "workspace/uploads").iterdir())) == 1


@pytest.mark.parametrize("kind", ["traversal", "symlink", "hardlink", "credential"])
def test_reference_cannot_bypass_native_path_and_content_guards(tmp_path, kind):
    with client_for(tmp_path, model_factory=None) as client:
        root = tmp_path / "workspace"
        outside = tmp_path / "outside.txt"
        outside.write_text("outside")
        if kind == "traversal":
            path = "../outside.txt"
        elif kind == "symlink":
            (root / "linked.txt").symlink_to(outside)
            path = "linked.txt"
        elif kind == "hardlink":
            os.link(outside, root / "linked.txt")
            path = "linked.txt"
        else:
            (root / "source.txt").write_text("sk-" + "A" * 48)
            path = "source.txt"
        result = reference(client, "home", path, "0" * 64)
        assert result.status_code >= 400
        assert client.get("/api/pages/home/materials").json()["materials"] == []


def test_reference_publication_failure_does_not_create_an_in_memory_receipt_or_change_bytes(tmp_path, monkeypatch):
    with client_for(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        path = tmp_path / "workspace/source.md"
        path.write_text("An existing local source.\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with monkeypatch.context() as patch:
            def fail(*_args, **_kwargs):
                raise OSError("synthetic component publication failure")
            patch.setattr(pages.runtime.storage, "save_component_state", fail)
            response = reference(client, "home", "source.md", digest)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "material_reference_unconfirmed"
        assert pages.materials.listing("home")["materials"] == []
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert reference(client, "home", "source.md", digest).status_code == 201
