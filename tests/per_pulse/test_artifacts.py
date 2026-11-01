"""Native write provenance and bounded previews using synthetic model choices.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is explicitly scripted; file effects, approvals,
World/Kernel persistence, path validation, and digests are real. No external
model is contacted by these checks.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from per_pulse.artifacts import ArtifactError, PageArtifacts
from per_pulse.execution import ExecutionError, PageExecution
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.events import EventDraft


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _factory(monkeypatch, plans, requests, *, tool_name="mutate_file", tool_call_id="synthetic-file-request"):
    monkeypatch.setenv("PER_PULSE_ARTIFACT_TEST_KEY", "synthetic-test-credential")

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]
        last_user = max((i for i, message in enumerate(messages) if message.get("role") == "user"), default=0)
        current = messages[last_user:]
        prompt = json.dumps(current)
        selected = next((value for marker, value in plans.items() if marker in prompt), None)
        if selected is not None and not any(message.get("role") == "tool" for message in current):
            delta = {"tool_calls": [{"index": 0, "id": tool_call_id, "type": "function", "function": {
                "name": tool_name, "arguments": json.dumps(selected),
            }}]}
            finish = "tool_calls"
        else:
            # An assistant's claim is not itself evidence of a file result.
            delta = {"content": "Synthetic assistant: the report is ready."}
            finish = "stop"
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "artifact-fixture", credential_env="PER_PULSE_ARTIFACT_TEST_KEY"),
            transport=httpx.MockTransport(handler),
        )

    return factory


def _client(path: Path, **kwargs):
    return TestClient(create_app(path, **kwargs), base_url="http://localhost")


def _page(client, instruction="WRITE_REPORT"):
    response = client.post("/api/pages", json={"title": instruction, "instruction": instruction})
    assert response.status_code == 201, response.text
    return response.json()["page"]["id"]


def _wait(execution, page_id, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    view = None
    while time.monotonic() < deadline:
        view = execution.snapshot(page_id)
        if predicate(view):
            return view
        time.sleep(0.025)
    pytest.fail(f"Page did not reach the expected state: {view}")


def _approve(execution, page_id):
    waiting = _wait(execution, page_id, lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
    action_id = waiting["pendingActions"][0]["actionId"]
    execution.resolve_approval(page_id, action_id, "allow_once")
    _wait(execution, page_id, lambda view: view["status"] in {"idle", "error"})
    return action_id


def test_listing_without_a_session_has_no_runtime_or_file_effect(tmp_path):
    with _client(tmp_path, model_factory=None) as client:
        runtime = client.app.state.pages.runtime
        artifacts = PageArtifacts(runtime)
        page_id = _page(client, "A queued report.")
        before = runtime.kernel_service.store.sessions_for_world(runtime.world_id)
        for target in ("home", page_id):
            assert artifacts.snapshot(target)["artifacts"] == []
        assert runtime.kernel_service.store.sessions_for_world(runtime.world_id) == before
        assert not runtime.kernel_service._runtimes
        assert runtime.tick_count == 0
        with pytest.raises(ExecutionError) as missing:
            artifacts.snapshot("missing")
        assert missing.value.status == 404
        with pytest.raises(ArtifactError):
            artifacts.read(page_id, "../../world.db")


def test_successful_file_result_has_native_source_and_survives_restart(tmp_path, monkeypatch):
    content, requests = "A confirmed report.\n", []
    factory = _factory(monkeypatch, {"WRITE_REPORT": {"path": "report.txt", "expected_digest": None, "content": content}}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client)
        pending = _wait(execution, page_id, lambda view: bool(view["pendingActions"]))
        assert artifacts.snapshot(page_id)["artifacts"] == []
        action_id = _approve(execution, page_id)
        item, = artifacts.snapshot(page_id)["artifacts"]
        assert item["id"] == item["actionId"] == action_id
        assert item["sha256"] == item["version"] == _sha(content)
        assert item["turnId"] == pending["turnId"]
        assert item["sessionId"] == pending["sessionId"]
        assert item["path"] == "report.txt" and item["state"] == "unchecked"
        assert item["changeId"].startswith("change-")
        assert item["checkpointId"] and item["operationId"]
        assert runtime.kernel_service.store.has_event(
            execution._scope(page_id), "change.applied", action_request_id=action_id,
        )
        preview = artifacts.read(page_id, action_id)
        assert preview["state"] == "current"
        assert preview["content"] == content
        assert preview["observedSha256"] == item["sha256"]
        assert preview["bytes"] == len(content.encode("utf-8"))
        assert not preview["redacted"] and not preview["truncated"]
        listed = client.get(f"/api/pages/{page_id}/artifacts")
        assert listed.status_code == 200 and listed.json()["artifacts"] == [item]
        assert client.get(f"/api/pages/{page_id}").json()["artifacts"] == [item]
        opened = client.get(f"/api/pages/{page_id}/artifacts/{action_id}")
        assert opened.status_code == 200 and opened.json() == preview
        assert opened.headers["cache-control"] == "no-store"
        assert artifacts.snapshot("home")["artifacts"] == []
        second_page = _page(client, "No file request.")
        for wrong_page in ("home", second_page):
            with pytest.raises(ArtifactError) as mismatch:
                artifacts.read(wrong_page, action_id)
            assert mismatch.value.status == 404
            assert client.get(f"/api/pages/{wrong_page}/artifacts/{action_id}").status_code == 404

    round_count = len(requests)
    with _client(tmp_path, model_factory=None, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        artifacts = PageArtifacts(runtime)
        assert artifacts.snapshot(page_id)["artifacts"] == [item]
        assert artifacts.read(page_id, action_id)["content"] == content
        assert len(requests) == round_count
        assert not runtime.kernel_service._runtimes


def test_original_work_effect_evidence_survives_native_succession_without_replay(tmp_path, monkeypatch):
    """A committed generation changes the holder, not the Work's file sources."""
    import per_pulse.execution as execution_module
    from pulse_harness.core import TurnStatus
    from pulse_harness.core.contracts import ActionClassification, TerminalOutcome

    requests = []
    content = "The original approved report.\n"
    plans = {"WRITE_REPORT": {"path": "report.txt", "expected_digest": None, "content": content}}
    factory = _factory(monkeypatch, plans, requests)
    monkeypatch.setattr(execution_module, "_MAX_ACTIONS", 2)

    def uncertain_fixture(store, scope, prefix, count):
        # These are explicitly synthetic read-only outcomes, not claims of
        # physical effects. The confirmed file below uses actual approval/write.
        store.create_turn(scope, "Synthetic retained uncertain read evidence")
        for index in range(count):
            identity = f"{prefix}-{index}"
            store.create_action(scope, action_request_id=identity, tool_name="fixture_read",
                                arguments={"path": f"{prefix}-{index}.txt", "api_key": "SYNTHETIC_SECRET_VALUE"},
                                requires_approval=False, classification=ActionClassification.READ_ONLY_NOT_REQUIRED)
            store.claim_action_start(scope, identity)
            store.mark_action_running(scope, identity)
            store.terminal_action(scope, identity, TerminalOutcome.UNCERTAIN)
        store.set_turn_status(scope, TurnStatus.CANCELLED)

    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        pages = client.app.state.pages
        runtime, execution, artifacts = pages.runtime, pages.execution, pages.artifacts
        work = _page(client)
        action = _approve(execution, work)
        item, = artifacts.snapshot(work)["artifacts"]
        _wait(execution, work, lambda _value: bool(
            runtime.causal_ledger.get_turn_for_world(item["turnId"], runtime.world_id).result_event_id,
        ))
        source_result = runtime.causal_ledger.get_turn_for_world(item["turnId"], runtime.world_id).result_event_id
        original_subject = runtime.continuity_engram_id
        original_mtime = (tmp_path / "workspace" / "report.txt").stat().st_mtime_ns
        store = runtime.kernel_service.store
        old_scope = execution._scope(work).for_turn("synthetic-uncertain-predecessor")
        uncertain_fixture(store, old_scope, "older-uncertain", 3)

        # Summary and future explicit input use a declared reply-only fixture.
        # Native prepare/commit performs the real generation and Work handoff.
        plans.clear()
        prepared = runtime._mgr.prepare_succession(
            original_subject, parent_event_id=source_result, runtime_fence=runtime._current_runtime_fence(),
        )
        assert artifacts.snapshot(work)["artifacts"] == [item]
        assert runtime.causal_ledger.committed_history_lineage(runtime.world_id, prepared.successor_id) == (prepared.successor_id,)
        rotated = runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence())
        assert runtime.world.get_task_front(work).focal_engram_id == rotated.new_id
        assert runtime.causal_ledger.committed_history_lineage(runtime.world_id, rotated.new_id) == (rotated.new_id, original_subject)
        before_sessions = store.sessions_for_world(runtime.world_id)
        before_runners = set(runtime.kernel_service._runtimes)
        before_requests = len(requests)

        assert artifacts.snapshot(work)["artifacts"] == [item]
        assert artifacts.read(work, action)["content"] == content
        inherited = client.get(f"/api/pages/{work}").json()
        assert inherited["artifacts"] == [item]
        state = inherited["execution"]
        assert state["sessionId"] is None  # Reading must not open the new Work session.
        assert state["historicalUncertainty"] and state["uncertainActionsTruncated"]
        assert [record["actionId"] for record in state["uncertainActions"]] == ["older-uncertain-2", "older-uncertain-1"]
        assert not state["canCancel"] and not state["pendingActions"] and not state["recoveryRequired"]
        assert "SYNTHETIC_SECRET_VALUE" not in json.dumps(state)
        assert store.sessions_for_world(runtime.world_id) == before_sessions
        assert set(runtime.kernel_service._runtimes) == before_runners
        assert len(requests) == before_requests
        for operation in (
            lambda: execution.cancel(work, old_scope.turn_id),
            lambda: execution.resolve_approval(work, action, "allow_once"),
        ):
            with pytest.raises(ExecutionError):
                operation()
        assert (tmp_path / "workspace" / "report.txt").stat().st_mtime_ns == original_mtime

        sibling = _page(client, "A different Work after succession.")
        for other in ("home", sibling):
            assert artifacts.snapshot(other)["artifacts"] == []
            assert execution.snapshot(other)["uncertainActions"] == []
            assert client.get(f"/api/pages/{other}/artifacts/{action}").status_code == 404
        sent = client.post(f"/api/pages/{work}/messages", json={"content": "Continue the same Work with a new explicit instruction."})
        assert sent.status_code == 202
        _wait(execution, work, lambda value: value["sessionId"] is not None and value["status"] == "idle")
        uncertain_fixture(store, execution._scope(work).for_turn("synthetic-uncertain-successor"), "newer-uncertain", 1)
        latest = execution.snapshot(work)
        assert [record["actionId"] for record in latest["uncertainActions"]] == ["newer-uncertain-0", "older-uncertain-2"]
        assert latest["uncertainActionsTruncated"] is True  # One bound across generations.
        assert artifacts.snapshot(work)["artifacts"] == [item]
        assert (tmp_path / "workspace" / "report.txt").stat().st_mtime_ns == original_mtime

    with _client(tmp_path, model_factory=None, allow_workspace_actions=True) as client:
        pages = client.app.state.pages
        assert pages.runtime.continuity_engram_id == rotated.new_id
        assert pages.artifacts.snapshot(work)["artifacts"] == [item]
        assert pages.artifacts.read(work, action)["content"] == content
        assert [record["actionId"] for record in pages.execution.snapshot(work)["uncertainActions"]] == ["newer-uncertain-0", "older-uncertain-2"]
        assert not pages.runtime.kernel_service._runtimes
        assert (tmp_path / "workspace" / "report.txt").stat().st_mtime_ns == original_mtime


def test_unverifiable_history_fails_closed_before_reading_effects(tmp_path, monkeypatch):
    from pulse_system.core.causality import CausalTransitionError

    with _client(tmp_path, model_factory=None) as client:
        pages = client.app.state.pages

        def invalid_lineage(*_args):
            raise CausalTransitionError("synthetic ambiguous ancestry")

        monkeypatch.setattr(pages.runtime.causal_ledger, "committed_history_lineage", invalid_lineage)
        for path in ("/api/pages/home/execution", "/api/pages/home/artifacts"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "execution_history_unavailable"


@pytest.mark.parametrize("decision", ["deny", "cancel"])
def test_rejected_file_actions_and_unattributed_files_are_not_artifacts(tmp_path, monkeypatch, decision):
    requests = []
    factory = _factory(monkeypatch, {"WRITE_REPORT": {"path": "report.txt", "expected_digest": None, "content": "A report."}}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client)
        waiting = _wait(execution, page_id, lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        execution.resolve_approval(page_id, action_id, decision)
        (tmp_path / "workspace" / "unattributed.txt").write_text("A user file, not a page-owned output.")
        assert artifacts.snapshot(page_id)["artifacts"] == []
        with pytest.raises(ArtifactError):
            artifacts.read(page_id, action_id)
        with pytest.raises(ArtifactError):
            artifacts.read(page_id, "unattributed.txt")


def test_failed_digest_guard_does_not_claim_an_artifact(tmp_path, monkeypatch):
    requests = []
    factory = _factory(monkeypatch, {"WRITE_REPORT": {"path": "report.txt", "expected_digest": None, "content": "A replacement."}}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        original = tmp_path / "workspace" / "report.txt"
        original.write_text("Existing source.")
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client)
        action_id = _approve(execution, page_id)
        assert artifacts.snapshot(page_id)["artifacts"] == []
        assert original.read_text() == "Existing source."
        with pytest.raises(ArtifactError):
            artifacts.read(page_id, action_id)


def test_other_page_overwrite_marks_the_original_version_changed(tmp_path, monkeypatch):
    first, second, requests = "First page version.\n", "Second page version.\n", []
    factory = _factory(monkeypatch, {
        "WRITE_FIRST": {"path": "shared.txt", "expected_digest": None, "content": first},
        "WRITE_SECOND": {"path": "shared.txt", "expected_digest": _sha(first), "content": second},
    }, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        first_page = _page(client, "WRITE_FIRST")
        first_id = _approve(execution, first_page)
        second_page = _page(client, "WRITE_SECOND")
        second_id = _approve(execution, second_page)
        stale = artifacts.read(first_page, first_id)
        assert stale["state"] == stale["artifact"]["state"] == "changed"
        assert stale["reason"] == "artifact_version_changed"
        assert stale["artifact"]["sha256"] == _sha(first)
        assert stale["observedSha256"] == _sha(second)
        assert stale["content"] is None
        assert second not in json.dumps(stale)
        response = client.get(f"/api/pages/{first_page}/artifacts/{first_id}")
        assert response.status_code == 200 and response.json()["state"] == "changed"
        assert response.json()["content"] is None
        denied_download = client.get(f"/api/pages/{first_page}/artifacts/{first_id}/download")
        assert denied_download.status_code == 409
        assert denied_download.json()["error"]["code"] == "artifact_version_changed"
        assert second not in denied_download.text
        assert artifacts.read(second_page, second_id)["content"] == second
        assert artifacts.snapshot(first_page)["artifacts"][0]["id"] == first_id
        with pytest.raises(ArtifactError):
            artifacts.read(first_page, second_id)


def test_preview_uses_existing_path_file_type_and_size_guards(tmp_path, monkeypatch):
    requests = []
    factory = _factory(monkeypatch, {"WRITE_REPORT": {"path": "report.txt", "expected_digest": None, "content": "A report."}}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client)
        action_id = _approve(execution, page_id)
        target = tmp_path / "workspace" / "report.txt"
        outside = tmp_path / "private.txt"
        outside.write_text("Outside the tool workspace.")
        target.unlink()
        assert artifacts.read(page_id, action_id)["state"] == "unavailable"
        target.symlink_to(outside)
        linked = artifacts.read(page_id, action_id)
        assert linked["state"] == "unavailable" and linked["content"] is None
        assert client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download").status_code == 404
        target.unlink()
        os.link(outside, target)
        linked = artifacts.read(page_id, action_id)
        assert linked["reason"] == "not_a_single_regular_file" and linked["content"] is None
        assert client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download").status_code == 403
        target.unlink()
        for data, reason in (
            (b"\xff\x00", "text_files_only"),
            (b"x" * (1024 * 1024 + 1), "file_too_large"),
            (b"password=synthetic-sensitive-value", "credential_content_blocked"),
        ):
            target.write_bytes(data)
            preview = artifacts.read(page_id, action_id)
            assert preview["state"] == "unavailable" and preview["content"] is None
            assert preview["reason"] == reason
            denied = client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download")
            assert denied.status_code in {403, 413, 415}
            assert denied.json()["error"]["code"] == reason
        workspace = tmp_path / "workspace"
        original_workspace = tmp_path / "original-workspace"
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        (replacement / "report.txt").write_text("A replacement root's private content.")
        workspace.rename(original_workspace)
        try:
            workspace.symlink_to(replacement, target_is_directory=True)
            root_preview = artifacts.read(page_id, action_id)
            assert root_preview["state"] == "unavailable" and root_preview["content"] is None
            workspace.unlink()
            replacement.rename(workspace)
            root_preview = artifacts.read(page_id, action_id)
            assert root_preview["state"] == "changed" and root_preview["content"] is None
            assert "private content" not in json.dumps(root_preview)
        finally:
            if workspace.is_symlink():
                workspace.unlink()
            elif workspace.exists():
                workspace.rename(replacement)
            original_workspace.rename(workspace)


def test_preview_redacts_and_bounds_text_after_verifying_original_bytes(tmp_path, monkeypatch):
    import per_pulse.artifacts as module

    monkeypatch.setattr(module, "MAX_PREVIEW_BYTES", 256)
    before = "Draft heading.\npassword=abc\n" + "Ordinary report content.\n" * 80
    after = before.replace("Draft heading.", "Final heading.")
    requests = []
    factory = _factory(monkeypatch, {"PATCH_REPORT": {
        "path": "report.txt", "expected_digest": _sha(before),
        "patch": {"edits": [{"old": "Draft heading.", "new": "Final heading."}]},
    }}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        (tmp_path / "workspace" / "report.txt").write_text(before)
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client, "PATCH_REPORT")
        waiting = _wait(execution, page_id, lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        try:
            execution.resolve_approval(page_id, action_id, "allow_once")
        except ExecutionError as error:
            # The existing runner can reject a previously redacted tool
            # result during continuation. A confirmed file effect still has
            # its original artifact even when that later step cannot finish.
            assert error.code == "redaction_required"
        preview = artifacts.read(page_id, action_id)
        assert preview["state"] == "current"
        assert preview["observedSha256"] == _sha(after)
        assert preview["redacted"] and preview["truncated"]
        assert "password=abc" not in preview["content"]
        assert len(preview["content"].encode("utf-8")) <= 256
        assert preview["bytes"] == len(after.encode("utf-8"))
        denied = client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download")
        assert denied.status_code == 422
        assert denied.json()["error"]["code"] == "artifact_download_redacted"
        assert "password=abc" not in denied.text

def test_preview_rejects_hidden_marker_beyond_bounded_prefix(tmp_path, monkeypatch):
    """A safe-looking preview cannot authorize carrying an unsafe complete file."""
    import per_pulse.artifacts as module

    monkeypatch.setattr(module, "MAX_PREVIEW_BYTES", 256)
    before = "Draft status.\n" + "Public details only.\n" * 32 + "[REDACTED]\n"
    after = before.replace("Draft status.", "Final status.", 1)
    factory = _factory(monkeypatch, {"PATCH_LATE_MARKER": {
        "path": "late-marker.txt", "expected_digest": _sha(before),
        "patch": {"edits": [{"old": "Draft status.", "new": "Final status."}]},
    }}, [])
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        (tmp_path / "workspace" / "late-marker.txt").write_text(before)
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client, "PATCH_LATE_MARKER")
        waiting = _wait(execution, page_id, lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        try:
            execution.resolve_approval(page_id, action_id, "allow_once")
        except ExecutionError as error:
            assert error.code == "redaction_required"
        preview = artifacts.read(page_id, action_id)
        assert preview["state"] == "current"
        assert preview["observedSha256"] == _sha(after)
        assert preview["truncated"]
        assert "[REDACTED]" not in preview["content"]
        # The entire file contains a persisted redaction marker *after* the
        # displayed portion. Both actions must agree it is not transferable.
        assert preview["redacted"]
        denied = client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download")
        assert denied.status_code == 422
        assert denied.json()["error"]["code"] == "artifact_download_redacted"


def test_download_keeps_complete_utf8_bytes_and_filename_after_restart(tmp_path, monkeypatch):
    from urllib.parse import quote
    import per_pulse.artifacts as artifact_module

    # Exercise preview clipping independently of the native write ceiling.
    # The real adapter currently admits file writes up to its 64 KiB payload
    # bound; increasing that is a separate file-lifecycle decision.
    monkeypatch.setattr(artifact_module, "MAX_PREVIEW_BYTES", 4096)
    before = "\ufeffDRAFT\r\n" + "Café and 文本 retain  two spaces.\r\n" * 1200
    after = before.replace("DRAFT", "FINAL", 1)
    filename = "Café 100% 文本.txt"
    relative = "results/" + filename
    requests = []
    factory = _factory(monkeypatch, {"PATCH_FULL_REPORT": {
        "path": relative, "expected_digest": _sha(before),
        "patch": {"edits": [{"old": "DRAFT", "new": "FINAL"}]},
    }}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        (tmp_path / "workspace" / "results").mkdir()
        (tmp_path / "workspace" / relative).write_bytes(before.encode("utf-8"))
        page_id = _page(client, "PATCH_FULL_REPORT")
        action_id = _approve(PageExecution(client.app.state.pages.runtime), page_id)
        preview = client.get(f"/api/pages/{page_id}/artifacts/{action_id}").json()
        assert preview["state"] == "current" and preview["truncated"]
        download = client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download")
        assert download.status_code == 200, download.text
        assert download.content == after.encode("utf-8")
        assert len(download.content) > len(preview["content"].encode("utf-8"))
        assert download.headers["etag"] == '"' + _sha(after) + '"'
        assert download.headers["x-per-pulse-content-sha256"] == _sha(after)
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-length"] == str(len(after.encode("utf-8")))
        disposition = download.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="Caf_ 100_ __.txt";')
        assert "filename*=UTF-8''" + quote(filename, safe="") in disposition
        assert "results/" not in disposition
        assert download.headers["cache-control"] == "no-store"
        assert download.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/api/pages/home/artifacts/{action_id}/download").status_code == 404
    round_count = len(requests)
    with _client(tmp_path, model_factory=None) as client:
        download = client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download")
        assert download.status_code == 200 and download.content == after.encode("utf-8")
        assert len(requests) == round_count
        assert not client.app.state.pages.runtime.kernel_service._runtimes


def test_download_serves_verified_snapshot_without_reopening_a_replaced_path(tmp_path, monkeypatch):
    from pulse_system.product.files import WorkspaceFiles

    original = "The exact recorded file.\r\n"
    replacement = "A later writer's different file.\n"
    factory = _factory(monkeypatch, {"WRITE_REPORT": {
        "path": "report.txt", "expected_digest": None, "content": original,
    }}, [])
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        page_id = _page(client)
        action_id = _approve(PageExecution(client.app.state.pages.runtime), page_id)
        read = WorkspaceFiles.read

        def replace_after_read(files, path):
            snapshot = read(files, path)
            (tmp_path / "workspace" / path).write_text(replacement)
            return snapshot

        monkeypatch.setattr(WorkspaceFiles, "read", replace_after_read)
        downloaded = client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download")
        assert downloaded.status_code == 200 and downloaded.content == original.encode()
        assert (tmp_path / "workspace" / "report.txt").read_text() == replacement
        refused = client.get(f"/api/pages/{page_id}/artifacts/{action_id}/download")
        assert refused.status_code == 409 and replacement not in refused.text


def test_artifact_identity_survives_event_retention_and_list_bounds(tmp_path, monkeypatch):
    import per_pulse.artifacts as module

    requests = []
    factory = _factory(monkeypatch, {"WRITE_REPORT": {"path": "report.txt", "expected_digest": None, "content": "A report."}}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client)
        action_id = _approve(execution, page_id)
        store = runtime.kernel_service.store
        scope = store.action_scope_for_session(execution._scope(page_id), action_id)
        store.max_events_per_session = 8
        for index in range(12):
            store.append_event(EventDraft(scope=scope, event_type="test.retention", source="test", phase="test", status="succeeded", payload={"index": index}))
        assert not store.has_event(scope, "change.applied", action_request_id=action_id)
        assert artifacts.snapshot(page_id)["artifacts"][0]["id"] == action_id
        monkeypatch.setattr(module, "MAX_ARTIFACTS", 0)
        assert artifacts.snapshot(page_id)["artifacts"] == []
        assert artifacts.snapshot(page_id)["truncated"]
        assert artifacts.read(page_id, action_id)["content"] == "A report."


@pytest.mark.parametrize("body", ["x" * 50_000, '\\"\t' * 7_000, "💡" * 12_000], ids=["plain", "escaped", "multibyte"])
def test_large_native_diff_preserves_compact_receipt_artifact_and_continuation(tmp_path, monkeypatch, body):
    before = "DRAFT " + body + "\n"
    after = before.replace("DRAFT", "FINAL")
    requests = []
    factory = _factory(monkeypatch, {"PATCH_LARGE": {
        "path": "large.txt", "expected_digest": _sha(before),
        "patch": {"edits": [{"old": "DRAFT", "new": "FINAL"}]},
    }}, requests)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        target = tmp_path / "workspace" / "large.txt"
        target.write_text(before)
        runtime = client.app.state.pages.runtime
        execution, artifacts = PageExecution(runtime), PageArtifacts(runtime)
        page_id = _page(client, "PATCH_LARGE")
        waiting = _wait(execution, page_id, lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        execution.resolve_approval(page_id, action_id, "allow_once")
        _wait(execution, page_id, lambda view: view["status"] == "idle")
        assert target.read_text() == after
        snapshot = artifacts.snapshot(page_id)
        item, = snapshot["artifacts"]
        assert item["id"] == action_id and item["sha256"] == _sha(after)
        assert not snapshot["truncated"]
        assert artifacts.read(page_id, action_id)["content"] == after
        store = runtime.kernel_service.store
        action_scope = store.action_scope_for_session(execution._scope(page_id), action_id)
        action = store.get_action(action_scope, action_id)
        assert action.status.value == "succeeded"
        assert action.result.get("_truncated") is not True
        output = action.result["output"]
        assert output["diff_truncated"] and not output["diff_redacted"]
        assert output["before_digest"] == _sha(before) and output["after_digest"] == _sha(after)
        assert output["action_request_id"] == action_id
        assert output["operation_id"] == action.operation_id
        assert output["workspace_provenance"]["adapter"] == "pulse_harness.workspace.v1"
        delivered = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
        assert len(delivered) == 1
        assert len(delivered[0]["content"].encode()) <= store.max_payload_bytes
        assert json.loads(delivered[0]["content"])["output"] == output
        mtime = target.stat().st_mtime_ns
        request_count = len(requests)
        execution.resolve_approval(page_id, action_id, "allow_once")
        assert len(requests) == request_count and target.stat().st_mtime_ns == mtime

    with _client(tmp_path, model_factory=None, allow_workspace_actions=True) as client:
        artifacts = client.app.state.pages.artifacts
        assert artifacts.snapshot(page_id)["artifacts"] == [item]
        assert artifacts.read(page_id, action_id)["content"] == after
        assert target.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("body", ['A quoted line: "text" with a tab\tand slash \\\n' * 5_000,
                                  "A multibyte document: 💡 café.\n" * 5_000], ids=["escaped", "multibyte"])
def test_large_native_read_reaches_continuation_with_bounded_text_and_exact_digest(tmp_path, monkeypatch, body):
    requests = []
    factory = _factory(monkeypatch, {"READ_LARGE": {"path": "large.txt"}}, requests,
                       tool_name="read_file", tool_call_id="fixture-read-" * 250)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        target = tmp_path / "workspace" / "large.txt"
        target.write_text(body)
        runtime = client.app.state.pages.runtime
        execution = client.app.state.pages.execution
        page_id = _page(client, "READ_LARGE")
        _wait(execution, page_id, lambda view: len(requests) == 2 and view["status"] == "idle")
        delivered = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
        assert len(delivered) == 1
        assert len(delivered[0]["content"].encode()) <= runtime.kernel_service.store.max_payload_bytes
        result = json.loads(delivered[0]["content"])
        assert result["status"] == "succeeded"
        output = result["output"]
        assert output["path"] == "large.txt" and output["digest"] == _sha(body)
        assert output["truncated"] and not output["redacted"]
        assert output["content"].startswith("[TRUNCATED sha256=")
        assert output["workspace_provenance"]["adapter"] == "pulse_harness.workspace.v1"
        assert client.app.state.pages.artifacts.snapshot(page_id)["artifacts"] == []
        assert target.read_text() == body


def test_unrepresentable_receipt_metadata_rejects_before_the_file_effect(tmp_path, monkeypatch):
    requests = []
    factory = _factory(monkeypatch, {"WRITE_REPORT": {
        "path": "report.txt", "expected_digest": None, "content": "A short report.\n",
    }}, requests, tool_call_id="fixture-wide-" * 1_900)
    with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        execution = client.app.state.pages.execution
        page_id = _page(client)
        waiting = _wait(execution, page_id, lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        execution.resolve_approval(page_id, action_id, "allow_once")
        _wait(execution, page_id, lambda view: view["status"] == "idle")
        store = runtime.kernel_service.store
        scope = store.action_scope_for_session(execution._scope(page_id), action_id)
        action = store.get_action(scope, action_id)
        assert action.status.value == "failed" and action.error_code == "payload_too_large"
        assert action.operation_id is None
        assert not (tmp_path / "workspace" / "report.txt").exists()
        assert client.app.state.pages.artifacts.snapshot(page_id)["artifacts"] == []


def test_legacy_truncated_receipt_is_not_reconstructed_from_file_existence(tmp_path, monkeypatch):
    from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider, _safe_observer_text

    before = "DRAFT " + "x" * 50_000 + "\n"
    after = before.replace("DRAFT", "FINAL")
    requests = []
    factory = _factory(monkeypatch, {"PATCH_LEGACY": {
        "path": "legacy.txt", "expected_digest": _sha(before),
        "patch": {"edits": [{"old": "DRAFT", "new": "FINAL"}]},
    }}, requests)

    def old_text_only_bound(self, metadata, text, *, text_key, redacted_key, truncated_key, **_kwargs):
        # Explicit legacy adapter fixture: each text field fits, but its JSON
        # envelope exceeds the store bound and loses the full receipt identity.
        preview, redacted, truncated = _safe_observer_text(text, self.store.max_payload_bytes)
        return {**metadata, text_key: preview, redacted_key: redacted, truncated_key: truncated}

    with monkeypatch.context() as legacy:
        legacy.setattr(StructuredWorkspaceToolProvider, "_bounded_text_output", old_text_only_bound)
        with _client(tmp_path, model_factory=factory, allow_workspace_actions=True) as client:
            target = tmp_path / "workspace" / "legacy.txt"
            target.write_text(before)
            execution = client.app.state.pages.execution
            page_id = _page(client, "PATCH_LEGACY")
            waiting = _wait(execution, page_id, lambda view: bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
            action_id = waiting["pendingActions"][0]["actionId"]
            receipt = execution.resolve_approval(page_id, action_id, "allow_once")
            assert receipt["actionCompleted"]
            assert receipt["continuationError"]["code"] == "payload_too_large"
            assert target.read_text() == after

    with _client(tmp_path, model_factory=None, allow_workspace_actions=True) as client:
        artifacts = client.app.state.pages.artifacts
        snapshot = artifacts.snapshot(page_id)
        assert snapshot["artifacts"] == [] and snapshot["truncated"]
        with pytest.raises(ArtifactError):
            artifacts.read(page_id, action_id)
        assert target.read_text() == after
