"""Native interpretation revisions keep original observations and actual actors.

HTTP model decisions and controlled terminal states are explicit fixtures.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json

import pytest

from example_revision_fixture import ExampleRevisionFixture
from pulse_harness.core.contracts import ActionStatus, TurnStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_system.service.native_posture import NativePosture, validate
from test_native_posture import native, remember, settle
from test_pages import client_for, wait_for
from test_posture_examples import example
from test_reading_continuity import work
from test_reading_examples import choose


def revision(native, **extra):
    before = native.memory.add_experience(**example(
        subject_id=native.scope.engram_id, context_id=native.scope.context_id, scope="subject"))
    values = {"op": "revise", "experience_id": before["id"], "expected_revision": before["revision"],
              "changes": {"title": "A more precise example", "note": "Compare the first usable action with the current purpose."}, **extra}
    native.states.arguments = values
    return before, values


def test_own_interpretation_is_staged_and_attributed_without_feedback(native):
    before, values = revision(native)
    receipt = remember(native, values, action="refine-own-example")
    assert remember(native, values, action="refine-own-example") == receipt
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    settle(native)
    native.tool.drain()
    after = native.memory.get_experience(before["id"])
    assert after["revision"] == 2 and after["history"] == [dict((k, v) for k, v in before.items() if k != "history")]
    assert all(after[key] == before[key] for key in ("source", "encounter", "excerpt", "active", "scope", "feedback"))
    assert after["feedback"] == []
    assert all(after[key] == value for key, value in values["changes"].items())
    actor = after["revision_source"]
    assert actor["kind"] == "model_reinterpreted_example"
    assert actor["learning_turn_id"] == native.current.id
    assert actor["source_action_id"] == "refine-own-example" and actor["native_request_id"] == receipt["request_id"]
    assert actor["expected_revision"] == 1 and actor["selector_evidence_class"] == "TEST_DOUBLE"
    assert not {"feedback_message_id", "verdict", "evidence"} & set(actor)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == after
    local = native.memory.materials(subject_id=native.scope.engram_id, context_id="")["episodes"][0]
    assert local["revision_source"] == actor and local["source"]["evidence"] == before["source"]
    shared = native.memory.materials(subject_id=native.scope.engram_id, context_id="work:receiver")["episodes"][0]
    assert shared["note"] == values["changes"]["note"]
    assert shared["revision_source"] == {"kind": actor["kind"], "selector_evidence_class": "TEST_DOUBLE"}
    assert actor["native_request_id"] not in json.dumps(shared)


@pytest.mark.parametrize("extra", [
    {"changes": {"excerpt": "Rewritten source"}}, {"changes": {"source": {"kind": "user"}}},
    {"changes": {"lesson": "A claim requiring feedback"}}, {"changes": {"active": True}},
    {"changes": {"scope": "subject"}}, {"changes": {"encounter": {}}},
    {"revision_source": {"kind": "user"}}, {"feedback_message_id": ""},
])
def test_native_interpretation_arguments_cannot_rewrite_sources_or_invent_actor(extra):
    with pytest.raises(ValueError):
        validate({"op": "revise", "experience_id": "example", "expected_revision": 1,
                  "changes": {"note": "A new interpretation"}, **extra})


def test_lesson_title_still_requires_later_user_feedback(native):
    receipt = remember(native)
    settle(native)
    native.tool.drain()
    # Begin a new actual turn, then attempt a title-only lesson edit.
    event = native.runtime.inject(native.scope.engram_id, "Continue the work.")
    native.current = native.runtime.causal_ledger.begin_turn(event, native.scope.engram_id, "Continue the work.",
        execution_context="", runtime_fence=native.runtime._current_runtime_fence())
    native.scope = native.scope.for_turn(native.current.id)
    values = {"op": "revise", "experience_id": receipt["experience_id"], "expected_revision": 1,
              "changes": {"title": "An unsupported reinterpretation"}}
    with pytest.raises(ScopeConflict, match="own example"):
        remember(native, values)


@pytest.mark.parametrize("foreign", ["context", "subject"])
def test_native_interpretation_cannot_edit_shared_receiver_or_foreign_subject(native, foreign):
    before, values = revision(native)
    field = "context_id" if foreign == "context" else "subject_id"
    value = "work:other" if foreign == "context" else "other-subject"
    # Deliberately corrupt both the current row and its lookup column to
    # exercise the scope check at the new persistence boundary.
    connection = native.runtime.storage._conn
    connection.execute(f"UPDATE posture_experiences SET {field}=?,record=json_set(record,?,?) "
                       "WHERE world_id=? AND experience_id=?",
                       (value, "$." + field, value, native.runtime.world_id, before["id"]))
    connection.commit()
    with pytest.raises(ScopeConflict, match="this context's own"):
        remember(native, values)


def test_native_interpretation_respects_intervening_operator_revision(native):
    before, values = revision(native)
    receipt = remember(native, values)
    operator = native.memory.revise_experience(before["id"], expected_revision=1,
                                               changes={"note": "The operator's later interpretation"})
    settle(native)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == operator
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "blocked"


@pytest.mark.parametrize("field,value", [("turn", TurnStatus.CANCELLED), ("turn", TurnStatus.FAILED),
    ("action", ActionStatus.FAILED), ("name", "another_tool")])
def test_unsuccessful_interpretation_selector_does_not_change_memory(native, field, value):
    before, values = revision(native)
    receipt = remember(native, values)
    settle(native)
    setattr(native.states, field, value)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "cancelled"


def test_subject_cancellation_discards_pending_interpretation(native):
    before, values = revision(native)
    receipt = remember(native, values)
    assert native.tool.cancel_subject(native.scope.engram_id) == 1
    settle(native)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "cancelled"


@pytest.mark.parametrize("tamper", ["actor", "content"])
def test_staged_interpretation_provenance_and_content_must_match_native_action(native, tamper):
    before, values = revision(native)
    receipt = remember(native, values)
    stored = native.runtime.storage.load_component_state(receipt["request_id"])
    if tamper == "actor":
        stored["revision_update"]["source"]["learning_turn_id"] = "fabricated-turn"
    else:
        stored["revision_update"]["changes"]["note"] = "Different content"
    native.runtime.storage.save_component_state(receipt["request_id"], stored)
    settle(native)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    assert native.runtime.storage.load_component_state(receipt["request_id"])["error_code"] == "revision_identity_conflict"


@pytest.mark.parametrize("later_operator", [False, True])
def test_interpretation_receipt_crash_recovers_exact_revision_without_reapply(native, monkeypatch, later_operator):
    before, values = revision(native)
    receipt = remember(native, values)
    settle(native)
    monkeypatch.setattr(native.tool, "_finish", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("receipt crash")))
    with pytest.raises(RuntimeError, match="receipt crash"):
        native.tool.drain()
    applied = native.memory.get_experience(before["id"])
    if later_operator:
        applied = native.memory.revise_experience(before["id"], expected_revision=2, changes={"note": "Later operator text"})
        assert "revision_source" not in applied
        assert applied["history"][-1]["revision_source"]["native_request_id"] == receipt["request_id"]
    recovered = NativePosture(native.runtime)
    recovered.drain()
    recovered.drain()
    assert native.memory.get_experience(before["id"]) == applied
    assert native.runtime.storage.load_component_state(receipt["request_id"])["revision"] == 2


def test_receipt_recovery_rejects_a_different_applied_snapshot(native, monkeypatch):
    before, values = revision(native)
    receipt = remember(native, values)
    settle(native)
    monkeypatch.setattr(native.tool, "_finish", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("receipt crash")))
    with pytest.raises(RuntimeError, match="receipt crash"):
        native.tool.drain()
    connection = native.runtime.storage._conn
    connection.execute("UPDATE posture_experiences SET record=json_set(record,'$.note',?) "
                       "WHERE world_id=? AND experience_id=?",
                       ("Different applied content", native.runtime.world_id, before["id"]))
    connection.commit()
    recovered = NativePosture(native.runtime)
    recovered.drain()
    assert native.runtime.storage.load_component_state(receipt["request_id"])["error_code"] == "revision_identity_conflict"
    assert native.memory.get_experience(before["id"])["note"] == "Different applied content"


def quiet(client, page, fixture, command):
    fixture.quiet = command
    count = len(fixture.outcomes)
    assert client.post(f"/api/pages/{page}/tick", json={"count": 1}).status_code == 201
    wait_for(client, page, lambda view: view["tick"]["status"] == "complete", timeout=30)
    assert len(fixture.outcomes) == count + 1, json.dumps(fixture.requests[-1]["messages"][-3:])
    return fixture.outcomes[-1]


@pytest.mark.parametrize("succession", [False, True])
def test_quiet_native_reading_refinement_survives_restart_and_reaches_next_round_zero(tmp_path, monkeypatch, succession):
    fixture = ExampleRevisionFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        source, receiver = work(client), work(client)
        (tmp_path / "workspace" / "source.txt").write_text("Map complete source characters, then compare the selected span.")
        choose(client, source, fixture, op="open", path="source.txt", retain={"title": "Character spans",
            "excerpt": "complete source characters", "note": "Count folded character positions."})
        before, = client.get(f"/api/pages/{source}/posture").json()["experiences"]
        pages, runtime = client.app.state.pages, client.app.state.pages.runtime
        predecessor = runtime.continuity_engram_id
        if succession:
            prepared = runtime._mgr.prepare_succession(predecessor,
                parent_event_id=before["sourceAnchor"]["resultMessageId"], runtime_fence=runtime._current_runtime_fence())
            runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence())
        users_before = [m["id"] for m in client.get(f"/api/pages/{source}").json()["messages"] if m["role"] == "user"]
        corrected = "Map whole original characters before comparing offsets in a folded string."
        result = quiet(client, source, fixture, {"op": "revise", "experience_id": before["id"],
            "expected_revision": 1, "changes": {"note": corrected}})
        assert result[0]["status"] == "succeeded", result
        wait_for(client, source, lambda _: client.get(f"/api/pages/{source}/posture").json()["experiences"][0]["revision"] == 2)
        saved, = client.get(f"/api/pages/{source}/posture").json()["experiences"]
        assert saved["note"] == corrected and saved["feedback"] == []
        assert all(saved[key] == before[key] for key in ("source", "excerpt", "encounter", "sourceAnchor"))
        users_after = [m["id"] for m in client.get(f"/api/pages/{source}").json()["messages"] if m["role"] == "user"]
        assert users_after == users_before
        actor = saved["revisionSource"]
        assert actor["subject_id"] == runtime.continuity_engram_id
        if succession:
            assert actor["subject_id"] != predecessor
        else:
            assert actor["subject_id"] == predecessor
        assert actor["selector_evidence_class"] == "TEST_DOUBLE"
        action_scope = runtime.kernel_service.store.action_scope_for_session(
            runtime.harness.scope_for(runtime.continuity_engram_id, pages.learning._context(source)), actor["source_action_id"])
        assert action_scope.turn_id == actor["learning_turn_id"]
        assert runtime.kernel_service.store.get_action(action_scope, actor["source_action_id"]).status is ActionStatus.SUCCEEDED
        assert actor["resultMessageId"] != before["sourceAnchor"]["resultMessageId"]
        # The receiver has only the shared interpretation, and cannot revise it.
        rejected = choose(client, receiver, fixture, op="revise", experience_id=before["id"],
                          expected_revision=2, changes={"note": "Receiver rewrite"})
        assert rejected[0]["status"] == "failed"
        assert "revisionSource" not in client.get(f"/api/pages/{receiver}/posture").json()["experiences"][0]
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        assert client.get(f"/api/pages/{source}/posture").json()["experiences"] == [saved]
        at = len(fixture.before_action)
        quiet(client, source, fixture, {"op": "get", "experience_id": before["id"]})
        packet = fixture.before_action[at]
        assert packet["native_model_round"] == 0
        offered = next(e for e in packet["posture_experiences"]["episodes"] if e["id"] == saved["id"])
        assert offered["note"] == corrected and offered["revision"] == 2
        assert offered["source"]["evidence"] == before["source"]
        assert offered["revision_source"]["native_request_id"] == saved["revisionSource"]["native_request_id"]


def test_failed_quiet_selector_does_not_admit_a_prepared_refinement(tmp_path, monkeypatch):
    fixture = ExampleRevisionFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        page = work(client)
        (tmp_path / "workspace" / "source.txt").write_text("The original observation.")
        choose(client, page, fixture, op="open", path="source.txt",
               retain={"title": "Original", "excerpt": "original observation", "note": "Earlier interpretation"})
        before, = client.get(f"/api/pages/{page}/posture").json()["experiences"]
        fixture.quiet = {"op": "revise", "experience_id": before["id"], "expected_revision": 1,
                         "changes": {"note": "Not admitted from a failed selector"}}
        fixture.fail_after_prepare = True
        assert client.post(f"/api/pages/{page}/tick", json={"count": 1}).status_code == 201
        wait_for(client, page, lambda view: view["tick"]["status"] in {"stopped", "failed"}, timeout=30)
        assert fixture.prepared["output"]["status"] == "prepared"
        client.app.state.pages.runtime._native_posture.drain()
        assert client.get(f"/api/pages/{page}/posture").json()["experiences"] == [before]


def test_command_example_interpretation_can_change_without_replaying_the_command(tmp_path, monkeypatch):
    from command_example_fixture import CommandExampleFixture
    from command_output_fixture import LocalPipeCommandExecutor
    from pulse_system.service import kernel_runtime
    from test_command_examples import read_and_keep, run

    monkeypatch.setenv("P_PULSE_COMMAND_IMAGE", "explicit-pipe-fixture")
    monkeypatch.setattr(kernel_runtime, "DockerCommandExecutor", LocalPipeCommandExecutor)
    fixture = CommandExampleFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page = work(client)
        action = run(client, page)
        read_and_keep(client, page, fixture, action, note="A first interpretation of the failure.")
        before, = client.get(f"/api/pages/{page}/posture").json()["experiences"]
        outcome = quiet(client, page, fixture, {"op": "revise", "experience_id": before["id"],
            "expected_revision": 1, "changes": {"note": "Use the diagnostic to choose the next check, with exit code 7 in view."}})
        assert outcome[0]["status"] == "succeeded"
        wait_for(client, page, lambda _: client.get(f"/api/pages/{page}/posture").json()["experiences"][0]["revision"] == 2)
        after, = client.get(f"/api/pages/{page}/posture").json()["experiences"]
        assert after["source"] == before["source"] and after["excerpt"] == before["excerpt"]
        assert after["commandSource"] == before["commandSource"] == {"stream": "stderr", "exitCode": 7, "captureComplete": True}
        assert after["feedback"] == [] and after["revisionSource"]["selector_evidence_class"] == "TEST_DOUBLE"
        assert client.app.state.pages.runtime.harness._command_executor.calls == 1
