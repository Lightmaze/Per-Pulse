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


def test_native_browse_continues_changed_directory_after_last_observed_entry(tmp_path):
    for number in range(45):
        (tmp_path / f"note-{number:03}.txt").write_text("Source.", encoding="utf-8")
    first = _browse(tmp_path)
    assert first["nextCursor"] and not first["restarted_due_to_change"]
    (tmp_path / "newly-added.txt").write_text("Another observation.", encoding="utf-8")
    continued = _browse(tmp_path, cursor=first["nextCursor"])
    assert continued["continued_after_change"] is True
    assert continued["restarted_due_to_change"] is False
    assert "continuing after" in continued["next_action"]
    first_names = {row["name"] for row in first["entries"]}
    later_names = {row["name"] for row in continued["entries"]}
    assert not first_names & later_names
    # Windows may enumerate newly inserted names before the previous anchor.
    # Refresh finds those names; continuation still covers the older sources.
    assert first_names | later_names >= {f"note-{number:03}.txt" for number in range(45)}


def test_native_browse_restarts_when_the_last_seen_entry_disappears(tmp_path):
    import base64
    import json

    for number in range(45):
        (tmp_path / f"note-{number:03}.txt").write_text("Source.", encoding="utf-8")
    first = _browse(tmp_path)
    cursor = first["nextCursor"]
    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    anchor = decoded["after"]
    assert anchor in {row["name"] for row in first["entries"]}
    (tmp_path / anchor).unlink()
    restarted = _browse(tmp_path, cursor=cursor)
    assert restarted["restarted_due_to_change"] is True
    assert restarted["continued_after_change"] is False
    assert "restarted" in restarted["next_action"]
    assert restarted["entries"]
    assert set(row["name"] for row in restarted["entries"]) & {
        row["name"] for row in first["entries"]
    }


def test_native_browse_legacy_cursor_restarts_visibly_after_change(tmp_path):
    import base64
    import json

    for number in range(45):
        (tmp_path / f"note-{number:03}.txt").write_text("Source.", encoding="utf-8")
    first = _browse(tmp_path)
    cursor = first["nextCursor"]
    value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    value["v"] = 1
    value.pop("after")
    old = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    (tmp_path / "newly-added.txt").write_text("Another observation.", encoding="utf-8")
    restarted = _browse(tmp_path, cursor=old)
    assert restarted["restarted_due_to_change"] is True
    assert restarted["continued_after_change"] is False


def test_native_browse_forged_cursor_cannot_probe_unlisted_anchor(tmp_path):
    """Editable base64 resume cursors must not disclose hidden or symlink names."""
    import base64
    import json
    import os

    for number in range(45):
        (tmp_path / f"note-{number:03}.txt").write_text("Source.", encoding="utf-8")
    (tmp_path / "private.pem").write_text("Not listed.", encoding="utf-8")
    if os.name != "nt":
        (tmp_path / "link.txt").symlink_to(tmp_path / "note-000.txt")
    first = _browse(tmp_path)
    cursor = first["nextCursor"]
    value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert value["v"] == 2

    for forbidden in (["private.pem", "link.txt"] if os.name != "nt" else ["private.pem"]):
        forged = {**value, "after": forbidden, "revision": [-1]}
        raw = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
        result = _browse(tmp_path, cursor=raw)
        assert result["restarted_due_to_change"] is True
        assert result["continued_after_change"] is False
        assert forbidden not in {entry["name"] for entry in result["entries"]}


@pytest.mark.parametrize("cursor", ["", 123, None, "x" * 4097])
def test_native_browse_rejects_invalid_cursor_shape(cursor):
    with pytest.raises(ProviderProtocolError):
        workspace_index.validate({"path": "", "cursor": cursor})
