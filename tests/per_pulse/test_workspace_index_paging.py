"""The native browse tool pages a real workspace rather than hiding later files.

Local files and scopes below are explicit synthetic fixtures.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pulse_harness.core.errors import ProviderProtocolError
from pulse_system.service import workspace_index


def _browse(root, **arguments):
    store = SimpleNamespace(get_session=lambda _: None, max_payload_bytes=1024 * 1024)
    provider = SimpleNamespace(store=store, workspace_root=root,
                               _is_protected_relative=lambda path: False)
    scope = SimpleNamespace(session_scope=lambda: "session")
    call = SimpleNamespace(arguments={"path": "", **arguments})
    cancellation = SimpleNamespace(raise_if_cancelled=lambda: None)
    return workspace_index.browse(provider, scope, call, cancellation)


def test_native_browse_reaches_every_file_across_pages(tmp_path):
    for number in range(73):
        (tmp_path / f"note-{number:03}.txt").write_text("Source.", encoding="utf-8")
    observed, cursor = [], None
    for _ in range(6):
        page = _browse(tmp_path, **({"cursor": cursor} if cursor else {}))
        assert len(page["entries"]) <= 32
        assert page["restarted_due_to_change"] is False
        observed.extend(row["name"] for row in page["entries"])
        cursor = page["nextCursor"]
        if cursor is None:
            assert page["truncated"] is False
            break
    else:
        pytest.fail("native workspace listing did not finish")
    assert len(observed) == 73
    assert set(observed) == {f"note-{number:03}.txt" for number in range(73)}


def test_native_browse_restarts_changed_directory_with_visible_note(tmp_path):
    for number in range(45):
        (tmp_path / f"note-{number:03}.txt").write_text("Source.", encoding="utf-8")
    first = _browse(tmp_path)
    assert first["nextCursor"] and not first["restarted_due_to_change"]
    (tmp_path / "newly-added.txt").write_text("Another observation.", encoding="utf-8")
    restarted = _browse(tmp_path, cursor=first["nextCursor"])
    assert restarted["restarted_due_to_change"] is True
    assert "restarted" in restarted["next_action"]
    assert restarted["entries"] and restarted["nextCursor"]
    assert set(row["name"] for row in first["entries"]) & set(
        row["name"] for row in restarted["entries"])


@pytest.mark.parametrize("cursor", ["", 123, None, "x" * 4097])
def test_native_browse_rejects_invalid_cursor_shape(cursor):
    with pytest.raises(ProviderProtocolError):
        workspace_index.validate({"path": "", "cursor": cursor})
