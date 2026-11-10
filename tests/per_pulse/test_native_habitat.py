"""Native Habitat region is shared by separate Home/Work histories in one World.

The model replies are a declared synthetic HTTP fixture. World, Kernel,
ManagedHabitat journal and filesystem are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib

import pytest

from per_pulse.service import PageService, PulseSettings
from per_pulse.execution import PageExecution
from test_execution import _wait
from test_pages import client_for, new_page, wait_for
from pulse_system.service.native_habitat import OBSERVE, WRITE, validate
from test_internal_work_organization import WorkFixture, run_event


@pytest.mark.parametrize("name,values", [
    (OBSERVE, {"op": "list", "region": "../home"}),
    (OBSERVE, {"op": "read", "region": "lab", "file": "../../secrets"}),
    (OBSERVE, {"op": "read", "region": "lab", "file": ".managed-state"}),
    (WRITE, {"op": "write", "region": "lab/../other", "file": "note.txt", "content": "x"}),
    (WRITE, {"op": "append", "region": "lab", "file": "a.txt", "content": "x\x00"}),
])
def test_region_cannot_escape_its_world_directory(name, values):
    with pytest.raises(ValueError):
        validate(name, values)


@pytest.mark.parametrize("auto_apply_edits", [False, True])
def test_two_distinct_works_revisit_one_persistent_region(tmp_path, monkeypatch, auto_apply_edits):
    """Both ordinary and auto-apply profiles retain the actual approval boundary.

    The action decisions are synthetic HTTP responses; permissions, physical
    files, World/Kernel state and reopened SQLite are real.
    """
    fixture = WorkFixture(monkeypatch.setenv)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0, check_interval_seconds=0.01,
        adaptive_connections=False, spectrum_enabled=False,
    )

    def open_client():
        return client_for(
            tmp_path, model_factory=fixture.factory,
            allow_workspace_actions=True, auto_apply_edits=auto_apply_edits,
            posture=False, pulse=settings,
        )

    def approve_once(client, page_id, target):
        execution = PageExecution(client.app.state.pages.runtime)
        waiting = _wait(execution, page_id, lambda view:
            bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action = waiting["pendingActions"][0]
        assert action["toolName"] == WRITE
        assert not target.exists() or target.read_text() == "An actual first observation.\n"
        receipt = execution.resolve_approval(page_id, action["actionId"], "allow_once")
        assert receipt["continuationError"] is None, receipt
        wait_for(client, page_id, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        return action["actionId"]

    with open_client() as client:
        pages = client.app.state.pages
        target = pages.runtime.habitat.root / "regions/garden/notes.txt"
        fixture.plan = [(WRITE, {
            "op": "write", "region": "garden", "file": "notes.txt",
            "content": "An actual first observation.\n",
        })]
        a = new_page(client, "Visit a place", "Make one real local observation.")["page"]["id"]
        action_a = approve_once(client, a, target)
        result = fixture.outcomes[0]
        assert result["status"] == "succeeded", result
        receipt = result["output"]["effectReceipt"]
        assert receipt["terminal_state"] == "committed"
        assert receipt["before_digest"] is None
        assert receipt["after_digest"] == hashlib.sha256(b"An actual first observation.\n").hexdigest()
        assert receipt["correlation_id"] == action_a
        assert target.read_text() == "An actual first observation.\n"

        fixture.plan = [(OBSERVE, {"op": "read", "region": "garden", "file": "notes.txt"})]
        b = new_page(client, "Return to the place", "Read what is already there.")["page"]["id"]
        wait_for(client, b, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        assert fixture.outcomes[0]["status"] == "succeeded"
        assert fixture.outcomes[0]["output"]["current"] == "An actual first observation.\n"
        assert fixture.outcomes[0]["output"]["lastVerifiedWrite"]["effectReceipt"] == receipt
        assert fixture.outcomes[0]["output"]["lastVerifiedWrite"]["source"]["contextId"].startswith("work:")
        assert fixture.outcomes[0]["output"]["lastVerifiedWrite"]["source"]["kernelActionId"] == action_a
        assert len(pages.runtime.storage.list_engrams()) == 1

    with open_client() as client:
        pages = client.app.state.pages
        target = pages.runtime.habitat.root / "regions/garden/notes.txt"
        fixture.plan = [(WRITE, {
            "op": "append", "region": "garden", "file": "notes.txt",
            "content": "A second observation.\n",
        })]
        response = client.post(f"/api/pages/{b}/messages", json={
            "content": "Continue the shared regional observation."})
        assert response.status_code == 202, response.text
        action_b = approve_once(client, b, target)
        answer = fixture.outcomes[0]
        assert answer["status"] == "succeeded", answer
        after = "An actual first observation.\nA second observation.\n"
        assert target.read_text() == after
        assert answer["output"]["effectReceipt"]["correlation_id"] == action_b
        assert answer["output"]["effectReceipt"]["before_digest"] == receipt["after_digest"]
        assert answer["output"]["effectReceipt"]["after_digest"] == hashlib.sha256(after.encode()).hexdigest()
        assert answer["output"]["writerIndex"] == "recorded"
        assert len(pages.runtime.storage.list_engrams()) == 1



def test_long_region_keeps_original_writer_after_restart_and_exposes_external_edits(
        tmp_path, monkeypatch):
    """A bounded preview never erases full-file provenance or invents a writer."""
    fixture = WorkFixture(monkeypatch.setenv)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0,
        adaptive_connections=False, spectrum_enabled=False,
    )
    first, second = "A" * 48_000, "B" * 48_000

    def open_client():
        return client_for(
            tmp_path, model_factory=fixture.factory,
            allow_workspace_actions=True, auto_apply_edits=True,
            posture=False, pulse=settings,
        )

    def write_in_work(client, instruction, op, content):
        fixture.plan = [(WRITE, {
            "op": op, "region": "garden", "file": "large.txt",
            "content": content,
        })]
        page = new_page(client, "Regional " + op, instruction)["page"]["id"]
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        waiting = _wait(execution, page, lambda view:
            bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action = waiting["pendingActions"][0]
        assert action["toolName"] == WRITE
        target = runtime.habitat.root / "regions/garden/large.txt"
        assert (target.read_text() if target.exists() else "") == (
            first if op == "append" else "")
        decision = execution.resolve_approval(page, action["actionId"], "allow_once")
        assert decision["continuationError"] is None, decision
        wait_for(client, page, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        assert fixture.outcomes[0]["status"] == "succeeded"
        return action["actionId"], fixture.outcomes[0]["output"]["effectReceipt"]

    with open_client() as client:
        first_action, _ = write_in_work(client, "First large visit.", "write", first)
        second_action, second_receipt = write_in_work(
            client, "Append in a separate Work.", "append", second)
        assert first_action != second_action
        target = client.app.state.pages.runtime.habitat.root / "regions/garden/large.txt"
        assert target.read_text() == first + second
        assert second_receipt["after_digest"] == hashlib.sha256((first + second).encode()).hexdigest()

    with open_client() as client:
        fixture.plan = [(OBSERVE, {
            "op": "read", "region": "garden", "file": "large.txt",
        })]
        response = client.post("/api/pages/home/messages", json={
            "content": "Read the full region source after restart."})
        assert response.status_code == 202, response.text
        wait_for(client, "home", lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        answer = fixture.outcomes[0]["output"]
        assert answer["current"].startswith(first)
        assert "内容已截断" in answer["current"]
        assert answer["fullCurrentSha256"] == second_receipt["after_digest"]
        assert answer["lastVerifiedWrite"]["source"]["kernelActionId"] == second_action
        assert answer["lastVerifiedWrite"]["effectReceipt"] == second_receipt
        assert answer["readOffset"] == 0
        assert answer["totalChars"] == 96_000
        assert answer["nextOffset"] == 64_000

        # Home continues the actual file beyond the truncated first page,
        # preserving the original Work B source across the restart.
        fixture.plan = [(OBSERVE, {
            "op": "read", "region": "garden", "file": "large.txt",
            "offset": answer["nextOffset"],
            "expectedSha256": answer["fullCurrentSha256"],
        })]
        response = client.post("/api/pages/home/messages", json={
            "content": "Continue reading the same regional file."})
        assert response.status_code == 202, response.text
        wait_for(client, "home", lambda view: len([
            m for m in view["messages"]
            if m["role"] == "assistant" and m["status"] == "settled"
        ]) >= 2)
        continuation = fixture.outcomes[0]["output"]
        assert continuation["current"] == second[16_000:]
        assert continuation["readOffset"] == 64_000
        assert continuation["totalChars"] == 96_000
        assert continuation["nextOffset"] is None
        assert continuation["fullCurrentSha256"] == second_receipt["after_digest"]
        assert continuation["lastVerifiedWrite"]["source"]["kernelActionId"] == second_action

        # An out-of-band edit invalidates that same author's verified origin.
        target = client.app.state.pages.runtime.habitat.root / "regions/garden/large.txt"
        target.write_text(first + second + "externally modified", encoding="utf-8")
        with pytest.raises(ValueError, match="changed"):
            client.app.state.pages.runtime.habitat.text_page_snapshot(
                "regions/garden/large.txt", offset=64_000,
                expected_digest=second_receipt["after_digest"])
        fixture.plan = [(OBSERVE, {
            "op": "read", "region": "garden", "file": "large.txt",
        })]
        response = client.post("/api/pages/home/messages", json={
            "content": "Read after another actor changed the region."})
        assert response.status_code == 202, response.text
        wait_for(client, "home", lambda view: len([
            m for m in view["messages"]
            if m["role"] == "assistant" and m["status"] == "settled"
        ]) >= 3)
        changed = fixture.outcomes[0]["output"]
        assert changed["lastVerifiedWrite"] is None
        assert changed["fullCurrentSha256"] != second_receipt["after_digest"]


@pytest.mark.parametrize("op", ["write", "append"])
def test_habitat_rechecks_observed_version_at_approval(tmp_path, monkeypatch, op):
    """A real delayed Kernel approval must not overwrite a newer regional file.

    Only model tool selections are synthetic. The file, Work histories,
    original approval, refusal and later successful write are native.
    """
    fixture = WorkFixture(monkeypatch.setenv)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0,
        adaptive_connections=False, spectrum_enabled=False,
    )
    with client_for(tmp_path, model_factory=fixture.factory,
                    allow_workspace_actions=True, auto_apply_edits=True,
                    posture=False, pulse=settings) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        target = runtime.habitat.root / "regions/garden/notes.txt"

        fixture.plan = [(WRITE, {"op": "write", "region": "garden",
                                "file": "notes.txt", "content": "First version.\n"})]
        original = new_page(client, "Original observation", "Write the first note.")["page"]["id"]
        waiting = _wait(execution, original, lambda view:
                        bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        execution.resolve_approval(original, waiting["pendingActions"][0]["actionId"], "allow_once")
        wait_for(client, original, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled" for m in view["messages"]))
        assert target.read_text() == "First version.\n"

        fixture.plan = [(OBSERVE, {"op": "read", "region": "garden", "file": "notes.txt"})]
        reader = new_page(client, "Read current version", "Inspect the shared note.")["page"]["id"]
        wait_for(client, reader, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled" for m in view["messages"]))
        observed_digest = fixture.outcomes[0]["output"]["fullCurrentSha256"]

        fixture.plan = [(WRITE, {"op": op, "region": "garden",
                                "file": "notes.txt", "content": "Edited version.\n",
                                "expectedSha256": observed_digest})]
        editor = new_page(client, "Delayed revision", "Edit the note I inspected.")["page"]["id"]
        waiting = _wait(execution, editor, lambda view:
                        bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        assert waiting["pendingActions"][0]["arguments"]["expectedSha256"] == observed_digest
        # Another actor updates the same physical file while this Work waits
        # for its original approval. No stage or writer index may be added.
        target.write_text("Newer independent version.\n", encoding="utf-8")
        receipt = execution.resolve_approval(editor, action_id, "allow_once")
        assert receipt["accepted"], receipt
        wait_for(client, editor, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled" for m in view["messages"]))
        assert target.read_text() == "Newer independent version.\n"
        assert fixture.outcomes[0]["status"] != "succeeded", fixture.outcomes[0]
        assert execution.resolve_approval(editor, action_id, "allow_once")["repeated"]
        assert target.read_text() == "Newer independent version.\n"

        latest = hashlib.sha256(target.read_bytes()).hexdigest()
        fixture.plan = [(WRITE, {"op": op, "region": "garden",
                                "file": "notes.txt", "content": "Edited version.\n",
                                "expectedSha256": latest})]
        next_work = new_page(client, "Rebase onto latest", "Continue using the current note.")["page"]["id"]
        next_wait = _wait(execution, next_work, lambda view:
                          bool(view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        execution.resolve_approval(next_work, next_wait["pendingActions"][0]["actionId"], "allow_once")
        wait_for(client, next_work, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled" for m in view["messages"]))
        assert fixture.outcomes[0]["status"] == "succeeded", fixture.outcomes[0]
        assert fixture.outcomes[0]["output"]["effectReceipt"]["before_digest"] == latest
        assert target.read_text() == (
            "Edited version.\n" if op == "write"
            else "Newer independent version.\nEdited version.\n"
        )


@pytest.mark.parametrize("bad", ["", "A" * 64, "abcd", None, 7, True])
def test_habitat_edit_rejects_invalid_expected_sha(bad):
    with pytest.raises(ValueError, match="expectedSha256"):
        validate(WRITE, {"op": "write", "region": "garden", "file": "note.txt",
                         "content": "text", "expectedSha256": bad})


@pytest.mark.parametrize("arguments", [
    {"op": "read", "region": "garden", "file": "notes.txt", "offset": 64_000},
    {"op": "read", "region": "garden", "file": "notes.txt", "offset": -1},
    {"op": "read", "region": "garden", "file": "notes.txt", "offset": True},
    {"op": "list", "region": "garden", "offset": 64_000},
    {"op": "read", "region": "garden", "file": "notes.txt",
     "offset": 64_000, "expectedSha256": "not-a-digest"},
])
def test_habitat_continuation_rejects_unpinned_or_invalid_pages(arguments):
    with pytest.raises(ValueError):
        validate(OBSERVE, arguments)


def test_read_only_workspace_never_advertises_habitat_mutation(tmp_path):
    pages = PageService(tmp_path, model_factory=None, posture=False,
                        allow_workspace_actions=False)
    try:
        from pulse_system.service.native_habitat import descriptors
        assert any(d.name == WRITE and d.mutable and d.requires_approval
                   for d in descriptors())
        assert pages.runtime.harness._allow_mutations is False
        # A direct call cannot bypass the front-end's read-only configuration.
        from pulse_system.service.native_habitat import NativeHabitat
        from pulse_harness.core.contracts import ToolCall
        from pulse_harness.core.provider import CancellationToken
        from pulse_harness.core.errors import ScopeConflict
        from pulse_harness.core import HarnessScope
        scope = HarnessScope(pages.runtime.world_id,
                             pages.runtime.continuity_engram_id,
                             "untrusted-session", 0, "untrusted-owner",
                             context_id="", turn_id="untrusted-turn")
        with pytest.raises(ScopeConflict, match="disabled"):
            NativeHabitat(pages.runtime).execute(
                scope, ToolCall("x", WRITE, {"op":"write", "region":"garden",
                     "file":"notes.txt", "content":"not allowed"}),
                CancellationToken(), action_request_id="untrusted-action",
            )
        assert not (pages.runtime.habitat.root / "regions/garden/notes.txt").exists()
    finally:
        pages.runtime.close()


@pytest.mark.parametrize("decision", ["deny", "allow_once"])
@pytest.mark.parametrize("auto_apply_edits", [False, True])
def test_habitat_write_waits_for_real_native_approval(tmp_path, monkeypatch, decision, auto_apply_edits):
    """A model tool call cannot write before the user's decision is recorded.

    Only the HTTP model decision is synthetic. The page, original Kernel action,
    approval transaction, filesystem effect, and later model continuation are real.
    """
    fixture = WorkFixture(monkeypatch.setenv)
    fixture.plan = [(WRITE, {
        "op": "write", "region": "garden", "file": "approved.txt",
        "content": "Only if the owner allows once.\\n",
    })]
    with client_for(tmp_path, model_factory=fixture.factory,
                    allow_workspace_actions=True, auto_apply_edits=auto_apply_edits,
                    posture=False, pulse=PulseSettings(
                        enabled=True, spontaneous_rate=0,
                        adaptive_connections=False, spectrum_enabled=False)) as client:
        runtime = client.app.state.pages.runtime
        execution = PageExecution(runtime)
        page = new_page(client, "Ask before changing the garden",
                        "Write the new garden note only when approved.")["page"]["id"]
        target = runtime.habitat.root / "regions" / "garden" / "approved.txt"
        waiting = _wait(execution, page,
            lambda view: bool(view["pendingActions"])
            and view["pendingActions"][0]["canResolve"])
        action_id = waiting["pendingActions"][0]["actionId"]
        assert waiting["pendingActions"][0]["toolName"] == WRITE
        assert not target.exists(), "approval pending must not write any file"
        scope = runtime.kernel_service.store.action_scope_for_session(
            execution._scope(page), action_id)
        assert runtime.kernel_service.store.get_action(scope, action_id).approval_id
        receipt = execution.resolve_approval(page, action_id, decision)
        assert receipt["continuationError"] is None, receipt
        wait_for(client, page, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        assert target.exists() is (decision == "allow_once")
        if decision == "allow_once":
            assert target.read_text() == "Only if the owner allows once.\\n"
        assert execution.resolve_approval(page, action_id, decision)["repeated"]
        assert target.exists() is (decision == "allow_once")


@pytest.mark.parametrize("name,values", [
    (OBSERVE, {"op": "read", "file": "notes.txt"}),
    (OBSERVE, {"op": "list", "region": None}),
    (OBSERVE, {"op": "list", "file": "notes.txt"}),
    (WRITE, {"op": "write", "file": "notes.txt", "content": "x"}),
])
def test_regionless_discovery_does_not_relax_other_habitat_paths(name, values):
    with pytest.raises(ValueError):
        validate(name, values)


def test_later_work_discovers_existing_regions_without_knowing_their_names(tmp_path, monkeypatch):
    """Region discovery travels through the real World after a process restart.

    The model tool selections are synthetic; native Home/Work histories,
    Kernel approval, SQLite and the physical Habitat are real.
    """
    fixture = WorkFixture(monkeypatch.setenv)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0,
        adaptive_connections=False, spectrum_enabled=False,
    )

    def open_client():
        return client_for(
            tmp_path, model_factory=fixture.factory,
            allow_workspace_actions=True, auto_apply_edits=True,
            posture=False, pulse=settings,
        )

    with open_client() as client:
        fixture.plan = [(OBSERVE, {"op": "list"})]
        response = client.post("/api/pages/home/messages", json={
            "content": "Find any shared working regions.",
        })
        assert response.status_code == 202, response.text
        wait_for(client, "home", lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        empty = fixture.outcomes[0]
        assert empty["status"] == "succeeded", empty
        assert empty["output"]["current"] == "No Habitat regions yet."
        assert empty["output"]["region"] is None

        fixture.plan = [(WRITE, {
            "op": "write", "region": "garden",
            "file": "notes.txt", "content": "A persistent observation.\n",
        })]
        first = new_page(client, "First visit", "Keep a note in the garden.")["page"]["id"]
        execution = PageExecution(client.app.state.pages.runtime)
        pending = _wait(execution, first, lambda view: bool(
            view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action = pending["pendingActions"][0]
        assert action["toolName"] == WRITE
        decision = execution.resolve_approval(first, action["actionId"], "allow_once")
        assert decision["continuationError"] is None, decision
        wait_for(client, first, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        assert (client.app.state.pages.runtime.habitat.root /
                "regions/garden/notes.txt").read_text() == "A persistent observation.\n"

    with open_client() as client:
        fixture.plan = [(OBSERVE, {"op": "list"})]
        later = new_page(
            client, "Unfamiliar revisit",
            "Discover earlier shared places without being given a region name.",
        )["page"]["id"]
        wait_for(client, later, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        discovered = fixture.outcomes[0]
        assert discovered["status"] == "succeeded", discovered
        assert discovered["output"]["path"] == "regions"
        assert discovered["output"]["region"] is None
        assert "garden/" in discovered["output"]["current"]

        fixture.plan = [(OBSERVE, {
            "op": "read", "region": "garden", "file": "notes.txt",
        })]
        response = client.post(f"/api/pages/{later}/messages", json={
            "content": "Visit the shared region discovered in the previous result.",
        })
        assert response.status_code == 202, response.text
        wait_for(client, later, lambda view: len([
            m for m in view["messages"]
            if m["role"] == "assistant" and m["status"] == "settled"
        ]) >= 2)
        result = fixture.outcomes[0]
        assert result["status"] == "succeeded", result
        assert result["output"]["current"] == "A persistent observation.\n"
        assert result["output"]["lastVerifiedWrite"]["source"]["contextId"].startswith("work:")



@pytest.mark.parametrize("values", [
    {"op": "find"},
    {"op": "find", "query": " "},
    {"op": "find", "query": "x", "file": "notes.txt"},
    {"op": "find", "query": "notes", "after": "../outside.txt"},
    {"op": "find", "query": "notes", "region": "garden", "after": "other/notes.txt"},
])
def test_habitat_find_requires_bounded_query_and_owned_cursor(values):
    with pytest.raises(ValueError):
        validate(OBSERVE, values)


def test_later_work_finds_attributed_text_beyond_first_habitat_page(tmp_path, monkeypatch):
    """Search old regional material without knowing its region or file first.

    The model's HTTP tool choices are synthetic; both actual Work writes,
    original Kernel approvals, durable World and restart are real.
    """
    fixture = WorkFixture(monkeypatch.setenv)
    settings = PulseSettings(
        enabled=True, spontaneous_rate=0,
        adaptive_connections=False, spectrum_enabled=False,
    )

    def open_client():
        return client_for(
            tmp_path, model_factory=fixture.factory,
            allow_workspace_actions=True, auto_apply_edits=True,
            posture=False, pulse=settings,
        )

    def approved_write(client, op, content):
        fixture.plan = [(WRITE, {
            "op": op, "region": "garden", "file": "journey.txt", "content": content,
        })]
        page = new_page(client, "Habitat " + op, "Record a regional experience.")["page"]["id"]
        execution = PageExecution(client.app.state.pages.runtime)
        pending = _wait(execution, page, lambda view: bool(
            view["pendingActions"]) and view["pendingActions"][0]["canResolve"])
        action = pending["pendingActions"][0]
        assert action["toolName"] == WRITE
        receipt = execution.resolve_approval(page, action["actionId"], "allow_once")
        assert receipt["continuationError"] is None, receipt
        wait_for(client, page, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        assert fixture.outcomes[0]["status"] == "succeeded"
        return action["actionId"]

    with open_client() as client:
        approved_write(client, "write", "a" * 48_000)
        last_action = approved_write(client, "append", "b" * 20_000 + "migration outcome: succeeded\n")
        target = client.app.state.pages.runtime.habitat.root / "regions/garden/journey.txt"
        assert target.stat().st_size > 64_000
        full_sha = hashlib.sha256(target.read_bytes()).hexdigest()

    with open_client() as client:
        fixture.plan = [(OBSERVE, {"op": "find", "query": "MIGRATION OUTCOME"})]
        later = new_page(
            client, "Unfamiliar regional search",
            "Find the earlier migration outcome without its file name.",
        )["page"]["id"]
        wait_for(client, later, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        found = fixture.outcomes[0]
        assert found["status"] == "succeeded", found
        output = found["output"]
        assert output["searchIncomplete"] is False
        assert output["nextCursor"] is None
        assert output["scannedFiles"] == 1
        assert len(output["matches"]) == 1
        match = output["matches"][0]
        assert match["path"] == "regions/garden/journey.txt"
        assert match["readOffset"] == 68_000
        assert "migration outcome: succeeded" in match["excerpt"]
        assert match["fullCurrentSha256"] == full_sha
        assert match["lastVerifiedWrite"]["source"]["kernelActionId"] == last_action
        assert match["lastVerifiedWrite"]["source"]["contextId"].startswith("work:")

        # The real original read tool can expand the matching text by its
        # provided offset and pinned version, without a separate search store.
        fixture.plan = [(OBSERVE, {
            "op": "read", "region": "garden", "file": "journey.txt",
            "offset": match["readOffset"],
            "expectedSha256": match["fullCurrentSha256"],
        })]
        response = client.post(f"/api/pages/{later}/messages", json={
            "content": "Read the attributed passage and continue from it.",
        })
        assert response.status_code == 202, response.text
        wait_for(client, later, lambda view: len([
            m for m in view["messages"]
            if m["role"] == "assistant" and m["status"] == "settled"
        ]) >= 2)
        assert fixture.outcomes[0]["output"]["current"].startswith(
            "migration outcome: succeeded\n")


def test_habitat_find_cursor_and_incomplete_search_are_explicit(tmp_path, monkeypatch):
    """More files than one bounded search never silently yield 'no results'."""
    fixture = WorkFixture(monkeypatch.setenv)
    fixture.plan = [(OBSERVE, {"op": "find", "query": "later target"})]
    with client_for(
        tmp_path, model_factory=fixture.factory, allow_workspace_actions=True,
        posture=False, pulse=PulseSettings(enabled=True, spontaneous_rate=0,
            adaptive_connections=False, spectrum_enabled=False),
    ) as client:
        root = client.app.state.pages.runtime.habitat.root / "regions/garden"
        root.mkdir(parents=True, exist_ok=True)
        for index in range(25):
            (root / f"notes{index:02d}.txt").write_text(
                "later target\n" if index == 24 else "unrelated\n")
        page = new_page(client, "Search first pass", "Find previous related notes.")["page"]["id"]
        wait_for(client, page, lambda view: any(
            m["role"] == "assistant" and m["status"] == "settled"
            for m in view["messages"]))
        first = fixture.outcomes[0]["output"]
        assert first["matches"] == []
        assert first["scannedFiles"] == 24
        assert first["nextCursor"] == "garden/notes23.txt"
        assert first["searchIncomplete"] is True

        fixture.plan = [(OBSERVE, {"op": "find", "query": "later target",
                                  "after": first["nextCursor"]})]
        response = client.post(f"/api/pages/{page}/messages", json={
            "content": "Continue the same bounded regional search.",
        })
        assert response.status_code == 202, response.text
        wait_for(client, page, lambda view: len([
            m for m in view["messages"]
            if m["role"] == "assistant" and m["status"] == "settled"
        ]) >= 2)
        second = fixture.outcomes[0]["output"]
        assert second["searchIncomplete"] is False
        assert second["nextCursor"] is None
        assert second["matches"][0]["file"] == "notes24.txt"
        # These setup files were external, not original Kernel writes.
        assert second["matches"][0]["lastVerifiedWrite"] is None
