"""Native learning admission over real World events and component persistence.

Kernel terminal states in these focused tests are controlled fixtures. End-to-end
provider tests cover the actual Kernel tool boundary separately. No test here
contacts a model or treats a constructed lesson as measured capability.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from per_pulse.service import PageService
from pulse_harness.core import HarnessScope
from pulse_harness.core.contracts import ActionStatus, ToolCall, TurnStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.provider import CancellationToken
from pulse_system.core.types import HarnessTurnState
from pulse_system.service.native_posture import NativePosture, capture_encounter, validate
from pulse_system.service.posture import PostureMemory


@pytest.fixture
def native(tmp_path, monkeypatch):
    service = PageService(tmp_path, model_factory=None, posture=True)
    runtime = service.runtime
    subject = runtime.continuity_engram_id
    fence = runtime._current_runtime_fence
    original_input = runtime.inject(subject, "Build a product a visitor can try immediately.")
    original_turn = runtime.causal_ledger.begin_turn(
        original_input, subject, "Build a product a visitor can try immediately.",
        execution_context="", runtime_fence=fence(),
    )
    _, original = runtime.causal_ledger.settle_turn(
        original_turn.id, "The first screen is a twelve-field intake form.", runtime_fence=fence(),
        metadata={"evidence_class": "TEST_DOUBLE"},
    )
    feedback = runtime.inject(subject, "Let me try the interaction before asking for a profile.")
    current = runtime.causal_ledger.begin_turn(
        feedback, subject, "Let me try the interaction before asking for a profile.",
        execution_context="", runtime_fence=fence(),
    )
    scope = HarnessScope(runtime.world_id, subject, "synthetic-session", 0, "synthetic-owner",
                         context_id="", turn_id=current.id)
    values = {
        "op": "remember", "source_message_id": original.id, "feedback_message_id": feedback,
        "title": "Let the first interaction deliver value",
        "lesson": "Offer a usable attempt before optional personalization when experience is the purpose.",
        "conditions": {"all": ["An interactive experience is requested", {"not": "Registration is the purpose"}]},
        "counterexamples": ["A fixed attendance register should remain a simple form."],
        "shift": {"understanding": "Find the value of the first interaction.",
                  "candidates": "Build a usable attempt alongside the form candidate.",
                  "delivery": "Check the visitor can actually try it."},
    }
    memory = runtime.harness.posture_memory or PostureMemory(runtime.storage, runtime.world_id,
                                                            guard=runtime._require_runtime_owner)
    monkeypatch.setattr(runtime.harness, "posture_memory", memory)
    admissions = []
    monkeypatch.setattr(runtime.harness, "authorize_posture",
                        lambda s, action, call: admissions.append((s, action, call)), raising=False)
    states = SimpleNamespace(turn=TurnStatus.SUCCEEDED, action=ActionStatus.SUCCEEDED,
                             name="posture", arguments=values)
    monkeypatch.setattr(runtime.kernel_service.store, "get_turn",
                        lambda s: SimpleNamespace(status=states.turn))
    monkeypatch.setattr(runtime.kernel_service.store, "get_action", lambda s, identity: SimpleNamespace(
        status=states.action, tool_name=states.name, arguments=states.arguments,
    ))
    tool = NativePosture(runtime)
    env = SimpleNamespace(runtime=runtime, memory=memory, scope=scope, values=values,
                          tool=tool, states=states, admissions=admissions,
                          original=original, feedback=feedback, current=current)
    yield env
    runtime.close()


def remember(env, values=None, action="synthetic-action"):
    return env.tool.execute(env.scope, ToolCall("call", "posture", values or env.values),
                            CancellationToken(), action_request_id=action)


def settle(env):
    env.runtime.causal_ledger.settle_turn(
        env.current.id, "I changed the first interaction and retained the relevant lesson.",
        runtime_fence=env.runtime._current_runtime_fence(),
        metadata={"evidence_class": "TEST_DOUBLE"},
    )


def test_actual_encounter_is_host_captured_and_only_admitted_after_settlement(native):
    receipt = remember(native)
    assert receipt["status"] == "prepared" and receipt["revision"] is None
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []
    native.tool.drain()
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []
    settle(native)
    native.tool.drain()
    record = native.memory.get_experience(receipt["experience_id"])
    assert record["encounter"] == {
        "situation": "Build a product a visitor can try immediately.",
        "action": "The first screen is a twelve-field intake form.",
        "outcome": "Let me try the interaction before asking for a profile.",
    }
    assert record["scope"] == "context" and record["revision"] == 1
    assert record["source"]["source_action_id"] == "synthetic-action"
    assert record["source"]["learning_turn_id"] == native.current.id
    assert record["source"]["source_message_id"] == native.original.id
    assert record["feedback"] == []  # Saving does not award usefulness.
    saved = native.runtime.storage.load_component_state(receipt["request_id"])
    assert saved["status"] == "remembered" and saved["revision"] == 1
    native.tool.drain()
    assert len(native.memory.list_experiences(subject_id=native.scope.engram_id)) == 1


def test_same_action_replay_is_idempotent_and_changed_arguments_are_rejected(native):
    first = remember(native)
    assert remember(native) == first
    modified = {**native.values, "lesson": "A different lesson cannot reuse this action."}
    with pytest.raises(ScopeConflict, match="replay arguments"):
        remember(native, modified)
    assert len(native.tool._requests(pending=True)) == 1


@pytest.mark.parametrize("field,value", [
    ("turn", TurnStatus.CANCELLED), ("turn", TurnStatus.FAILED),
    ("action", ActionStatus.FAILED), ("name", "some_other_tool"),
])
def test_failed_or_wrong_native_origin_never_becomes_learning(native, field, value):
    receipt = remember(native)
    settle(native)
    setattr(native.states, field, value)
    native.tool.drain()
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "cancelled"


def test_native_argument_tampering_is_not_admitted(native):
    receipt = remember(native)
    settle(native)
    native.states.arguments = {**native.values, "title": "Changed after the staged action"}
    native.tool.drain()
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []
    assert native.runtime.storage.load_component_state(receipt["request_id"])["error_code"] == "native_action_not_settled"


def test_archived_subject_without_committed_continuation_does_not_learn(native):
    receipt = remember(native)
    settle(native)
    native.runtime.storage.archive_engram(native.scope.engram_id)
    native.tool.drain()
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []
    assert native.runtime.storage.load_component_state(receipt["request_id"])["error_code"] == "subject_inactive"


def test_crash_between_memory_and_receipt_write_recovers_without_duplicate(native, monkeypatch):
    receipt = remember(native)
    settle(native)
    finish = native.tool._finish
    monkeypatch.setattr(native.tool, "_finish", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash fixture")))
    with pytest.raises(RuntimeError, match="crash fixture"):
        native.tool.drain()
    assert len(native.memory.list_experiences(subject_id=native.scope.engram_id)) == 1
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "prepared"
    monkeypatch.setattr(native.tool, "_finish", finish)
    native.tool.drain()
    assert len(native.memory.list_experiences(subject_id=native.scope.engram_id)) == 1
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "remembered"


def test_sources_cannot_be_fabricated_reversed_or_selected_from_another_context(native):
    r, s = native.runtime, native.scope
    with pytest.raises(ScopeConflict):
        capture_encounter(r, s.engram_id, "work:another-context", native.original.id, native.feedback)
    with pytest.raises(ScopeConflict):
        capture_encounter(r, "another-subject", "", native.original.id, native.feedback)
    with pytest.raises(ScopeConflict):
        capture_encounter(r, s.engram_id, "", native.feedback, native.original.id)
    with pytest.raises(ScopeConflict):
        capture_encounter(r, s.engram_id, "", native.original.id, "invented-feedback")
    with pytest.raises(ScopeConflict, match="available"):
        capture_encounter(r, s.engram_id, "", native.original.id, native.feedback,
                          latest_input_id=native.original.id)
    assert native.tool._requests(pending=True) == []


def test_list_returns_current_message_ids_and_scoped_material(native):
    result = native.tool.execute(native.scope, ToolCall("list-call", "posture", {"op": "list"}),
                                 CancellationToken(), action_request_id="list-action")
    assert result["experiences"] == []
    sources = {record["message_id"]: record for record in result["recent_sources"]}
    assert sources[native.original.id]["kind"] == "assistant_result"
    assert sources[native.feedback]["source"] == "user"
    assert len(native.admissions) == 1
    assert native.tool._requests() == []



def test_native_lookup_finds_older_lesson_without_cross_context_or_raw_transfer(native):
    """A long-lived subject can revisit an old lesson after the 8-item tool window."""
    from pulse_system.service.native_posture import validate

    def add(identity, title, *, context="", scope="context"):
        return native.memory.add_experience(
            experience_id=identity, subject_id=native.scope.engram_id,
            context_id=context, scope=scope, record_type="constructed_fixture",
            title=title, lesson="Use concrete results when an experience is requested.",
            conditions={"all": ["The task needs an actual usable experience"]},
            counterexamples=["A fixed record-keeping form can be sufficient."],
            shift={"understanding": "Identify the purpose.",
                   "candidates": "Consider an immediate demo.",
                   "delivery": "Check the result."},
            encounter={"situation": "A specific request", "action": "A first attempt",
                       "outcome": "The user responded"},
            source={"kind": "fixture_for_lookup_privacy"},
        )

    old = add("old-wintergarden", "Wintergarden interactive experience")
    for index in range(12):
        add(f"later-{index:02d}", f"Routine record {index:02d}")
    private = add("private-other-work", "Wintergarden private notebook",
                  context="work:other-center")
    shared = add("explicitly-shared", "Wintergarden shared boundary",
                 context="work:other-center", scope="subject")
    paused = add("paused-wintergarden", "Wintergarden withdrawn guidance")
    native.memory.revise_experience(paused["id"], expected_revision=1,
                                    changes={"active": False})

    def read(args):
        return native.tool.execute(
            native.scope, ToolCall("lookup", "posture", args),
            CancellationToken(), action_request_id="lookup-action",
        )

    newest = read({"op": "list"})
    assert len(newest["experiences"]) <= 8
    assert newest["more_experiences"]
    assert old["id"] not in {item["id"] for item in newest["experiences"]}

    found = read({"op": "find", "query": "WiNtErGaRdEn"})
    identities = {item["id"] for item in found["experiences"]}
    assert identities == {old["id"], shared["id"]}
    assert found["matching_count"] == 2
    assert found["retrieval"] == "literal_casefold_on_eligible_abstractions_only"
    assert "encounter" not in next(item for item in found["experiences"]
                                   if item["id"] == shared["id"])
    assert "evidence" not in next(item for item in found["experiences"]
                                  if item["id"] == shared["id"])["source"]
    assert read({"op": "get", "experience_id": old["id"]})["experience"]["id"] == old["id"]
    assert read({"op": "get", "experience_id": shared["id"]})["experience"]["encounter_available"] is False
    for inaccessible in (private["id"], paused["id"], "missing-id"):
        with pytest.raises(ScopeConflict, match="not active or accessible"):
            read({"op": "get", "experience_id": inaccessible})
    for malformed in ({"op": "find", "query": "a"}, {"op": "find", "query": "line\\nbreak"},
                      {"op": "get", "experience_id": ""}, {"op": "get", "experience_id": old["id"],
                                                         "context_id": "work:other-center"}):
        with pytest.raises(ValueError):
            validate(malformed)
    assert native.tool._requests() == []  # Read-only: no staged journal write.


def test_model_cannot_supply_a_scope_or_fabricated_encounter(native):
    for extra in ({"scope": "subject"}, {"encounter": {"action": "Invented success"}},
                  {"source": {"kind": "verified"}}):
        with pytest.raises(ValueError):
            validate({**native.values, **extra})
    with pytest.raises(ValueError):
        validate({**native.values, "counterexamples": []})


def test_cancel_subject_and_early_cancel_preserve_no_learned_record(native):
    receipt = remember(native)
    assert native.tool.cancel_subject(native.scope.engram_id) == 1
    assert native.runtime.storage.load_component_state(receipt["request_id"])["status"] == "cancelled"
    token = CancellationToken()
    token.cancel()
    with pytest.raises(ScopeConflict, match="cancelled"):
        native.tool.execute(native.scope, ToolCall("call-two", "posture", native.values), token,
                            action_request_id="another-action")
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []


def test_native_model_tool_learning_reaches_later_ordinary_work(tmp_path, monkeypatch):
    """Exercise real tool admission and World settlement with a local transport."""
    import json
    import time

    import httpx
    from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
    from test_pages import client_for, send, wait_for

    monkeypatch.setenv("NATIVE_POSTURE_TEST_KEY", "synthetic-native-posture-key")
    stage = {"name": "first", "round": 0}
    requests, staged_receipts, feedback_receipts, learning_sources = [], [], [], []

    def response(*, text=None, arguments=None, call_id=None):
        delta = {"content": text} if arguments is None else {"tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": "posture", "arguments": json.dumps(arguments)},
        }]}
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {},
                          "finish_reason": "stop" if arguments is None else "tool_calls"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                                      + "data: [DONE]\n\n")

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        rendered = json.dumps(body["messages"])
        if "WORK_MATERIAL_JSON" in rendered:
            if stage["name"] == "reject":
                return response(text="PASS")
            if "candidate_response" in rendered:
                return response(text="PASS")
            return response(text="Build one directly playable interaction before asking for profile details.")
        if stage["name"] == "first":
            return response(text="The first screen is a twelve-field intake form.")
        if stage["name"] == "learn":
            index = stage["round"]
            stage["round"] += 1
            if index == 0:
                return response(arguments={"op": "list"}, call_id="posture-list")
            latest = next(message for message in reversed(body["messages"]) if message["role"] == "tool")
            outcome = json.loads(latest["content"])
            assert outcome["status"] == "succeeded", outcome
            if index == 1:
                originals = outcome["output"]["recent_sources"]
                source = next(item for item in originals if item["kind"] == "assistant_result")
                feedback = [item for item in originals if item["source"] == "user"][-1]
                learning_sources.extend([source["message_id"], feedback["message_id"]])
                values = {
                    "op": "remember", "source_message_id": source["message_id"],
                    "feedback_message_id": feedback["message_id"],
                    "title": "Direct interaction before optional setup",
                    "lesson": "Deliver an interactive attempt when immediate experience is the purpose.",
                    "conditions": {"not": "Collecting standard records is itself the requested product"},
                    "counterexamples": ["A standard registration task needs a concise form."],
                    "shift": {"understanding": "Identify the first usable interaction.",
                              "candidates": "Consider a playable first slice.",
                              "delivery": "Verify the visitor can actually try it."},
                }
                return response(arguments=values, call_id="posture-remember")
            staged_receipts.append(outcome["output"])
            return response(text="The revised flow begins with a directly playable interaction.")
        if stage["name"] == "reject":
            index = stage["round"]
            stage["round"] += 1
            if index == 0:
                return response(arguments={"op": "list"}, call_id="posture-list-later")
            latest = next(message for message in reversed(body["messages"]) if message["role"] == "tool")
            outcome = json.loads(latest["content"])
            assert outcome["status"] == "succeeded", outcome
            if index == 1:
                record = outcome["output"]["experiences"][0]
                feedback = [item for item in outcome["output"]["recent_sources"] if item["source"] == "user"][-1]
                return response(arguments={
                    "op": "feedback", "experience_id": record["id"], "expected_revision": record["revision"],
                    "verdict": "withdrawn", "feedback_message_id": feedback["message_id"],
                }, call_id="posture-withdraw")
            feedback_receipts.append(outcome["output"])
            return response(text="I will stop applying that lesson and preserve the straightforward registration flow.")
        return response(text="A new visitor can try one practice scene immediately.")

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "native-posture-fixture",
            credential_env="NATIVE_POSTURE_TEST_KEY",
        ), transport=httpx.MockTransport(handler))

    with client_for(tmp_path, posture=True, model_factory=factory) as client:
        send(client, "home", "Build a practice product a visitor can try immediately.")
        wait_for(client, "home", lambda view: sum(m["role"] == "assistant" for m in view["messages"]) == 1)
        stage.update(name="learn", round=0)
        correction = send(client, "home", "Let me try the interaction before asking for a profile.")
        wait_for(client, "home", lambda view: sum(m["role"] == "assistant" for m in view["messages"]) == 2)
        r = client.app.state.pages.runtime
        deadline = time.monotonic() + 5
        records = []
        while time.monotonic() < deadline:
            records = r.harness.posture_memory.list_experiences(subject_id=r.continuity_engram_id)
            if records:
                break
            time.sleep(0.03)
        assert len(records) == 1
        saved = records[0]
        assert saved["record_type"] == "constructed_fixture"
        assert saved["source"]["kind"] == "synthetic_runtime_observation"
        assert saved["source"]["provider_evidence_class"] == "TEST_DOUBLE"
        assert staged_receipts[0]["status"] == "prepared"
        assert saved["source"]["feedback_message_id"] == correction["messageId"] == learning_sources[1]
        assert saved["source"]["source_message_id"] == learning_sources[0]
        assert saved["source"]["interpretation"] == "model_proposed_lesson_from_user_followup"
        assert saved["encounter"]["action"] == "The first screen is a twelve-field intake form."
        request_count_before = len(requests)
        stage.update(name="reuse", round=0)
        send(client, "home", "Create another short practice scene for a new visitor.")
        wait_for(client, "home", lambda view: sum(m["role"] == "assistant" for m in view["messages"]) == 3)
        later = requests[request_count_before:]
        computations = [item for item in later if "WORK_MATERIAL_JSON" in json.dumps(item["messages"])]
        assert len(computations) == 2
        assert all(saved["id"] in json.dumps(item["messages"]) for item in computations)
        executor = next(item for item in later if "WORK_MATERIAL_JSON" not in json.dumps(item["messages"]))
        assert "POSTURE_WORK_CONTRIBUTION" in json.dumps(executor["messages"])
        stage.update(name="reject", round=0)
        rejection = send(client, "home", "Stop applying that lesson. It added an unnecessary scene to a simple registration task.")
        wait_for(client, "home", lambda view: sum(m["role"] == "assistant" for m in view["messages"]) == 4)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            revised = r.harness.posture_memory.get_experience(saved["id"])
            if revised["revision"] == 2:
                break
            time.sleep(0.03)
        assert revised["revision"] == 2 and not revised["active"]
        assert revised["feedback"][0]["source"]["feedback_message_id"] == rejection["messageId"]
        assert revised["feedback"][0]["source"]["kind"] == "model_interpreted_user_feedback"
        assert revised["feedback"][0]["evidence"].startswith("Stop applying that lesson.")
        assert feedback_receipts[0]["status"] == "prepared"
        request_count_before = len(requests)
        stage.update(name="after-feedback", round=0)
        send(client, "home", "Maintain the existing registration fields.")
        wait_for(client, "home", lambda view: sum(m["role"] == "assistant" for m in view["messages"]) == 5)
        later = requests[request_count_before:]
        assert len(later) == 1  # Withdrawal stops new posture computations.
        assert "POSTURE_WORK_CONTRIBUTION" not in json.dumps(later[0]["messages"])


def test_native_feedback_cannot_self_award_useful_or_commit_twice(native, monkeypatch):
    created = remember(native)
    settle(native)
    native.tool.drain()
    r = native.runtime
    feedback_id = r.inject(native.scope.engram_id, "Withdraw this lesson; it was harmful for this registration tool.")
    current = r.causal_ledger.begin_turn(
        feedback_id, native.scope.engram_id, "Withdraw this lesson; it was harmful for this registration tool.",
        execution_context="", runtime_fence=r._current_runtime_fence(),
    )
    native.current, native.scope = current, native.scope.for_turn(current.id)
    values = {"op": "feedback", "experience_id": created["experience_id"], "expected_revision": 1,
              "verdict": "withdrawn", "feedback_message_id": feedback_id}
    with pytest.raises(ValueError, match="positive"):
        validate({**values, "verdict": "useful"})
    native.states.arguments = values
    staged = remember(native, values, action="withdraw-action")
    assert native.memory.get_experience(created["experience_id"])["active"]
    settle(native)
    finish = native.tool._finish
    monkeypatch.setattr(native.tool, "_finish", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("feedback receipt crash")))
    with pytest.raises(RuntimeError, match="receipt crash"):
        native.tool.drain()
    monkeypatch.setattr(native.tool, "_finish", finish)
    native.tool.drain()
    revised = native.memory.get_experience(created["experience_id"])
    assert revised["revision"] == 2 and len(revised["feedback"]) == 1 and not revised["active"]
    receipt = r.storage.load_component_state(staged["request_id"])
    assert receipt["status"] == "feedback_recorded" and receipt["revision"] == 2


def test_same_turn_withdrawal_and_new_lesson_survive_partial_receipt_failure(native, monkeypatch):
    created = remember(native)
    settle(native)
    native.tool.drain()
    r = native.runtime
    previous_result = r.causal_ledger.get_turn_for_world(native.current.id, r.world_id).result_event_id
    followup = "Withdraw that broad lesson. Keep registration simple; offer a playable slice only for practice products."
    feedback_id = r.inject(native.scope.engram_id, followup)
    current = r.causal_ledger.begin_turn(
        feedback_id, native.scope.engram_id, followup,
        execution_context="", runtime_fence=r._current_runtime_fence(),
    )
    native.current, native.scope = current, native.scope.for_turn(current.id)
    withdrawal = {"op": "feedback", "experience_id": created["experience_id"], "expected_revision": 1,
                  "verdict": "withdrawn", "feedback_message_id": feedback_id}
    replacement = {**native.values, "source_message_id": previous_result, "feedback_message_id": feedback_id,
                   "title": "A playable slice when practice is the actual goal",
                   "conditions": "The task asks for immediate practice, not collecting registration records."}
    action_arguments = {"withdraw-old": withdrawal, "remember-narrower": replacement}
    monkeypatch.setattr(r.kernel_service.store, "get_action", lambda scope, identity: SimpleNamespace(
        status=ActionStatus.SUCCEEDED, tool_name="posture", arguments=action_arguments[identity],
    ))
    old_receipt = remember(native, withdrawal, action="withdraw-old")
    new_receipt = remember(native, replacement, action="remember-narrower")
    assert remember(native, withdrawal, action="withdraw-old") == old_receipt
    assert remember(native, replacement, action="remember-narrower") == new_receipt
    assert native.memory.get_experience(created["experience_id"])["active"]
    assert len(native.memory.list_experiences(subject_id=native.scope.engram_id)) == 1
    settle(native)
    finish = native.tool._finish

    def interrupted_finish(record, status, error=None, revision=None):
        if record["request_id"] == new_receipt["request_id"]:
            raise RuntimeError("replacement receipt crash")
        finish(record, status, error, revision)

    monkeypatch.setattr(native.tool, "_finish", interrupted_finish)
    with pytest.raises(RuntimeError, match="replacement receipt crash"):
        native.tool.drain()
    # Rebuild the bridge as after process recovery. Each completed memory write
    # is recovered from its action identity; neither revision is applied again.
    recovered = NativePosture(r)
    recovered.drain()
    recovered.drain()
    all_records = native.memory.list_experiences(subject_id=native.scope.engram_id, include_inactive=True)
    assert len(all_records) == 2
    old = native.memory.get_experience(created["experience_id"])
    new = native.memory.get_experience(new_receipt["experience_id"])
    assert old["revision"] == 2 and not old["active"] and len(old["feedback"]) == 1
    assert new["revision"] == 1 and new["active"] and new["record_type"] == "constructed_fixture"
    assert new["source"]["learning_turn_id"] == old["feedback"][0]["source"]["learning_turn_id"] == current.id
    assert r.storage.load_component_state(old_receipt["request_id"])["status"] == "feedback_recorded"
    assert r.storage.load_component_state(new_receipt["request_id"])["status"] == "remembered"


def test_feedback_cannot_reuse_precreation_or_future_message(native):
    created = remember(native)
    settle(native)
    native.tool.drain()
    r = native.runtime
    feedback_id = r.inject(native.scope.engram_id, "This lesson added unnecessary work; stop applying it.")
    current = r.causal_ledger.begin_turn(
        feedback_id, native.scope.engram_id, "This lesson added unnecessary work; stop applying it.",
        execution_context="", runtime_fence=r._current_runtime_fence(),
    )
    native.current, native.scope = current, native.scope.for_turn(current.id)
    future_id = r.inject(native.scope.engram_id, "A later message is not visible to the ongoing turn.")
    values = {"op": "feedback", "experience_id": created["experience_id"],
              "expected_revision": 1, "verdict": "withdrawn"}
    for invalid_id in (native.feedback, future_id, native.original.id):
        with pytest.raises(ScopeConflict, match="later actual user message"):
            remember(native, {**values, "feedback_message_id": invalid_id}, action="invalid-feedback")
    assert native.tool._requests(pending=True) == []
    assert native.memory.get_experience(created["experience_id"])["revision"] == 1


@pytest.mark.parametrize("page_kind", ["home", "work"])
def test_committed_native_succession_retains_source_access_in_its_context(tmp_path, monkeypatch, page_kind):
    """Run native summary, rotation, publication, and later tool admission."""
    import json

    import httpx
    from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

    monkeypatch.setenv("NATIVE_POSTURE_LINEAGE_KEY", "synthetic-lineage-key")
    stage = {"name": "original", "round": 0}
    first_reply = "The first screen is a twelve-field intake form."
    selected = {}

    def handler(request):
        body = json.loads(request.content)
        arguments = None
        if stage["name"] == "original":
            text = first_reply
        elif stage["name"] == "summary":
            text = "Continue the same subject and retain the original work in its context."
        else:
            index = stage["round"]
            stage["round"] += 1
            text = "The lesson is now narrower and local to this work."
            if index == 0:
                arguments = {"op": "list"}
            elif index == 1:
                result = json.loads(next(m for m in reversed(body["messages"]) if m["role"] == "tool")["content"])
                assert result["status"] == "succeeded", result
                sources = result["output"]["recent_sources"]
                original = next(item for item in sources if item["content"] == first_reply)
                followup = [item for item in sources if item["source"] == "user"][-1]
                selected.update(original=original, followup=followup)
                arguments = {
                    "op": "remember", "source_message_id": original["message_id"],
                    "feedback_message_id": followup["message_id"],
                    "title": "Offer a usable first interaction for practice products",
                    "lesson": "Let the visitor try a practice interaction before optional personalization.",
                    "conditions": "Immediate interactive practice is the purpose of the task.",
                    "counterexamples": ["Registration itself can appropriately remain a concise form."],
                    "shift": {"understanding": "Identify the intended first use.",
                              "candidates": "Consider a directly usable first slice.",
                              "delivery": "Check the first interaction works."},
                }
        delta = {"content": text} if arguments is None else {"tool_calls": [{
            "index": 0, "id": "posture-lineage-" + str(stage["round"]), "type": "function",
            "function": {"name": "posture", "arguments": json.dumps(arguments)},
        }]}
        payload = {"choices": [{"index": 0, "delta": delta,
                                "finish_reason": "stop" if arguments is None else "tool_calls"}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n")

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "native-posture-lineage-fixture",
            credential_env="NATIVE_POSTURE_LINEAGE_KEY",
        ), transport=httpx.MockTransport(handler))

    pages = PageService(tmp_path, posture=True, model_factory=factory)
    r = pages.runtime
    try:
        predecessor = r.continuity_engram_id
        if page_kind == "work":
            created = pages.create_page("Practice", "Build a practice product a visitor can try immediately.")
            page_id, first_input = created["page"]["id"], created["initialMessageId"]
            context = "work:" + r.world.get_task_front(page_id).center_id
        else:
            page_id, context = "home", ""
            first_input = pages.send(page_id, "Build a practice product a visitor can try immediately.")["messageId"]
        original = r._mgr.pulse(
            predecessor, pulse_event=r.causal_ledger.get_event(first_input), execution_context=context,
            runtime_fence=r._current_runtime_fence(),
        )
        original_id = r.causal_ledger.get_turn_for_world(original.turn_id, r.world_id).result_event_id
        pending = pages.send(page_id, "Preserve the pending work through the generation change.")["messageId"]
        stage["name"] = "summary"
        preparation = r._mgr.prepare_succession(
            predecessor, parent_event_id=original_id, runtime_fence=r._current_runtime_fence(),
        )
        assert r.causal_ledger.committed_history_lineage(r.world_id, preparation.successor_id) == (preparation.successor_id,)
        with pytest.raises(ScopeConflict, match="not a message"):
            capture_encounter(r, preparation.successor_id, context, original_id, pending)
        succession = r._mgr.commit_succession(preparation, runtime_fence=r._current_runtime_fence())
        successor = succession.new_id
        assert r.continuity_engram_id == successor
        assert r.causal_ledger.committed_history_lineage(r.world_id, successor) == (successor, predecessor)
        if page_kind == "work":
            assert r.world.get_task_front(page_id).focal_engram_id == successor
        pages._check_message(page_id, original_id)
        correction = pages.send(page_id, "Let me try the interaction before asking for a profile.")["messageId"]
        stage.update(name="learn", round=0)
        learned = r._mgr.pulse(
            successor, pulse_event=r.causal_ledger.get_event(correction), execution_context=context,
            runtime_fence=r._current_runtime_fence(),
        )
        assert len(r._native_posture._requests(pending=True)) == 1
        assert r.harness.posture_memory.list_experiences(subject_id=successor) == []
        assert r._native_posture.cancel_subject(successor) == 0
        # A successful learning turn may be followed by normal succession
        # before Runtime gets another tick to drain its staged lesson.
        stage["name"] = "summary"
        learned_result = r.causal_ledger.get_turn_for_world(learned.turn_id, r.world_id).result_event_id
        continued = r._mgr.succession(
            successor, parent_event_id=learned_result, runtime_fence=r._current_runtime_fence(),
        ).new_id
        assert r.continuity_engram_id == continued
        r._native_posture.drain()
        records = r.harness.posture_memory.list_experiences(subject_id=continued)
        assert len(records) == 1
        record = records[0]
        assert record["subject_id"] == successor and record["context_id"] == context
        assert record["source"]["source_subject_id"] == selected["original"]["subject_id"] == predecessor
        assert record["source"]["feedback_subject_id"] == selected["followup"]["subject_id"] == successor
        assert record["source"]["source_message_id"] == original_id
        assert record["source"]["feedback_message_id"] == correction
        assert record["source"]["learning_turn_id"] == learned.turn_id
        assert record["record_type"] == "constructed_fixture"
        other_context = "" if context else "work:unrelated"
        with pytest.raises(ScopeConflict):
            capture_encounter(r, continued, other_context, original_id, correction)
    finally:
        r.close()


def test_credential_bearing_source_never_enters_learning_journal(native):
    r = native.runtime
    _, secret_source = r.causal_ledger.settle_turn(
        native.current.id, "The copied example contains api_key=synthetic-sensitive-value.",
        runtime_fence=r._current_runtime_fence(),
    )
    feedback = r.inject(native.scope.engram_id, "Use the documented setup pattern instead.")
    turn = r.causal_ledger.begin_turn(
        feedback, native.scope.engram_id, "Use the documented setup pattern instead.",
        execution_context="", runtime_fence=r._current_runtime_fence(),
    )
    native.scope = native.scope.for_turn(turn.id)
    values = {**native.values, "source_message_id": secret_source.id, "feedback_message_id": feedback}
    with pytest.raises(ScopeConflict, match="contains a credential") as error:
        remember(native, values)
    assert "synthetic-sensitive-value" not in str(error.value)
    assert native.tool._requests() == []
    assert native.memory.list_experiences(subject_id=native.scope.engram_id) == []


def revision_turn(env, *, paused_and_shared=False):
    created = remember(env)
    settle(env)
    env.tool.drain()
    saved = env.memory.get_experience(created["experience_id"])
    if paused_and_shared:
        saved = env.memory.revise_experience(
            saved["id"], expected_revision=saved["revision"], changes={"active": False, "scope": "subject"},
        )
    correction = "Keep the experience lesson for practice products. A fixed attendance register needs its concise form."
    feedback_id = env.runtime.inject(env.scope.engram_id, correction)
    current = env.runtime.causal_ledger.begin_turn(
        feedback_id, env.scope.engram_id, correction,
        execution_context="", runtime_fence=env.runtime._current_runtime_fence(),
    )
    env.current, env.scope = current, env.scope.for_turn(current.id)
    values = {
        "op": "revise", "experience_id": saved["id"], "expected_revision": saved["revision"],
        "feedback_message_id": feedback_id,
        "changes": {
            "conditions": {"all": ["The user needs an immediate practice experience",
                                    {"not": "Collecting fixed attendance records is the requested outcome"}]},
            "counterexamples": ["Use a concise form for a fixed attendance register."],
            "shift": {"understanding": "Separate practice from collecting standard records.",
                      "candidates": "Keep the concise registration form when the records are the product.",
                      "delivery": "Check the first practice action or the requested record, as applicable."},
        },
    }
    env.states.arguments = values
    return saved, values, correction


@pytest.mark.parametrize("paused_and_shared", [False, True])
def test_native_revision_preserves_identity_source_controls_and_attributes_correction(native, paused_and_shared):
    before, values, correction = revision_turn(native, paused_and_shared=paused_and_shared)
    staged = remember(native, values, action="refine-lesson")
    assert staged["status"] == "prepared"
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    settle(native)
    native.tool.drain()
    after = native.memory.get_experience(before["id"])
    assert after["id"] == before["id"]
    assert after["revision"] == before["revision"] + 1
    assert after["source"] == before["source"] and after["encounter"] == before["encounter"]
    assert after["active"] == before["active"] and after["scope"] == before["scope"]
    assert all(after[key] == value for key, value in values["changes"].items())
    assert after["history"][-1]["conditions"] == before["conditions"]
    feedback = after["feedback"][-1]
    assert feedback["verdict"] == "revised" and feedback["revision"] == before["revision"]
    assert feedback["evidence"] == correction
    assert feedback["source"]["kind"] == "model_interpreted_user_revision"
    assert feedback["source"]["feedback_message_id"] == values["feedback_message_id"]
    assert feedback["source"]["source_action_id"] == "refine-lesson"
    receipt = native.runtime.storage.load_component_state(staged["request_id"])
    assert receipt["status"] == "revised" and receipt["revision"] == after["revision"]
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == after
    if not paused_and_shared:
        material = native.memory.materials(subject_id=native.scope.engram_id, context_id="")["episodes"][0]
        assert material["conditions"] == values["changes"]["conditions"]
        assert material["revision"] == after["revision"]
        assert material["feedback"][-1]["source"] == feedback["source"]


@pytest.mark.parametrize("changes", [
    {}, {"active": True}, {"scope": "subject"}, {"encounter": {}}, {"source": {}},
    {"counterexamples": []}, {"shift": {"delivery": "Incomplete shift"}},
])
def test_native_revision_rejects_invalid_or_operator_control_changes(changes):
    with pytest.raises(ValueError):
        validate({"op": "revise", "experience_id": "lesson", "expected_revision": 1,
                  "feedback_message_id": "actual-message", "changes": changes})


@pytest.mark.parametrize("field,value", [
    ("turn", TurnStatus.CANCELLED), ("turn", TurnStatus.FAILED),
    ("action", ActionStatus.FAILED), ("name", "another_tool"),
])
def test_native_revision_requires_successful_exact_action_and_turn(native, field, value):
    before, values, _ = revision_turn(native)
    staged = remember(native, values, action="unsuccessful-revision")
    settle(native)
    setattr(native.states, field, value)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    assert native.runtime.storage.load_component_state(staged["request_id"])["status"] == "cancelled"


@pytest.mark.parametrize("operator_changes", [{"active": False}, {"title": "The operator's later wording"}])
def test_native_revision_never_overwrites_intervening_operator_change(native, operator_changes):
    before, values, _ = revision_turn(native)
    staged = remember(native, values, action="stale-revision")
    operator = native.memory.revise_experience(
        before["id"], expected_revision=before["revision"], changes=operator_changes,
    )
    settle(native)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == operator
    receipt = native.runtime.storage.load_component_state(staged["request_id"])
    assert receipt["status"] == "blocked" and receipt["error_code"] == "experience_not_admitted"


def test_native_revision_rejects_old_future_or_other_context_feedback(native):
    before, values, _ = revision_turn(native)
    future = native.runtime.inject(native.scope.engram_id, "A later user message is not visible yet.")
    for identity in (native.feedback, native.original.id, future):
        with pytest.raises(ScopeConflict, match="later actual user message"):
            remember(native, {**values, "feedback_message_id": identity}, action="invalid-revision")
    newer = native.memory.revise_experience(before["id"], expected_revision=1, changes={"title": "Later operator edit"})
    with pytest.raises(ScopeConflict, match="later actual user message"):
        remember(native, {**values, "expected_revision": newer["revision"]}, action="outdated-feedback")
    # Even a refreshed revision number cannot turn a pre-edit message into
    # new evidence against that operator edit.
    assert native.memory.get_experience(before["id"]) == newer
    foreign = native.memory.add_experience(
        subject_id=native.scope.engram_id, context_id="work:other-page",
        **{key: before[key] for key in (
            "title", "lesson", "encounter", "conditions", "counterexamples", "shift", "source", "record_type",
        )},
    )
    with pytest.raises(ScopeConflict, match="this context's own"):
        remember(native, {**values, "experience_id": foreign["id"]}, action="foreign-lesson")
    assert native.tool._requests(pending=True) == []


def test_native_revision_recovers_receipt_crash_without_reapplying_over_later_edit(native, monkeypatch):
    before, values, _ = revision_turn(native)
    staged = remember(native, values, action="revision-before-crash")
    assert remember(native, values, action="revision-before-crash") == staged
    settle(native)
    monkeypatch.setattr(native.tool, "_finish", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("revision receipt crash")))
    with pytest.raises(RuntimeError, match="receipt crash"):
        native.tool.drain()
    applied = native.memory.get_experience(before["id"])
    assert applied["revision"] == 2 and len(applied["feedback"]) == 1
    operator = native.memory.revise_experience(before["id"], expected_revision=2, changes={"active": False})
    recovered = NativePosture(native.runtime)
    recovered.drain()
    recovered.drain()
    assert native.memory.get_experience(before["id"]) == operator
    receipt = native.runtime.storage.load_component_state(staged["request_id"])
    assert receipt["status"] == "revised" and receipt["revision"] == 2


def test_native_revision_journal_capacity_failure_is_atomic(native, monkeypatch):
    import pulse_system.service.posture as posture_module

    before, values, _ = revision_turn(native)
    staged = remember(native, values, action="revision-at-capacity")
    settle(native)
    monkeypatch.setattr(posture_module, "MAX_STATE_BYTES", 1)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    receipt = native.runtime.storage.load_component_state(staged["request_id"])
    assert receipt["status"] == "blocked" and receipt["error_code"] == "experience_not_admitted"


def test_native_revision_staged_content_cannot_differ_from_actual_action(native):
    before, values, _ = revision_turn(native)
    staged = remember(native, values, action="revision-exact-arguments")
    stored = native.runtime.storage.load_component_state(staged["request_id"])
    stored["revision_update"]["changes"]["lesson"] = "A different unrequested lesson."
    native.runtime.storage.save_component_state(staged["request_id"], stored)
    settle(native)
    native.tool.drain()
    assert native.memory.get_experience(before["id"]) == before
    assert native.runtime.storage.load_component_state(staged["request_id"])["error_code"] == "revision_identity_conflict"
