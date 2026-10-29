"""A cold copy may retain a recovered turn, but never replay uncertain effects.

The subprocess uses a synthetic, deliberately blocked model transport. Process
death, lease expiry, World recovery, Kernel cancellation and copying are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import zipfile

import pytest

from per_pulse.cli import main
from pulse_system.product import image
from test_pages import client_for, send


pytestmark = pytest.mark.skipif(os.name != "posix", reason="cold Image requires POSIX")

_CHILD = r'''
from functools import partial
import json
import os
from pathlib import Path
import sys
import threading
import time
import httpx
from fastapi.testclient import TestClient
import per_pulse.service as service
from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider

home, evidence = map(Path, sys.argv[1:3])
mode = sys.argv[3]
service.RuntimeServiceConfig = partial(service.RuntimeServiceConfig,
    runtime_lease_ttl_sec=5, runtime_lease_renew_interval_sec=1)
os.environ["PER_PULSE_IMAGE_FIXTURE_KEY"] = "synthetic-local-only"

def blocked(request):
    (evidence / "request-started").write_text("one synthetic request\n")
    if mode == "model":
        threading.Event().wait(120)
        raise AssertionError("The parent must kill this process during the request.")
    # A pending native approval must survive death for an explicit Stop. No
    # file effect is authorized or executed in this fixture.
    delta = {"tool_calls": [{"index": 0, "id": "pending-image-fixture", "type": "function",
        "function": {"name": "mutate_file", "arguments": json.dumps({
            "path": "fixture-effect.txt", "expected_digest": None, "content": "Synthetic file effect.\n"})}}]}
    chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

def factory(scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "image-fixture",
        credential_env="PER_PULSE_IMAGE_FIXTURE_KEY"),
        transport=httpx.MockTransport(blocked))

original_replace = StructuredWorkspaceToolProvider._replace
def stop_after_effect(self, plan):
    original_replace(self, plan)
    (evidence / "effect-started").write_text("native file replacement happened\n")
    threading.Event().wait(120)
    raise AssertionError("The parent must kill this process before its effect receipt.")
if mode == "effect":
    StructuredWorkspaceToolProvider._replace = stop_after_effect

with TestClient(create_app(home, model_factory=factory, allow_workspace_actions=True), base_url="http://localhost") as client:
    result = client.post("/api/pages", json={
        "title": "Interrupted work", "instruction": "Keep the original question and its history."})
    assert result.status_code == 201, result.text
    (evidence / "page.json").write_text(json.dumps(result.json()))
    if mode == "effect":
        page = result.json()["page"]["id"]
        while True:
            execution = client.app.state.pages.execution
            actions = execution.snapshot(page)["pendingActions"]
            if actions:
                execution.resolve_approval(page, actions[0]["actionId"], "allow_once")
                raise AssertionError("The parent must stop the approved effect.")
            time.sleep(0.05)
    while True:
        time.sleep(1)
'''


def _rows(home, database, query, parameters=()):
    with closing(sqlite3.connect((home / database).as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute(query, parameters).fetchall()


@pytest.fixture
def recovered_home(tmp_path, request):
    home, evidence = tmp_path / "original", tmp_path / "evidence"
    evidence.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    mode = getattr(request, "param", "approval")
    with (evidence / "child.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-u", "-c", _CHILD, str(home), str(evidence), mode],
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 20
            while not all((evidence / name).exists() for name in ("request-started", "page.json")):
                assert process.poll() is None, (evidence / "child.log").read_text()
                assert time.monotonic() < deadline, "The native model request never started."
                time.sleep(0.05)
            page = json.loads((evidence / "page.json").read_text())["page"]["id"]
            if mode == "effect":
                while not (evidence / "effect-started").exists():
                    assert time.monotonic() < deadline, "The native file effect never started."
                    time.sleep(0.05)
            elif mode == "approval":
                while not _rows(home, "kernel.db", "SELECT 1 FROM actions WHERE status='approval_pending'"):
                    assert time.monotonic() < deadline, "The native approval never became pending."
                    time.sleep(0.05)
            # An opt-in archive still cannot acquire a live or unreleased body.
            with pytest.raises(image.ImageError, match="stop the source cleanly"):
                image.save(home, tmp_path / "live.zip", include_recovered_history=True)
            process.kill()
            assert process.wait(timeout=5) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    expires = _rows(home, "world.db", "SELECT expires_at FROM runtime_leases WHERE scope='pulse_world'")[0][0]
    wait = (datetime.fromisoformat(expires) - datetime.now(timezone.utc)).total_seconds()
    assert wait < 6
    time.sleep(max(0, wait) + 0.1)
    # Real startup recovery keeps the original record uncertain. It never asks
    # a model to recreate an answer that might have been generated before death.
    with client_for(home, model_factory=None) as client:
        view = client.get(f"/api/pages/{page}").json()
        execution = view["execution"]
        turn = execution["turnId"]
        if mode in {"approval", "effect"}:
            assert execution["recoveryRequired"] and execution["canCancel"]
        else:
            assert execution["status"] == "stopped"
            assert not execution["recoveryRequired"]
    if mode in {"approval", "effect"}:
        with pytest.raises(image.ImageError, match="Kernel has unfinished turns"):
            image.save(home, tmp_path / "unfinished.zip", include_recovered_history=True)
        with client_for(home, model_factory=None) as client:
            cancelled = client.app.state.pages.execution.cancel(page, turn)
            assert cancelled["accepted"]
    assert (home / "workspace/fixture-effect.txt").exists() == (mode == "effect")
    return home, page, turn


@pytest.mark.parametrize("recovered_home", ["approval", "model"], indirect=True)
def test_explicit_copy_keeps_recovery_evidence_and_original_work(recovered_home, tmp_path, capsys):
    home, page, turn = recovered_home
    original = _rows(home, "world.db", "SELECT * FROM harness_turns WHERE id = ?", (turn,))
    original_events = _rows(home, "world.db", "SELECT * FROM causal_events ORDER BY seq")
    assert _rows(home, "kernel.db", "SELECT status FROM turns WHERE turn_id = ?", (turn,)) == [("cancelled",)]
    archive = tmp_path / "private.zip"
    with pytest.raises(image.ImageError, match="unsettled native turns"):
        image.save(home, archive)
    assert not archive.exists()

    main(["image", "save", str(archive), "--data-dir", str(home), "--include-recovered-history"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["retained_uncertain_turns"] == [turn]
    assert receipt["model_calls"] == 0
    assert image.inspect(archive)["retained_uncertain_turns"] == [turn]
    copied = tmp_path / "copy"
    restored = image.restore(archive, copied)
    assert restored["retained_uncertain_turns"] == [turn]
    assert restored["model_calls"] == 0
    assert _rows(copied, "world.db", "SELECT * FROM harness_turns WHERE id = ?", (turn,)) == original
    assert _rows(copied, "world.db", "SELECT * FROM causal_events ORDER BY seq") == original_events
    assert _rows(home, "world.db", "SELECT * FROM causal_events ORDER BY seq") == original_events

    with client_for(copied, model_factory=None) as client:
        view = client.get(f"/api/pages/{page}").json()
        assert view["page"]["title"] == "Interrupted work"
        # Model requests and tool approvals have different recovery evidence;
        # the original uncertain World message remains visible in both cases.
        assert not view["execution"]["recoveryRequired"]
        assert len(view["messages"]) == 1
        assert view["messages"][0]["status"] == "uncertain"
        assert not client.app.state.pages.runtime.kernel_service._runtimes
        send(client, page, "Continue only from this new instruction.")
        continued = client.get(f"/api/pages/{page}").json()
        assert len(continued["messages"]) == 2
        assert continued["messages"][0]["id"] == view["messages"][0]["id"]
        assert all(message["role"] == "user" for message in continued["messages"])
    assert _rows(home, "world.db", "SELECT * FROM causal_events ORDER BY seq") == original_events


def test_restore_checks_recovery_manifest_against_native_history(recovered_home, tmp_path):
    home, _, turn = recovered_home
    archive = tmp_path / "private.zip"
    image.save(home, archive, include_recovered_history=True)
    altered = tmp_path / "altered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(altered, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(content)
                manifest["retained_uncertain_turns"] = [turn + "-not-the-original"]
                content = json.dumps(manifest).encode()
            target.writestr(name, content)
    destination = tmp_path / "refused-copy"
    with pytest.raises(image.ImageError, match="recovery history differs"):
        image.restore(altered, destination)
    assert (destination / image.INCOMPLETE).exists()


@pytest.mark.parametrize("recovered_home", ["effect"], indirect=True)
def test_opt_in_still_refuses_an_actual_unconfirmed_file_effect(recovered_home, tmp_path):
    home, _, turn = recovered_home
    assert _rows(home, "kernel.db", "SELECT status FROM actions WHERE turn_id = ?", (turn,)) == [("uncertain",)]
    assert (home / "workspace/fixture-effect.txt").read_bytes() == b"Synthetic file effect.\n"
    with pytest.raises(image.ImageError, match="Kernel has unfinished actions"):
        image.save(home, tmp_path / "refused.zip", include_recovered_history=True)
    assert not (tmp_path / "refused.zip").exists()
    assert (home / "workspace/fixture-effect.txt").read_bytes() == b"Synthetic file effect.\n"


@pytest.mark.parametrize("change", ["foreign_context", "missing_turn", "unsettled_event", "different_uncertainty"])
def test_opt_in_requires_exact_settled_recovery_binding(recovered_home, tmp_path, change):
    home, _, turn = recovered_home
    database, statement = {
        "foreign_context": ("kernel.db", "UPDATE turns SET context_id = 'work:another' WHERE turn_id = ?"),
        "missing_turn": ("kernel.db", "DELETE FROM turns WHERE turn_id = ?"),
        "unsettled_event": ("world.db", "UPDATE causal_events SET settled_at = NULL WHERE id = (SELECT event_id FROM harness_turns WHERE id = ?)"),
        "different_uncertainty": ("world.db", "UPDATE harness_turns SET error_code = 'unknown_effect' WHERE id = ?"),
    }[change]
    # These are deliberately malformed archive-source candidates, not a claim
    # that the normal native API produces or repairs this inconsistent state.
    with sqlite3.connect(home / database) as db:
        db.execute(statement, (turn,))
    archive = tmp_path / "refused.zip"
    with pytest.raises(image.ImageError, match="recovery|finished Kernel"):
        image.save(home, archive, include_recovered_history=True)
    assert not archive.exists()


@pytest.mark.parametrize("operation", ["inspect", "restore"])
def test_include_recovery_is_a_save_option_only(tmp_path, capsys, operation):
    with pytest.raises(SystemExit) as refused:
        main(["image", operation, str(tmp_path / "unused.zip"), "--data-dir", str(tmp_path / "copy"),
              "--include-recovered-history"])
    assert refused.value.code == 2
    assert "applies only to image save" in capsys.readouterr().err
