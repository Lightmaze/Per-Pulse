"""Real-provider acceptance must never approve the model's tool requests itself.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import runpy
import stat
from pathlib import Path

import pytest


SCRIPT = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "acceptance" / "local_model_journey.py")
)
Journey = SCRIPT["Journey"]
write_private_report = SCRIPT["write_private_report"]


def _view(status, actions):
    return {
        "page": {"status": status},
        "execution": {"pendingActions": actions},
        "artifacts": [],
        "messages": [{"role": "assistant", "content": "Finished."}],
    }


def test_real_provider_journey_waits_for_the_original_work_to_decide(monkeypatch):
    journey = Journey("http://localhost:8000", turn_timeout=3)
    action = {"actionId": "source-action", "toolName": "run_command"}
    views = iter([_view("needs_attention", [action]), _view("idle", [])])
    journey.view = lambda _page: next(views)
    journey.post = lambda *_args, **_kwargs: pytest.fail("acceptance script must not grant authority")
    monkeypatch.setattr(SCRIPT["time"], "sleep", lambda _seconds: None)
    try:
        result = journey.wait_for_operator("original-work")
    finally:
        journey.http.close()
    assert result["approvalHandling"] == "human_in_original_work"
    assert result["observedRequests"] == [{"actionId": "source-action", "tool": "run_command"}]
    assert result["pendingActions"] == []
    assert result["pageStatus"] == "idle"


def test_real_provider_journey_times_out_without_approving(monkeypatch):
    journey = Journey("http://localhost:8000", turn_timeout=0)
    action = {"actionId": "unreviewed-action", "toolName": "mutate_file"}
    journey.view = lambda _page: _view("needs_attention", [action])
    journey.post = lambda *_args, **_kwargs: pytest.fail("no implicit Allow once")
    try:
        result = journey.wait_for_operator("original-work")
    finally:
        journey.http.close()
    assert result["ok"] is False
    assert result["pendingActions"] == ["mutate_file"]
    assert "none were approved" in result["error"]


def test_private_report_is_private_from_creation_and_cannot_overwrite(tmp_path):
    path = tmp_path / "provider-run.json"
    write_private_report(path, {"status": "private", "model": "redacted"})
    assert path.read_text(encoding="utf-8").startswith("{")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_private_report(path, {"status": "overwrite"})
    assert '"private"' in path.read_text(encoding="utf-8")
