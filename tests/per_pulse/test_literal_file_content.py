"""Literal file text is not permission or provenance metadata.

Native World/Kernel, files, receipts and restart are real. Model HTTP is an
explicit synthetic transport; these checks never contact an external service.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
import per_pulse.artifacts as artifact_module
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.replay import stored_payload_flags, stored_text_flags


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LiteralFileContentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="per-pulse-literal-file-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plans = {}
        self.requests = []
        credential = patch.dict(os.environ, {"PER_PULSE_LITERAL_FILE_KEY": "synthetic-test-credential"})
        credential.start()
        self.addCleanup(credential.stop)

    def model(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        boundary = max(i for i, row in enumerate(messages) if row["role"] == "user")
        current = messages[boundary:]
        prompt = json.dumps(current)
        arguments = next((value for key, value in self.plans.items() if key in prompt), None)
        if arguments is not None and not any(row["role"] == "tool" for row in current):
            delta = {"tool_calls": [{"index": 0, "id": "synthetic-literal-file-write", "type": "function",
                "function": {"name": "mutate_file", "arguments": json.dumps(arguments)}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"content": "Synthetic file update completed."}, "stop"
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(row) + "\n\n" for row in chunks) + "data: [DONE]\n\n")

    def factory(self, _scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions",
            "synthetic-literal-file-model", credential_env="PER_PULSE_LITERAL_FILE_KEY"),
            transport=httpx.MockTransport(self.model))

    def client(self, *, online=True):
        return TestClient(create_app(self.root, model_factory=self.factory if online else None,
            allow_workspace_actions=True, auto_apply_edits=True), base_url="http://localhost")

    def record(self, client, before: str, name="literal.txt"):
        before_bytes = before.encode("utf-8")
        (self.root / "workspace" / name).write_bytes(before_bytes)
        marker = "fixture-" + uuid4().hex
        self.plans[marker] = {"path": name, "expected_digest": sha(before_bytes),
                             "patch": {"edits": [{"old": "DRAFT", "new": "FINAL"}]}}
        response = client.post("/api/pages", json={"title": "Literal file", "instruction": marker})
        self.assertEqual(response.status_code, 201, response.text)
        page = response.json()["page"]["id"]
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            view = client.get("/api/pages/" + page).json()
            if view.get("artifacts") and view["execution"]["status"] == "idle":
                break
            time.sleep(.025)
        else:
            self.fail("Native file write did not settle: " + json.dumps(view))
        self.assertEqual(len(view["artifacts"]), 1)
        item = view["artifacts"][0]
        after = before.replace("DRAFT", "FINAL", 1).encode("utf-8")
        self.assertEqual(item["sha256"], sha(after))
        self.assertEqual((self.root / "workspace" / name).read_bytes(), after)
        return page, item, after

    def assert_download(self, client, page, item, expected):
        response = client.get(f"/api/pages/{page}/artifacts/{item['id']}/download")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, expected)
        self.assertEqual(response.headers["ETag"], '"' + sha(expected) + '"')
        self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
        self.assertEqual(response.headers["Content-Length"], str(len(expected)))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))

    def test_literal_marker_download_survives_restart_and_undo(self):
        before = "DRAFT\r\nA literal [REDACTED] example.\r\n"
        with self.client() as client:
            page, item, after = self.record(client, before)
            preview = client.get(f"/api/pages/{page}/artifacts/{item['id']}").json()
            self.assertFalse(preview["redacted"])
            self.assertFalse(preview["truncated"])
            self.assertTrue(preview["downloadable"])
            self.assert_download(client, page, item, after)
        calls = len(self.requests)
        with self.client(online=False) as client:
            self.assert_download(client, page, item, after)
            undone = client.post(f"/api/pages/{page}/artifacts/{item['id']}/undo", json={})
            self.assertEqual(undone.status_code, 200, undone.text)
            self.assertEqual((self.root / "workspace" / item["path"]).read_bytes(), before.encode())
            self.assertEqual(client.get(f"/api/pages/{page}/artifacts/{item['id']}/download").status_code, 409)
        self.assertEqual(len(self.requests), calls)

    def test_marker_after_preview_keeps_full_bom_crlf_unicode_bytes(self):
        before = "\ufeffDRAFT\r\n" + "Café 文本 retain  two spaces.\r\n" * 100 + "[REDACTED]\r\n"
        with patch.object(artifact_module, "MAX_PREVIEW_BYTES", 256), self.client() as client:
            page, item, after = self.record(client, before)
            preview = client.get(f"/api/pages/{page}/artifacts/{item['id']}").json()
            self.assertTrue(preview["truncated"])
            self.assertNotIn("[REDACTED]", preview["content"])
            self.assertFalse(preview["redacted"])
            self.assertTrue(preview["downloadable"])
            self.assert_download(client, page, item, after)

    def test_truncation_shaped_file_text_is_not_a_truncated_receipt(self):
        with self.client() as client:
            for redacted in (0, 1):
                with self.subTest(redacted=redacted):
                    before = f"[TRUNCATED sha256={'0' * 64} redacted={redacted} preview=DRAFT]\n"
                    page, item, after = self.record(client, before, f"truncation-{redacted}.txt")
                    preview = client.get(f"/api/pages/{page}/artifacts/{item['id']}").json()
                    self.assertFalse(preview["redacted"])
                    self.assertFalse(preview["truncated"])
                    self.assertTrue(preview["downloadable"])
                    self.assert_download(client, page, item, after)

    def test_wrong_page_and_changed_version_still_do_not_disclose_bytes(self):
        with self.client() as client:
            page, item, _ = self.record(client, "DRAFT\n[REDACTED]\n")
            self.assertEqual(client.get(f"/api/pages/home/artifacts/{item['id']}/download").status_code, 404)
            replacement = b"LATER_WRITER_MUST_NOT_BE_DISCLOSED\n"
            (self.root / "workspace" / item["path"]).write_bytes(replacement)
            response = client.get(f"/api/pages/{page}/artifacts/{item['id']}/download")
            self.assertEqual(response.status_code, 409)
            self.assertNotIn(replacement.decode().strip(), response.text)

    def test_unsafe_or_unreadable_replacements_still_fail_closed(self):
        with self.client() as client:
            for kind in ("symlink", "hardlink", "binary", "oversized"):
                with self.subTest(kind=kind):
                    page, item, _ = self.record(client, "DRAFT\n[REDACTED]\n", kind + ".txt")
                    target = self.root / "workspace" / item["path"]
                    target.unlink()
                    outside = self.root / (kind + "-outside.txt")
                    outside.write_text("OUTSIDE_CONTENT_MUST_NOT_LEAK", encoding="utf-8")
                    if kind == "symlink":
                        try:
                            target.symlink_to(outside)
                        except OSError as error:
                            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                                self.skipTest("Windows symlink creation requires an enabled user privilege")
                            raise
                    elif kind == "hardlink":
                        os.link(outside, target)
                    else:
                        target.write_bytes(b"\xff\x00" if kind == "binary" else b"x" * (1024 * 1024 + 1))
                    response = client.get(f"/api/pages/{page}/artifacts/{item['id']}/download")
                    self.assertNotEqual(response.status_code, 200)
                    self.assertNotIn("OUTSIDE_CONTENT_MUST_NOT_LEAK", response.text)
                    preview = client.get(f"/api/pages/{page}/artifacts/{item['id']}").json()
                    self.assertEqual(preview["state"], "unavailable")
                    self.assertFalse(preview["downloadable"])

    def test_uploaded_literal_text_is_not_locally_redacted(self):
        content = b"Supplied [REDACTED] stays literal.\r\n"
        with self.client(online=False) as client:
            world = client.get("/api/bootstrap").json()["workspaceId"]
            response = client.post("/api/pages/home/materials", params={"name": "literal-source.txt"},
                content=content, headers={"Content-Type": "application/octet-stream",
                    "X-Per-Pulse-Upload-Id": uuid4().hex, "X-Per-Pulse-World": world,
                    "X-Per-Pulse-File-SHA256": sha(content)})
            self.assertEqual(response.status_code, 201, response.text)
            material = response.json()["material"]
            base = f"/api/pages/home/materials/{material['id']}"
            preview = client.get(base).json()
            self.assertFalse(preview["redacted"])
            self.assertEqual(preview["content"].encode(), content)
            self.assertTrue(preview["downloadable"])
        with self.client(online=False) as client:
            identity = client.get("/api/bootstrap").json()
            response = client.get(base + "/download", headers={
                "X-Per-Pulse-World": identity["workspaceId"],
                "X-Per-Pulse-Service-Instance": identity["serviceInstanceId"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, content)
            self.assertFalse(client.get(base).json()["redacted"])
        self.assertEqual(self.requests, [])

    def test_legacy_durable_payload_flags_are_unchanged(self):
        self.assertEqual(stored_payload_flags({"content": "[REDACTED]"}), (True, False))
        envelope = {"_truncated": True, "_redacted": True,
                    "original_sha256": "0" * 64, "preview": "old omitted arguments"}
        self.assertEqual(stored_payload_flags(envelope), (True, True))
        self.assertEqual(stored_text_flags("[TRUNCATED sha256=" + "0" * 64 + " redacted=1 preview=old]"),
                         (True, True))
        self.assertIsNone(artifact_module.PageArtifacts._item("home", type("OldAction", (), {
            "status": artifact_module.ActionStatus.SUCCEEDED, "tool_name": "mutate_file",
            "result": envelope})()))


if __name__ == "__main__":
    unittest.main()
