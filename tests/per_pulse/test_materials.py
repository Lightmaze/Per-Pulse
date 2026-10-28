"""Source uploads use actual workspace bytes and native page inputs.

Provider decisions are explicit fixtures, never a substitute for a live model.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from per_pulse.materials import material_footer
from pulse_system.product.files import MAX_FILE_BYTES
from test_pages import client_for, new_page, wait_for
from material_fixture import MaterialFixture


def upload(client, page_id, name, content, *, identity=None, world=None, **headers):
    return client.post(f"/api/pages/{page_id}/materials", params={"name": name}, content=content,
        headers={"Content-Type": "application/octet-stream", "X-Per-Pulse-Upload-Id": identity or uuid4().hex,
                 "X-Per-Pulse-World": world or client.app.state.pages.runtime.world_id,
                 "X-Per-Pulse-File-SHA256": hashlib.sha256(content).hexdigest(), **headers})


def test_upload_keeps_exact_bytes_and_does_not_submit_a_message_or_start_a_session(tmp_path):
    content = "\ufeffname,value\r\nCafé,2\r\n文本,3\r\n".encode()
    with client_for(tmp_path, model_factory=None) as client:
        world_id = client.app.state.pages.runtime.world_id
        reply = upload(client, "home", "Café 文本.csv", content)
        assert reply.status_code == 201, reply.text
        material = reply.json()["material"]
        assert material["source"] == "user_upload" and material["pageId"] == "home"
        assert (tmp_path / "workspace" / material["path"]).read_bytes() == content
        assert material["sha256"] == hashlib.sha256(content).hexdigest()
        assert not client.get("/api/pages/home").json()["messages"]
        assert client.app.state.pages.runtime.kernel_service.store.sessions_for_world(world_id) == ()
        preview = client.get(f"/api/pages/home/materials/{material['id']}").json()
        assert preview["content"].encode() == content
        assert not preview["truncated"] and not preview["redacted"]
        assert client.get("/api/pages/home/materials").json()["materials"] == [material]

    with client_for(tmp_path, model_factory=None) as client:
        assert client.app.state.pages.runtime.world_id == world_id
        assert client.get("/api/pages/home/materials").json()["materials"] == [material]
        assert client.get(f"/api/pages/home/materials/{material['id']}").json()["content"].encode() == content
        assert not client.app.state.pages.runtime.kernel_service._runtimes


def test_upload_identity_is_repeated_only_for_the_same_page_and_original_bytes(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client)["page"]["id"]
        identity = uuid4().hex
        first = upload(client, work, "source.txt", b"original\n", identity=identity)
        assert first.status_code == 201
        record = first.json()["material"]
        repeated = upload(client, work, "source.txt", b"original\n", identity=identity)
        assert repeated.json() == {**first.json(), "repeated": True}
        for page, name, content in (("home", "source.txt", b"original\n"),
                                    (work, "renamed.txt", b"original\n"),
                                    (work, "source.txt", b"replacement\n")):
            assert upload(client, page, name, content, identity=identity).status_code == 409
        assert len(list((tmp_path / "workspace" / "uploads").iterdir())) == 1
        assert client.get(f"/api/pages/home/materials/{identity}").status_code == 404
        before = len(client.get("/api/pages/home").json()["messages"])
        denied = client.post("/api/pages/home/messages", json={"content": "Read another page's file.", "materials": [identity]})
        assert denied.status_code == 404
        assert len(client.get("/api/pages/home").json()["messages"]) == before


def test_attached_message_retains_native_references_and_unicode_body_without_copying_content(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client)["page"]["id"]
        record = upload(client, work, "Original notes.md", b"PRIVATE_FILE_CONTENT\n").json()["material"]
        text = "Inspect this material. 🌍"
        response = client.post(f"/api/pages/{work}/messages", json={"content": text, "materials": [record["id"]]})
        assert response.status_code == 202
        message = response.json()["view"]["messages"][-1]
        assert message["materials"] == [record]
        assert message["content"] == text + material_footer([record])
        assert message["content"][:-message["materialSuffixLength"]] == text
        assert "PRIVATE_FILE_CONTENT" not in message["content"]
        native = client.app.state.pages.runtime.causal_ledger.get_event(message["id"])
        assert native.content == message["content"]
        assert record["sha256"] in native.content and record["path"] in native.content
        only_file = client.post(f"/api/pages/{work}/messages", json={"content": "", "materials": [record["id"]]})
        assert only_file.status_code == 202
        assert only_file.json()["view"]["messages"][-1]["materials"] == [record]
        assert client.post(f"/api/pages/{work}/messages", json={"content": "", "materials": []}).status_code == 422
        assert client.post(f"/api/pages/{work}/messages", json={"content": "No duplicates", "materials": [record["id"], record["id"]]}).status_code == 422
        brought = client.post(f"/api/pages/{work}/return", json={"messageId": message["id"], "content": text, "includeMaterials": True}).json()
        transferred = next(item for item in brought["view"]["messages"] if item["id"] == brought["messageId"])
        assert transferred["materials"] == [record]
        assert transferred["content"][:-transferred["materialSuffixLength"]] == text
        text_only = client.post(f"/api/pages/{work}/return", json={"messageId": message["id"], "content": "Just this explanation."}).json()
        assert text_only["view"]["messages"][-1]["materials"] == []
        files_only = client.post(f"/api/pages/{work}/return", json={"messageId": message["id"], "content": "", "includeMaterials": True})
        assert files_only.status_code == 201
        assert files_only.json()["view"]["messages"][-1]["materials"] == [record]
        assert client.post("/api/pages/home/messages", json={"content": "No implicit ownership transfer", "materials": [record["id"]]}).status_code == 404
    with client_for(tmp_path, model_factory=None) as client:
        restored = client.get(f"/api/pages/{work}").json()["messages"]
        assert next(item for item in restored if item["id"] == message["id"]) == message


def test_changed_source_is_not_exposed_attached_or_overwritten_by_a_repeated_upload(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        record = upload(client, "home", "notes.txt", b"original\n").json()["material"]
        path = tmp_path / "workspace" / record["path"]
        path.write_text("REPLACEMENT_MUST_NOT_LEAK")
        preview = client.get(f"/api/pages/home/materials/{record['id']}")
        assert preview.status_code == 409 and preview.json()["error"]["code"] == "material_changed"
        assert "REPLACEMENT_MUST_NOT_LEAK" not in preview.text
        assert upload(client, "home", "notes.txt", b"original\n", identity=record["id"]).status_code == 409
        sent = client.post("/api/pages/home/messages", json={"content": "Use this version.", "materials": [record["id"]]})
        assert sent.status_code == 409
        assert not client.get("/api/pages/home").json()["messages"]
        assert path.read_text() == "REPLACEMENT_MUST_NOT_LEAK"
        path.unlink()
        assert client.get(f"/api/pages/home/materials/{record['id']}").json()["error"]["code"] == "material_unavailable"


@pytest.mark.parametrize("name,content,status", [
    ("../outside.txt", b"hello", 400), (".env", b"hello", 400), ("secret.key", b"hello", 400),
    ("binary.txt", b"hello\x00", 415), ("binary.txt", b"\xff\xfe", 415),
    ("large.txt", b"x" * (MAX_FILE_BYTES + 1), 413),
])
def test_invalid_material_creates_no_file_record_or_message(tmp_path, name, content, status):
    with client_for(tmp_path, model_factory=None) as client:
        response = upload(client, "home", name, content)
        assert response.status_code == status, response.text
        assert client.get("/api/pages/home/materials").json()["materials"] == []
        assert not client.get("/api/pages/home").json()["messages"]
        assert not (tmp_path / "workspace" / "uploads").exists()


def test_raw_upload_retains_existing_browser_boundary_and_checks_world_and_digest(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        assert upload(client, "home", "source.txt", b"hello", Origin="https://elsewhere.invalid").status_code == 403
        assert upload(client, "home", "source.txt", b"hello", world="a-different-world").status_code == 409
        assert upload(client, "home", "source.txt", b"hello", **{"X-Per-Pulse-File-SHA256": "0" * 64}).status_code == 422
        assert client.post("/api/pages/home/materials?name=file.txt", json={"content": "wrong encoding"}).status_code == 415
        assert client.post("/api/pages/home/messages", content=b"raw bytes", headers={"Content-Type": "application/octet-stream"}).status_code == 415
        assert upload(client, "missing", "source.txt", b"hello").status_code == 404
        assert not (tmp_path / "workspace" / "uploads").exists()


def test_large_multibyte_filename_and_full_size_file_remain_exact(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        filename = "文" * 80 + ".txt"
        content = b"x" * (MAX_FILE_BYTES - 7) + "café\r\n".encode()
        record = upload(client, "home", filename, content).json()["material"]
        assert len(Path(record["path"]).name.encode()) <= 255
        assert record["name"] == filename
        assert (tmp_path / "workspace" / record["path"]).read_bytes() == content
        preview = client.get(f"/api/pages/home/materials/{record['id']}").json()
        assert preview["truncated"]
        assert len(preview["content"].encode()) <= preview["previewLimitBytes"]


def test_actual_native_reading_continues_passages_then_writes_a_downloadable_report(tmp_path, monkeypatch):
    fixture = MaterialFixture(monkeypatch.setenv)
    content = ("\ufeffname,quantity,unit_price\r\n" + "Café supplies,3,2.50\r\n" * 300).encode()
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        record = upload(client, "home", "Inventory.csv", content).json()["material"]
        assert fixture.requests == []
        sent = client.post("/api/pages/home/messages", json={"content": "PROCESS_ATTACHED_CSV", "materials": [record["id"]]})
        assert sent.status_code == 202
        pending = wait_for(client, "home", lambda v: any(a["canResolve"] for a in v["execution"]["pendingActions"]))
        action = next(a for a in pending["execution"]["pendingActions"] if a["canResolve"])
        assert action["toolName"] == "mutate_file"
        assert fixture.reads[0]["passages"] > 1
        assert fixture.reads[0]["content"].encode() == content[3:]
        assert not (tmp_path / "workspace" / fixture.reads[0]["path"]).exists()
        approved = client.post(f"/api/pages/home/execution/actions/{action['actionId']}", json={"decision": "allow_once"})
        assert approved.status_code == 200
        view = wait_for(client, "home", lambda v: v["messages"][-1]["role"] == "assistant")
        assert "2250.00" in view["messages"][-1]["content"]
        artifact, = view["artifacts"]
        downloaded = client.get(f"/api/pages/home/artifacts/{artifact['id']}/download")
        assert downloaded.status_code == 200
        assert downloaded.content == fixture.reads[0]["report"].encode()
        requests_before = len(fixture.requests)
        current = client.get("/api/bootstrap").json()
        original = client.get(f"/api/pages/home/materials/{record['id']}/download", headers={
            "X-Per-Pulse-World": current["workspaceId"],
            "X-Per-Pulse-Service-Instance": current["serviceInstanceId"],
        })
        assert original.status_code == 200 and original.content == content
        assert len(fixture.requests) == requests_before
        assert (tmp_path / "workspace" / record["path"]).read_bytes() == content


def test_native_reader_refuses_a_file_replaced_after_message_admission(tmp_path, monkeypatch):
    def replace(source):
        (tmp_path / "workspace" / source["path"]).write_text("WRONG_VERSION_PRIVATE")

    fixture = MaterialFixture(monkeypatch.setenv, before_read=replace)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        record = upload(client, "home", "Inventory.csv", b"name,quantity,unit_price\nPencils,2,1.25\n").json()["material"]
        sent = client.post("/api/pages/home/messages", json={"content": "PROCESS_ATTACHED_CSV", "materials": [record["id"]]})
        assert sent.status_code == 202
        view = wait_for(client, "home", lambda v: v["messages"][-1]["role"] == "assistant")
        assert "changed before it could be read" in view["messages"][-1]["content"]
        assert not view["artifacts"] and not view["execution"]["pendingActions"]
        assert not fixture.reads
        assert "WRONG_VERSION_PRIVATE" not in json.dumps(fixture.requests)


def test_symlinked_upload_directory_cannot_escape_the_native_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with client_for(tmp_path / "world", model_factory=None) as client:
        (tmp_path / "world" / "workspace" / "uploads").symlink_to(outside, target_is_directory=True)
        reply = upload(client, "home", "file.txt", b"material")
        assert reply.status_code == 404
        assert list(outside.iterdir()) == []
        assert not client.get("/api/pages/home/materials").json()["materials"]


def test_failed_receipt_publication_does_not_invent_confirmation_or_retry_file_effect(tmp_path, monkeypatch):
    with client_for(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages
        save = pages.runtime.storage.save_component_state

        def fail_receipt(key, value):
            if key == pages._state_key and value.get("materials"):
                raise OSError("simulated receipt storage failure")
            return save(key, value)

        monkeypatch.setattr(pages.runtime.storage, "save_component_state", fail_receipt)
        result = upload(client, "home", "Unconfirmed.txt", b"The physical file may exist.\n")
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "material_upload_unconfirmed"
        assert client.get("/api/pages/home/materials").json()["materials"] == []
        files = list((tmp_path / "workspace" / "uploads").iterdir())
        assert len(files) == 1 and files[0].read_bytes() == b"The physical file may exist.\n"
        assert not client.get("/api/pages/home").json()["messages"]


def test_upload_respects_existing_unconfirmed_writer_cleanup(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        writer = client.app.state.pages.materials.writer
        writer.quarantine("actual-writer-cleanup-not-confirmed")
        try:
            response = upload(client, "home", "Wait.txt", b"No competing upload.\n")
            assert response.status_code != 201
            assert response.json()["error"]["code"] == "workspace_resource_unavailable"
            assert not (tmp_path / "workspace" / "uploads").exists()
            assert client.get("/api/pages/home/materials").json()["materials"] == []
        finally:
            writer.confirm_cleanup("actual-writer-cleanup-not-confirmed")
        assert upload(client, "home", "Wait.txt", b"Now the lane is available.\n").status_code == 201


def test_materials_survive_native_succession_and_stopped_image_restore(tmp_path, monkeypatch):
    from per_pulse.service import PageService
    from pulse_system.product import image

    directory = tmp_path / "original"
    fixture = MaterialFixture(monkeypatch.setenv)
    pages = PageService(directory, model_factory=fixture.factory)
    content = "\ufeffOriginal material, café\r\n".encode()
    try:
        created = pages.create_page("Continuing file", "Hold this supplied material.")
        page_id = created["page"]["id"]
        runtime = pages.runtime
        record = pages.materials.upload(page_id, "Original.csv", content, upload_id=uuid4().hex,
            world_id=runtime.world_id, expected_digest=hashlib.sha256(content).hexdigest())["material"]
        sent = pages.send(page_id, "Retain this source.", materials=[record["id"]])
        source_id = sent["messageId"]
        context = pages.learning._context(page_id)
        result = runtime._mgr.pulse(runtime.continuity_engram_id,
            pulse_event=runtime.causal_ledger.get_event(source_id), execution_context=context,
            runtime_fence=runtime._current_runtime_fence())
        before = next(row for row in pages.view(page_id)["messages"] if row["id"] == source_id)
        returned_id = pages.return_home(page_id, "Keep the original source.", source_id,
                                       include_materials=True)["messageId"]
        prepared = runtime._mgr.prepare_succession(runtime.continuity_engram_id,
            parent_event_id=result.result_event_id, runtime_fence=runtime._current_runtime_fence())
        successor = runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence()).new_id
        assert pages.materials.read(page_id, record["id"])["content"].encode() == content
    finally:
        assert pages.runtime.close().clean
    archive, restored = tmp_path / "private.pulse", tmp_path / "restored"
    image.save(directory, archive)
    assert image.restore(archive, restored)["model_calls"] == 0
    pages = PageService(restored, model_factory=None)
    try:
        assert pages.runtime.continuity_engram_id == successor
        assert pages.materials.listing(page_id)["materials"] == [record]
        assert pages.materials.read(page_id, record["id"])["content"].encode() == content
        assert (restored / "workspace" / record["path"]).read_bytes() == content
        assert next(row for row in pages.view(page_id)["messages"] if row["id"] == source_id) == before
        assert next(row for row in pages.view("home")["messages"] if row["id"] == returned_id)["materials"] == [record]
        assert not pages.runtime.kernel_service._runtimes
    finally:
        assert pages.runtime.close().clean
