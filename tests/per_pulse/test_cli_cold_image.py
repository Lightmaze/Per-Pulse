"""Native Per Pulse cold Image CLI round-trip, without a model provider.

The image is a private stopped copy of one World and Kernel, not a new
agent runtime, a live fork, or a provider-performance experiment.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zipfile

from fastapi.testclient import TestClient
import pytest

from per_pulse.app import create_app
from per_pulse.cli import main
from pulse_system.product import image


pytestmark = pytest.mark.skipif(os.name != "posix", reason="cold Image requires POSIX")


def _seed_stopped_home(home):
    with TestClient(create_app(home, model_factory=None), base_url="http://localhost") as client:
        original = client.get("/api/bootstrap").json()["workspaceId"]
        source = client.post("/api/pages/home/messages", json={
            "content": "Carry the local project and its sources."
        })
        assert source.status_code == 202, source.text
        created = client.post("/api/pages", json={
            "title": "One continuing Work",
            "instruction": "Keep the relevant history.",
            "sourceMessageId": source.json()["messageId"],
        })
        assert created.status_code == 201, created.text
        work = created.json()["page"]["id"]
        message = client.post(f"/api/pages/{work}/messages", json={
            "content": "The important result belongs to this Work."
        })
        assert message.status_code == 202, message.text
        returned = client.post(f"/api/pages/{work}/return", json={
            "content": "Bring the selected result back.",
            "messageId": message.json()["messageId"],
        })
        assert returned.status_code == 201, returned.text
    return original, work, message.json()["messageId"], returned.json()["messageId"]


def test_cold_image_command_preserves_original_source_and_return_after_restore(
    tmp_path, capsys,
):
    home = tmp_path / "original"
    world, work, source_message, returned = _seed_stopped_home(home)
    archive = tmp_path / "private.zip"

    main(["image", "save", str(archive), "--data-dir", str(home)])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["model_calls"] == 0
    assert archive.exists()
    main(["image", "inspect", str(archive)])
    inspection = json.loads(capsys.readouterr().out)
    assert inspection["compatible"] is True
    assert "per_pulse" in inspection["implementation"]
    assert inspection["world_id"] == world

    restored = tmp_path / "new-home"
    main(["image", "restore", str(archive), "--data-dir", str(restored)])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["model_calls"] == 0
    assert receipt["world_id"] == world
    assert not (restored / image.INCOMPLETE).exists()

    with TestClient(create_app(restored, model_factory=None), base_url="http://localhost") as client:
        assert client.get("/api/bootstrap").json()["workspaceId"] == world
        page = client.get(f"/api/pages/{work}").json()
        assert page["page"]["title"] == "One continuing Work"
        assert any(row["id"] == source_message for row in page["messages"])
        home_view = client.get("/api/pages/home").json()
        linked = next(row for row in home_view["messages"] if row["id"] == returned)
        assert linked["sourcePageId"] == work
        assert linked["sourceMessageId"] == source_message

    # The old home is a separate cold copy; restoring did not change it.
    with TestClient(create_app(home, model_factory=None), base_url="http://localhost") as client:
        assert client.get("/api/bootstrap").json()["workspaceId"] == world


def test_cold_image_refuses_live_world_and_existing_restore_destination(tmp_path, capsys):
    home = tmp_path / "active"
    with TestClient(create_app(home, model_factory=None), base_url="http://localhost"):
        with pytest.raises(SystemExit) as stopped:
            main(["image", "save", str(tmp_path / "blocked.zip"), "--data-dir", str(home)])
        assert stopped.value.code == 2
        assert not (tmp_path / "blocked.zip").exists()
    capsys.readouterr()
    archive = tmp_path / "saved.zip"
    main(["image", "save", str(archive), "--data-dir", str(home)])
    capsys.readouterr()
    with pytest.raises(SystemExit) as stopped:
        main(["image", "restore", str(archive), "--data-dir", str(home)])
    assert stopped.value.code == 2
    assert archive.exists()


@pytest.mark.parametrize("journal", ["world.db", "kernel.db"])
def test_cold_image_writer_contention_refuses_without_partial_archive_or_held_locks(tmp_path, capsys, journal):
    home, archive = tmp_path / "source", tmp_path / "copy.zip"
    _seed_stopped_home(home)
    writer = sqlite3.connect(home / journal, timeout=0, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(SystemExit) as stopped:
            main(["image", "save", str(archive), "--data-dir", str(home), "--include-recovered-history"])
        assert stopped.value.code == 2
        assert "stop the source cleanly" in capsys.readouterr().err
        assert writer.in_transaction
        assert not archive.exists() and not list(tmp_path.glob(".pulse-image-*"))
        # A later journal's refusal must release the earlier World reservation.
        if journal == "kernel.db":
            probe = sqlite3.connect(home / "world.db", timeout=0, isolation_level=None)
            try:
                probe.execute("BEGIN IMMEDIATE")
            finally:
                probe.close()
    finally:
        writer.close()
    main(["image", "save", str(archive), "--data-dir", str(home)])
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    assert archive.exists()


def test_cold_image_carries_actual_per_pulse_file_checkpoint_evidence(tmp_path, monkeypatch, capsys):
    from test_execution import _factory, _new_page, _wait
    from pulse_harness.adapters.workspace import WorkspaceAuthorityError

    home, restored, archive = tmp_path / "source", tmp_path / "carried", tmp_path / "checkpoint.zip"
    workspace = home / "workspace"
    workspace.mkdir(parents=True)
    original, revised = b"Original retained file bytes.\n", b"Revised through an approved native action.\n"
    (workspace / "note.txt").write_bytes(original)
    factory = _factory(monkeypatch, [], tool=("mutate_file", {
        "path": "note.txt", "expected_digest": hashlib.sha256(original).hexdigest(),
        "content": revised.decode(),
    }))
    with TestClient(create_app(home, model_factory=factory, allow_workspace_actions=True),
                    base_url="http://localhost") as client:
        page_id = _new_page(client)
        execution = client.app.state.pages.execution
        waiting = _wait(execution, page_id, lambda value: any(a["canResolve"] for a in value["pendingActions"]))
        action = next(a for a in waiting["pendingActions"] if a["canResolve"])
        approved = execution.resolve_approval(page_id, action["actionId"], "allow_once")
        assert approved["actionCompleted"]
        # Kernel completion precedes the World worker recording its result.
        # Export only after both original native stores have settled.
        _wait(execution, page_id, lambda value: value["status"] == "idle" and
              client.app.state.pages.runtime.storage._conn.execute(
                  "SELECT count(*) FROM harness_turns WHERE state IN ('running','uncertain')"
              ).fetchone()[0] == 0)
        artifact = client.get(f"/api/pages/{page_id}").json()["artifacts"][0]
        kernel = client.app.state.pages.runtime.kernel_service
        action_scope = kernel.store.action_scope_for_session(execution._scope(page_id), action["actionId"])
        checkpoint_root = kernel.checkpoint_root
        assert checkpoint_root == home / "workspace-checkpoints"
    retained = {p.relative_to(home).as_posix(): p.read_bytes() for p in checkpoint_root.rglob("*") if p.is_file()}
    assert retained and any(artifact["checkpointId"] in name for name in retained)
    assert (workspace / "note.txt").read_bytes() == revised

    main(["image", "save", str(archive), "--data-dir", str(home)])
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    with zipfile.ZipFile(archive) as saved:
        for name, content in retained.items():
            assert name in saved.namelist(), f"The native checkpoint was omitted: {name}"
            assert saved.read(name) == content
    main(["image", "restore", str(archive), "--data-dir", str(restored)])
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    for name, content in retained.items():
        assert (restored / name).read_bytes() == content
        assert (home / name).read_bytes() == content
    with TestClient(create_app(restored, model_factory=None, allow_workspace_actions=True), base_url="http://localhost") as client:
        carried = client.get(f"/api/pages/{page_id}").json()["artifacts"][0]
        assert carried["checkpointId"] == artifact["checkpointId"]
        kernel = client.app.state.pages.runtime.kernel_service
        assert not kernel._runtimes
        provider = kernel._provider()
        # Copying the checkpoint preserves its evidence, but never grants the
        # old action authority over either the new root or the original home.
        with pytest.raises(WorkspaceAuthorityError) as refused:
            provider.restore(action_scope, artifact["checkpointId"])
        assert refused.value.code == "workspace_authority_mismatch"
        assert (restored / "workspace/note.txt").read_bytes() == revised
        assert (workspace / "note.txt").read_bytes() == revised


def test_cold_image_refuses_unbound_upstream_archive(tmp_path, monkeypatch, capsys):
    home = tmp_path / "original"
    _seed_stopped_home(home)
    archive = tmp_path / "upstream-only.zip"
    original = image._implementation
    monkeypatch.setattr(image, "_implementation", lambda: {
        key: value for key, value in original().items() if key != "per_pulse"
    })
    image.save(home, archive)
    monkeypatch.setattr(image, "_implementation", original)
    with pytest.raises(SystemExit) as stopped:
        main(["image", "restore", str(archive), "--data-dir", str(tmp_path / "new")])
    assert stopped.value.code == 2
    assert "not bound to a Per Pulse" in capsys.readouterr().err
    assert not (tmp_path / "new").exists()


def test_cold_image_restore_requires_explicit_destination(tmp_path, capsys):
    # A new copy must be a deliberate destination, never the default home.
    archive = tmp_path / "unused-private.zip"
    with pytest.raises(SystemExit) as stopped:
        main(["image", "restore", str(archive)])
    assert stopped.value.code == 2
    assert "--data-dir NEW_HOME" in capsys.readouterr().err
    assert not archive.exists()


@pytest.mark.parametrize("marker_kind", ["regular_file", "dangling_symlink"])
def test_incomplete_cold_image_refuses_product_start(tmp_path, marker_kind):
    from per_pulse.service import PageError, PageService

    home = tmp_path / "partly-restored"
    home.mkdir()
    marker = home / image.INCOMPLETE
    if marker_kind == "regular_file":
        marker.write_text("interrupted private copy")
    else:
        marker.symlink_to("missing-marker-target")
    # A failed or interrupted native restore must not be initialized as a
    # runnable Home/Work just because the user opened the product CLI.
    with pytest.raises(PageError, match="incomplete cold Image restore") as error:
        PageService(home, model_factory=None)
    assert error.value.code == "image_restore_incomplete"
    assert marker.is_symlink() or marker.exists()
    assert not (home / "world.db").exists()
    assert not (home / "kernel.db").exists()
