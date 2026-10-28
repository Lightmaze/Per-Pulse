"""Explicit user file imports reuse the native workspace without running AI.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All file contents and HTTP model choices used here are synthetic fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from fastapi.testclient import TestClient
import pytest
import httpx

from per_pulse.app import create_app
from pulse_system.product.files import MAX_FILE_BYTES
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.contracts import ActionStatus


def _client(path, **kwargs):
    kwargs.setdefault("model_factory", None)
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def _identity(client):
    bootstrap = client.get("/api/bootstrap").json()
    return {"X-Per-Pulse-World": bootstrap["workspaceId"],
            "X-Per-Pulse-Service-Instance": bootstrap["serviceInstanceId"]}


def _upload(client, data=b"An observed source.\n", name="source.md", headers=None):
    return client.post("/api/workspace/files", params={"name": name}, content=data,
                       headers={"Content-Type": "application/octet-stream", **(headers or _identity(client))})


def test_materials_are_native_create_only_files_and_survive_restart(tmp_path):
    with _client(tmp_path) as client:
        identity = _identity(client)
        runtime = client.app.state.pages.runtime
        sessions_before = runtime.kernel_service.store.sessions_for_world(runtime.world_id)
        data = "# Field notes\nAn encounter belongs in later work.\n".encode()
        result = _upload(client, data).json()
        assert result["source"] == "user_upload"
        assert result["sha256"] == hashlib.sha256(data).hexdigest()
        assert result["workspaceId"] == identity["X-Per-Pulse-World"]
        path = result["path"]
        assert (tmp_path / "workspace" / path).read_bytes() == data
        assert _upload(client, b"A separate revision.\n").json()["path"] != path
        assert (tmp_path / "workspace" / path).read_bytes() == data
        listing = client.get("/api/workspace/files", params={"path": "uploads"}, headers=identity).json()
        assert len(listing["entries"]) == 2
        assert listing["scope"] == "shared_native_workspace"
        preview = client.get("/api/workspace/files/content", params={"path": path}, headers=identity).json()
        assert preview["content"] == data.decode()
        assert preview["sha256"] == result["sha256"]
        assert runtime.kernel_service.store.sessions_for_world(runtime.world_id) == sessions_before
        assert not runtime.kernel_service._runtimes
        assert runtime.tick_count == 0
        assert client.get("/api/pages/home").json()["messages"] == []
        # Uploading is not a grant for model write tools.
        assert client.get("/api/bootstrap").json()["provider"]["workspaceActions"] is False

    with _client(tmp_path) as client:
        assert _identity(client)["X-Per-Pulse-World"] == identity["X-Per-Pulse-World"]
        assert client.get("/api/workspace/files/content", params={"path": path}, headers=identity).status_code == 409
        assert _upload(client, headers=identity).status_code == 409
        refreshed = client.get("/api/workspace/files/content", params={"path": path}, headers=_identity(client))
        assert refreshed.json()["content"] == data.decode()
        assert len(list((tmp_path / "workspace" / "uploads").iterdir())) == 2


@pytest.mark.parametrize("headers", [{}, {"X-Per-Pulse-World": "different"},
    {"X-Per-Pulse-World": "different", "X-Per-Pulse-Service-Instance": "different"}])
def test_file_access_requires_observed_workspace_identity(tmp_path, headers):
    with _client(tmp_path) as client:
        assert client.get("/api/workspace/files", headers=headers).status_code == 409
        response = client.post("/api/workspace/files?name=source.txt", content=b"A source.",
                               headers={"Content-Type": "application/octet-stream", **headers})
        assert response.status_code == 409
        assert not (tmp_path / "workspace" / "uploads").exists()


@pytest.mark.parametrize("name,data,code,status", [
    ("empty.txt", b"", "empty_file", 400),
    ("binary.txt", b"a\x00b", "text_files_only", 415),
    ("latin.txt", b"\xff\xfe", "text_files_only", 415),
    ("huge.txt", b"x" * (MAX_FILE_BYTES + 1), "file_too_large", 413),
    ("../outside.txt", b"outside", "protected_or_invalid_path", 400),
    ("folder/file.txt", b"nested", "filename_only", 400),
    ("world.db", b"protected", "protected_or_invalid_path", 400),
    ("credentials.txt", b"api_key=sk-fictionalexampleabcdef", "credential_content_blocked", 403),
])
def test_invalid_uploads_have_specific_errors_and_no_file_effect(tmp_path, name, data, code, status):
    with _client(tmp_path) as client:
        response = _upload(client, data, name)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert not (tmp_path / "workspace" / "uploads").exists()


def test_streamed_upload_limit_counts_bytes_and_accepts_exact_limit(tmp_path):
    with _client(tmp_path) as client:
        identity = _identity(client)
        response = _upload(client, (b"x" * 65536 for _ in range(17)))
        assert response.status_code == 413
        assert not (tmp_path / "workspace" / "uploads").exists()
        data = ("\u6e90" * (MAX_FILE_BYTES // 3)).encode() + b"x"
        assert len(data) == MAX_FILE_BYTES
        accepted = _upload(client, data)
        assert accepted.status_code == 201
        assert accepted.json()["bytes"] == MAX_FILE_BYTES
        read = client.get("/api/workspace/files/content", params={"path": accepted.json()["path"]}, headers=identity)
        assert read.json()["sha256"] == hashlib.sha256(data).hexdigest()


def test_file_routes_keep_loopback_origin_and_json_boundaries(tmp_path):
    with _client(tmp_path) as client:
        response = _upload(client, headers={**_identity(client), "Origin": "https://remote.invalid"})
        assert response.status_code == 403
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Cache-Control"] == "no-store"
        assert client.post("/api/workspace/files?name=source.txt", json={"content": "text"}, headers=_identity(client)).status_code == 415
        assert client.post("/api/pages/home/messages", content=b"text", headers={"Content-Type": "application/octet-stream"}).status_code == 415
        assert not (tmp_path / "workspace" / "uploads").exists()


def test_long_multibyte_file_name_keeps_its_extension_and_original_label(tmp_path):
    with _client(tmp_path) as client:
        # This valid 243-byte original name used to exceed the filesystem's
        # 255-byte component limit once the unique upload prefix was added.
        name = "\u6e90" * 80 + ".md"
        response = _upload(client, name=name)
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["name"] == name
        assert result["path"].endswith(".md")
        assert len(result["path"].split("/")[-1].encode("utf-8")) <= 173
        assert (tmp_path / "workspace" / result["path"]).read_bytes() == b"An observed source.\n"


def test_native_file_guards_apply_to_materials_browser(tmp_path):
    with _client(tmp_path) as client:
        identity = _identity(client)
        root = tmp_path / "workspace"
        outside = tmp_path / "private.txt"
        outside.write_text("This is outside the material workspace.")
        (root / "linked.txt").symlink_to(outside)
        os.link(outside, root / "hardlinked.txt")
        (root / "secrets.env").write_text("Protected.")
        (root / "nested").mkdir()
        (root / "nested" / "source.txt").write_text("A nested source.")
        listing = client.get("/api/workspace/files", headers=identity).json()
        assert "linked.txt" not in {entry["name"] for entry in listing["entries"]}
        assert "secrets.env" not in {entry["name"] for entry in listing["entries"]}
        for path in ("linked.txt", "hardlinked.txt", "secrets.env", "../private.txt", "/etc/passwd"):
            assert client.get("/api/workspace/files/content", params={"path": path}, headers=identity).status_code >= 400
        nested = client.get("/api/workspace/files/content", params={"path": "nested/source.txt"}, headers=identity)
        assert nested.json()["content"] == "A nested source."


@pytest.mark.parametrize("tool_name", ["read_file", "reading"])
def test_imported_source_is_read_by_existing_native_tools_in_separate_works(tmp_path, monkeypatch, tool_name):
    """Synthetic tool choices, real imported bytes and original action receipts."""
    monkeypatch.setenv("PER_PULSE_MATERIAL_TEST_KEY", "synthetic-test-credential")
    source_path = None
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]
        last_user = max(i for i, message in enumerate(messages) if message.get("role") == "user")
        if not any(message.get("role") == "tool" for message in messages[last_user:]):
            args = {"path": source_path}
            if tool_name == "reading":
                args["op"] = "open"
            delta = {"tool_calls": [{"index": 0, "id": "synthetic-material-read", "type": "function",
                                    "function": {"name": tool_name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        else:
            delta = {"content": "Synthetic fixture: the native read completed."}
            finish = "stop"
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "material-fixture",
            credential_env="PER_PULSE_MATERIAL_TEST_KEY"), transport=httpx.MockTransport(handler))

    with _client(tmp_path, model_factory=factory) as client:
        source = b"The real source bytes reach the native read tool.\n"
        uploaded = _upload(client, source).json()
        source_path = uploaded["path"]
        service = client.app.state.pages
        scopes = []
        for title, private in [("First reading", "PRIVATE_FIRST_NOTE"), ("Second reading", "PRIVATE_SECOND_NOTE")]:
            created = client.post("/api/pages", json={"title": title,
                "instruction": f"{private}: read workspace file {source_path}."}).json()
            page_id = created["page"]["id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                view = client.get(f"/api/pages/{page_id}").json()
                if any(message["role"] == "assistant" for message in view["messages"]):
                    break
                time.sleep(0.025)
            else:
                pytest.fail(f"Native read did not complete: {view}")
            scope = service.execution._scope(page_id)
            scopes.append(scope)
            actions = service.runtime.kernel_service.store.actions_for_session(scope)
            action, = [action for action in actions if action.tool_name == tool_name]
            assert action.status is ActionStatus.SUCCEEDED
            assert action.result_delivered
            assert uploaded["sha256"] in json.dumps(action.result)
            assert source.decode().strip() in json.dumps(action.result)
            assert view["artifacts"] == [], "A read must not masquerade as a generated file artifact"
        assert scopes[0].session_id != scopes[1].session_id
        for request in requests:
            payload = json.dumps(request["messages"])
            assert not ("PRIVATE_FIRST_NOTE" in payload and "PRIVATE_SECOND_NOTE" in payload)
        assert (tmp_path / "workspace" / source_path).read_bytes() == source
