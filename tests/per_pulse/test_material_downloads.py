"""Original source downloads retain native upload ownership and exact bytes.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All material contents are synthetic. File and restart effects are real.
"""
import hashlib
from urllib.parse import quote

from test_materials import upload
from test_pages import client_for, new_page


def identity(client):
    current = client.get("/api/bootstrap").json()
    return {"X-Per-Pulse-World": current["workspaceId"],
            "X-Per-Pulse-Service-Instance": current["serviceInstanceId"]}


def download(client, page, material, headers=None):
    return client.get(f"/api/pages/{page}/materials/{material['id']}/download",
                      headers=identity(client) if headers is None else headers)


def test_original_download_exceeds_preview_and_survives_restart_without_a_model(tmp_path):
    content = ("\ufeffname,code\r\n" + "Café 文本,001\r\n" * 8000).encode()
    name = "Café 100% 文本.csv"
    with client_for(tmp_path, model_factory=None) as client:
        record = upload(client, "home", name, content).json()["material"]
        preview = client.get(f"/api/pages/home/materials/{record['id']}").json()
        assert preview["truncated"] and preview["downloadable"]
        before = client.get("/api/pages/home").json()["messages"]
        old_identity = identity(client)
        response = download(client, "home", record)
        assert response.status_code == 200, response.text
        assert response.content == content
        assert len(response.content) > preview["previewLimitBytes"]
        assert response.headers["Content-Type"] == "application/octet-stream"
        assert response.headers["Content-Length"] == str(len(content))
        assert response.headers["ETag"] == '"' + hashlib.sha256(content).hexdigest() + '"'
        assert "filename*=UTF-8''" + quote(name, safe="") in response.headers["Content-Disposition"]
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Per-Pulse-World"] == old_identity["X-Per-Pulse-World"]
        assert response.headers["X-Per-Pulse-Service-Instance"] == old_identity["X-Per-Pulse-Service-Instance"]
        assert client.get("/api/pages/home").json()["messages"] == before
        runtime = client.app.state.pages.runtime
        assert runtime.kernel_service.store.sessions_for_world(runtime.world_id) == ()

    with client_for(tmp_path, model_factory=None) as client:
        assert download(client, "home", record, old_identity).status_code == 409
        assert download(client, "home", record).content == content
        assert not client.app.state.pages.runtime.kernel_service._runtimes
        assert (tmp_path / "workspace" / record["path"]).read_bytes() == content


def test_original_download_refuses_wrong_owner_stale_identity_and_changed_source(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        work = new_page(client)["page"]["id"]
        record = upload(client, work, "source.txt", b"Original source bytes.\n").json()["material"]
        assert download(client, "home", record).status_code == 404
        assert download(client, work, record, {}).status_code == 409
        stale = {**identity(client), "X-Per-Pulse-World": "another-world"}
        assert download(client, work, record, stale).status_code == 409
        source = tmp_path / "workspace" / record["path"]
        source.write_text("REPLACEMENT_NOT_THE_SELECTED_SOURCE")
        response = download(client, work, record)
        assert response.status_code == 409 and response.json()["error"]["code"] == "material_changed"
        assert "REPLACEMENT_NOT_THE_SELECTED_SOURCE" not in response.text
        source.unlink()
        missing = download(client, work, record)
        assert missing.status_code == 409 and missing.json()["error"]["code"] == "material_unavailable"


def test_original_download_checks_hidden_content_beyond_the_preview(tmp_path):
    content = b"Ordinary source line.\n" * 4000 + b"api_key = SYNTHETIC_SECRET_MARKER_0123456789\n"
    with client_for(tmp_path, model_factory=None) as client:
        uploaded = upload(client, "home", "source-with-hidden-line.txt", content)
        assert uploaded.status_code == 201, uploaded.text
        record = uploaded.json()["material"]
        preview = client.get(f"/api/pages/home/materials/{record['id']}").json()
        assert preview["truncated"] and preview["redacted"]
        assert not preview["downloadable"]
        response = download(client, "home", record)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "material_download_redacted"
        assert "SYNTHETIC_SECRET_MARKER" not in response.text


def test_another_work_downloads_only_its_explicit_reference_receipt(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        original = upload(client, "home", "source.md", b"One shared native file.\n").json()["material"]
        work = new_page(client)["page"]["id"]
        response = client.post(f"/api/pages/{work}/materials/reference", headers=identity(client),
            json={"path": original["path"], "sha256": original["sha256"]})
        assert response.status_code == 201, response.text
        reference = response.json()["material"]
        assert reference["id"] != original["id"] and reference["pageId"] == work
        downloaded = download(client, work, reference)
        assert downloaded.status_code == 200 and downloaded.content == b"One shared native file.\n"
        assert "filename*=UTF-8''" + quote(reference["name"], safe="") in downloaded.headers["Content-Disposition"]
        assert download(client, work, original).status_code == 404
        assert len(list((tmp_path / "workspace" / "uploads").iterdir())) == 1


def test_literal_omission_marker_is_part_of_the_original_uploaded_bytes(tmp_path):
    content = b"A supplied [REDACTED] marker stays literal; omitted upstream text is unavailable.\r\n"
    with client_for(tmp_path, model_factory=None) as client:
        record = upload(client, "home", "limited-source.txt", content).json()["material"]
        preview = client.get(f"/api/pages/home/materials/{record['id']}").json()
        assert preview["redacted"] and preview["downloadable"]
        response = download(client, "home", record)
        assert response.status_code == 200 and response.content == content


def test_original_download_never_follows_a_replaced_source_symlink(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        record = upload(client, "home", "source.txt", b"original").json()["material"]
        outside = tmp_path / "outside.txt"
        outside.write_text("OUTSIDE_SOURCE_MUST_NOT_LEAK")
        source = tmp_path / "workspace" / record["path"]
        source.unlink()
        source.symlink_to(outside)
        response = download(client, "home", record)
        assert response.status_code == 409
        assert "OUTSIDE_SOURCE_MUST_NOT_LEAK" not in response.text
