"""Native Per Pulse cold Image CLI round-trip, without a model provider.

The image is a private stopped copy of one World and Kernel, not a new
agent runtime, a live fork, or a provider-performance experiment.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import os

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
