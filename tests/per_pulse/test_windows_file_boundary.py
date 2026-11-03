"""On Windows, workspace file access stays anchored to the directories it opened.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Each test swaps a directory for a junction to an outside folder at the worst
moment: right after that directory's handle was opened and before the file
inside it is read or written. Reads and writes must stay inside the
workspace, and a junction met by name must be refused.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handle semantics")


def _junction(target: Path, at: Path) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(at))


@pytest.fixture
def layout(tmp_path):
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "uploads").mkdir()
    outside.mkdir()
    (workspace / "notes" / "report.txt").write_bytes(b"inside\n")
    (outside / "report.txt").write_bytes(b"outside\n")
    return workspace, outside


def _swap_after_open(monkeypatch, name, workspace, outside):
    from pulse_system.product import winfiles
    swapped = []

    def hook(opened, _handle):
        if opened == name and not swapped:
            (workspace / name).rename(workspace / f"{name}-moved")
            _junction(outside, workspace / name)
            swapped.append(True)

    monkeypatch.setattr(winfiles, "component_opened", hook)
    return swapped


def test_a_directory_swapped_after_it_opened_cannot_redirect_a_read(layout, monkeypatch):
    from pulse_system.product.files import FileAccessError, WorkspaceFiles
    workspace, outside = layout
    swapped = _swap_after_open(monkeypatch, "notes", workspace, outside)
    result = WorkspaceFiles(workspace).read("notes/report.txt")
    assert swapped and result["content"] == "inside\n"
    # Met by name, the junction itself is refused.
    with pytest.raises(FileAccessError) as refused:
        WorkspaceFiles(workspace).read("notes/report.txt")
    assert refused.value.status == 404


def test_a_directory_swapped_after_it_opened_cannot_redirect_an_upload(layout, monkeypatch):
    from pulse_system.product.files import WorkspaceFiles
    workspace, outside = layout
    swapped = _swap_after_open(monkeypatch, "uploads", workspace, outside)
    receipt = WorkspaceFiles(workspace).upload("note.md", b"Uploaded.\n")
    assert swapped
    name = receipt["path"].split("/", 1)[1]
    assert (workspace / "uploads-moved" / name).read_bytes() == b"Uploaded.\n"
    assert sorted(p.name for p in outside.iterdir()) == ["report.txt"]


def test_a_junction_is_not_listed_or_followed(layout):
    from pulse_system.product.files import FileAccessError, WorkspaceFiles
    workspace, outside = layout
    _junction(outside, workspace / "linked")
    files = WorkspaceFiles(workspace)
    names = [entry["name"] for entry in files.list("")["entries"]]
    assert "linked" not in names and "notes" in names
    with pytest.raises(FileAccessError):
        files.read("linked/report.txt")
    with pytest.raises(FileAccessError):
        files.list("linked")
