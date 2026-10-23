"""Mechanism checks for attributed posture memory and native semantic work.

All model output here is an explicitly synthetic transport fixture. These
tests establish routing, privacy, persistence, and accounting, not taste.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core import HarnessScope, HarnessStore, TranscriptStore, TurnRunner, TurnStatus
from pulse_harness.core.provider import CancellationRequested, ModelRequest
from pulse_harness.core.response_review import CombinedResponseReviewer, RESPONSE_REVIEW_PREFIX, ResponseReview, decode_review_envelope, encode_review_envelope, review_receipts
from pulse_harness.core.turn_advice import TURN_ADVICE_PREFIX, decode_advice
from pulse_harness.plugins.posture import pack_posture_materials
from pulse_harness.plugins.expert_work import make_work_prompt, WorkMaterialBudgetExceeded
from pulse_system.service.expert_computation import ExpertRuntime, recorded_expert_usage
from pulse_system.service.posture import PostureMemory, posture_enabled
from pulse_system.substrate.storage import Storage


def episode(**changes):
    result = {
        "subject_id": "subject", "context_id": "origin", "title": "First useful experience",
        "lesson": "Let a visitor experience the central value before asking for setup.",
        "encounter": {"situation": "PRIVATE_SITUATION_MARKER", "action": "PRIVATE_ACTION_MARKER",
                      "outcome": "PRIVATE_OUTCOME_MARKER"},
        "conditions": {"all": ["The user is seeking an experience",
                                {"any": ["An immediate attempt is feasible", "A concrete preview is feasible"]},
                                {"not": "Registration is the requested value"}]},
        "counterexamples": ["An event attendance register should remain a simple form."],
        "shift": {"understanding": "Identify what the visitor can do first.",
                  "candidates": "Offer a direct attempt or a concrete preview.",
                  "delivery": "Inspect the first usable result, not the promise of one."},
        "source": {"kind": "synthetic_fixture", "message_id": "PRIVATE_SOURCE_MARKER"},
        "record_type": "constructed_fixture",
    }
    result.update(changes)
    return result


def test_original_encounter_persists_without_becoming_a_second_session_store(tmp_path):
    path = tmp_path / "world.db"
    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    saved = memory.add_experience(**episode())
    storage.close()
    reopened = Storage(path)
    try:
        memory = PostureMemory(reopened, "world")
        assert memory.get_experience(saved["id"]) == saved
        assert memory.materials(subject_id="subject", context_id="origin")["episodes"][0]["encounter"] == saved["encounter"]
        assert PostureMemory(reopened, "another-world").list_experiences(subject_id="subject") == []
    finally:
        reopened.close()


def test_default_context_and_explicit_transfer_have_different_disclosure():
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        saved = memory.add_experience(**episode())
        assert memory.materials(subject_id="subject", context_id="new-task")["episodes"] == []
        shared = memory.revise_experience(saved["id"], expected_revision=1, changes={"scope": "subject"})
        packet = memory.materials(subject_id="subject", context_id="new-task")
        offered = packet["episodes"][0]
        assert offered["revision"] == 2
        assert offered["lesson"] == saved["lesson"]
        assert "encounter" not in offered
        assert "PRIVATE_" not in json.dumps(packet)
        assert shared["history"][0] == {key: value for key, value in saved.items() if key != "history"}
        assert memory.materials(subject_id="someone-else", context_id="origin")["episodes"] == []
    finally:
        storage.close()


def test_native_empty_home_context_can_retain_and_reuse_experience():
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        saved = memory.add_experience(**episode(context_id=""))
        assert saved["context_id"] == ""
        assert memory.materials(subject_id="subject", context_id="")["episodes"][0]["id"] == saved["id"]
        assert memory.materials(subject_id="subject", context_id="home")["episodes"] == []
    finally:
        storage.close()


def test_only_host_lineage_can_inherit_ancestor_experience():
    storage = Storage()
    memory = PostureMemory(storage, "world", history_lineage=lambda subject: ("ancestor",) if subject == "successor" else ())
    try:
        saved = memory.add_experience(**episode(subject_id="ancestor"))
        assert memory.owns_subject("successor", "ancestor")
        assert not memory.owns_subject("unrelated", "ancestor")
        assert memory.materials(subject_id="successor", context_id="origin")["episodes"][0]["id"] == saved["id"]
        assert memory.materials(subject_id="successor", context_id="another-context")["episodes"] == []
        assert memory.materials(subject_id="unrelated", context_id="origin")["episodes"] == []
        assert PostureMemory(storage, "world").materials(subject_id="successor", context_id="origin")["episodes"] == []
    finally:
        storage.close()


def test_feedback_is_revisioned_reversible_and_privately_attributed():
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        first = memory.add_experience(**episode(scope="subject"))
        stopped = memory.record_feedback(first["id"], expected_revision=1, verdict="harmful",
                                         evidence="PRIVATE_FEEDBACK_MARKER: this task needed direct registration.",
                                         source={"kind": "operator_report", "message_id": "feedback-source"})
        assert stopped["revision"] == 2 and not stopped["active"]
        assert len(stopped["history"]) == 1
        assert memory.materials(subject_id="subject", context_id="origin")["episodes"] == []
        with pytest.raises(ValueError, match="revision conflict"):
            memory.revise_experience(first["id"], expected_revision=1, changes={"active": True})
        resumed = memory.revise_experience(first["id"], expected_revision=2, changes={
            "active": True, "lesson": "Offer direct experience only when registration is not itself the purpose.",
        })
        local = memory.materials(subject_id="subject", context_id="origin")["episodes"][0]
        remote = memory.materials(subject_id="subject", context_id="new-task")["episodes"][0]
        assert local["feedback"][0]["evidence"].startswith("PRIVATE_FEEDBACK_MARKER")
        assert remote["feedback"] == [{"verdict": "harmful", "revision": 1}]
        assert "PRIVATE_FEEDBACK_MARKER" not in json.dumps(remote)
        assert resumed["history"][-1]["active"] is False
        assert resumed["revision"] == 3
    finally:
        storage.close()


@pytest.mark.parametrize("change", [
    {"source": {}}, {"counterexamples": []}, {"conditions": {"any": []}},
    {"conditions": {"all": ["a"], "not": "b"}}, {"shift": {"candidates": "Only a slogan"}},
    {"scope": "everybody"}, {"record_type": "learned_by_assertion"},
    {"scope": []}, {"record_type": []}, {"title": "invalid\ud800"},
])
def test_invalid_experience_does_not_partially_write(change):
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        with pytest.raises(ValueError):
            memory.add_experience(**episode(**change))
        assert storage.load_component_state(memory.component) is None
    finally:
        storage.close()


def test_concurrent_revisions_require_fresh_observation():
    storage = Storage()
    one = PostureMemory(storage, "world")
    two = PostureMemory(storage, "world")
    try:
        saved = one.add_experience(**episode())
        def change(memory, title):
            try:
                return memory.revise_experience(saved["id"], expected_revision=1, changes={"title": title})
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda args: change(*args), [(one, "One"), (two, "Two")]))
        assert sum(value is not None for value in outcomes) == 1
        assert one.get_experience(saved["id"])["revision"] == 2
    finally:
        storage.close()


def test_invalid_revision_type_does_not_advance_or_write():
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        saved = memory.add_experience(**episode())
        with pytest.raises(ValueError, match="scope"):
            memory.revise_experience(saved["id"], expected_revision=1, changes={"scope": []})
        assert memory.get_experience(saved["id"]) == saved
    finally:
        storage.close()


def test_byte_packing_never_changes_conditions_or_counterexamples():
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        memory.add_experience(**episode(lesson="Value " * 500))
        materials = memory.materials(subject_id="subject", context_id="origin")
        packed = pack_posture_materials(materials)
        assert packed["episodes"][0]["conditions"] == materials["episodes"][0]["conditions"]
        assert packed["episodes"][0]["counterexamples"] == materials["episodes"][0]["counterexamples"]
        assert "lesson" in packed["episodes"][0]["clipped_fields"]
        tiny = pack_posture_materials(materials, byte_budget=10)
        assert tiny["episodes"] == []
        assert tiny["omitted_posture_revisions"] == packed["offered_posture_revisions"]
    finally:
        storage.close()


class NoTools:
    def descriptors(self):
        return ()
    def execute(self, call, cancellation):
        raise AssertionError("This transport fixture has no tools.")


def model_fixture(monkeypatch, requests, *, correction=False, all_contributions=False):
    monkeypatch.setenv("POSTURE_TEST_KEY", "synthetic-transport-key")
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        text = json.dumps(body["messages"])
        if "UNTRUSTED_REVIEW_DATA_JSON" in text:
            answer = '{"mode":"none"}'
        elif "WORK_MATERIAL_JSON" in text:
            if all_contributions or (correction and "candidate_response" in text):
                answer = "Use the direct worked example already available before the setup form."
            else:
                answer = "PASS"
        else:
            answer = "SYNTHETIC_FINAL_CANDIDATE"
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 3}},
        ]
        stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)
    return ChatHTTPModelAdapter(
        ChatHTTPConfig("https://fixture.invalid/chat/completions", "posture-transport-fixture", credential_env="POSTURE_TEST_KEY"),
        transport=httpx.MockTransport(handler))


def run_with_posture(tmp_path, monkeypatch, *, learned=False, correction=False):
    requests = []
    storage = Storage(tmp_path / "world.db")
    memory = PostureMemory(storage, "world")
    if learned:
        memory.add_experience(**episode(scope="subject"))
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None,
                           posture_memory=memory, expert_active=False)
    store = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", "subject", "session", 0, "owner", context_id="new-task", turn_id="turn")
    model = model_fixture(monkeypatch, requests, correction=correction)
    runner = TurnRunner(store, model_adapter=model, tool_provider=NoTools(),
                        turn_advisor=expert.advisor(store, model, scope),
                        response_reviewer=expert.posture_reviewer(store, model, scope))
    try:
        outcome = runner.run(scope, prompt="Build a useful first experience for a language practice page.")
        expert.record_outcome(store, scope, outcome)
        transcript = list(TranscriptStore(store).messages(scope))
        usage = recorded_expert_usage(store, scope)
        decisions = expert.store.turn_decisions(scope.as_dict())
        return requests, outcome, transcript, usage, decisions
    finally:
        runner.close()
        expert.close()
        store.close()
        storage.close()


def test_posture_only_without_eligible_experience_makes_no_extra_request(tmp_path, monkeypatch):
    requests, outcome, transcript, (usage, accounted), decisions = run_with_posture(tmp_path, monkeypatch)
    assert outcome.status is TurnStatus.SUCCEEDED
    assert len(requests) == 1
    assert not any(m.item_id.startswith(TURN_ADVICE_PREFIX) for m in transcript)
    assert usage["provider_requests"] == 0 and accounted
    assert decisions == []
    assert "POSTURE" not in json.dumps(requests[0]["messages"])


def test_semantic_pass_preserves_main_request_and_records_real_fork_cost(tmp_path, monkeypatch):
    requests, outcome, transcript, (usage, accounted), decisions = run_with_posture(tmp_path, monkeypatch, learned=True)
    assert outcome.status is TurnStatus.SUCCEEDED
    assert len(requests) == 3
    main = next(r for r in requests if "WORK_MATERIAL_JSON" not in json.dumps(r["messages"]))
    assert "POSTURE_WORK_CONTRIBUTION" not in json.dumps(main["messages"])
    assert "PRIVATE_" not in json.dumps(requests)
    assert usage == {"input_tokens": 20, "output_tokens": 6, "cached_tokens": 0,
                     "cache_write_tokens": 0, "provider_requests": 2}
    assert accounted
    assert {d["computation"]["checkpoint"] for d in decisions} == {"before_action", "delivery"}
    assert all(d["context"] is None and d["outcome"]["status"] == "succeeded" for d in decisions)
    review = next(decode_review_envelope(m.content) for m in transcript if m.item_id.startswith(RESPONSE_REVIEW_PREFIX))
    assert review["context"] is None
    assert review["receipt"]["fork"]["contribution_status"] == "pass"
    assert review["receipt"]["offered_posture_revisions"]


def test_delivery_contribution_continues_once_and_does_not_self_certify(tmp_path, monkeypatch):
    requests, outcome, transcript, (usage, accounted), decisions = run_with_posture(tmp_path, monkeypatch, learned=True, correction=True)
    assert outcome.status is TurnStatus.SUCCEEDED
    assert len(requests) == 4
    main = [r for r in requests if "WORK_MATERIAL_JSON" not in json.dumps(r["messages"])]
    assert len(main) == 2
    assert "single pre-publication" not in json.dumps(main[0]["messages"])
    assert "POSTURE_WORK_CONTRIBUTION" in json.dumps(main[1]["messages"])
    reviews = [decode_review_envelope(m.content) for m in transcript if m.item_id.startswith(RESPONSE_REVIEW_PREFIX)]
    assert len(reviews) == 1 and reviews[0]["receipt"]["continuation_limit"] == 1
    assert reviews[0]["candidate_text"] == "SYNTHETIC_FINAL_CANDIDATE"
    assert usage["provider_requests"] == 2 and accounted


def test_posture_does_not_silently_use_numerical_retrieval(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_EXPERT_MODE", "retrieval_baseline")
    storage = Storage()
    try:
        with pytest.raises(ValueError, match="requires.*semantic"):
            ExpertRuntime(tmp_path / "expert.db", guard=lambda: None,
                          posture_memory=PostureMemory(storage, "world"), expert_active=False)
        assert not (tmp_path / "expert.db").exists()
    finally:
        storage.close()


def test_posture_switch_is_explicit(monkeypatch):
    monkeypatch.delenv("PER_PULSE_POSTURE", raising=False)
    assert not posture_enabled()
    monkeypatch.setenv("PER_PULSE_POSTURE", "1")
    assert posture_enabled()
    monkeypatch.setenv("PER_PULSE_POSTURE", "sometimes")
    with pytest.raises(ValueError, match="0 or 1"):
        posture_enabled()


def test_retired_or_revised_experience_cannot_return_through_previous_work(tmp_path):
    storage = Storage()
    memory = PostureMemory(storage, "world")
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None, posture_memory=memory, expert_active=False)
    scope = HarnessScope("world", "subject", "session", 0, "owner", context_id="origin", turn_id="next")
    try:
        retired = memory.add_experience(**episode(experience_id="retired"))
        current = memory.add_experience(**episode(experience_id="current", title="A different boundary"))
        for identity, refs, marker in (
            ("old-A", [{"id": retired["id"], "revision": 1}], "RETIRED_WORK_MARKER"),
            ("old-B", [{"id": current["id"], "revision": 1}], "CURRENT_WORK_MARKER"),
            ("old-unattributed", [], "UNATTRIBUTED_WORK_MARKER"),
        ):
            expert.store.record_computation(
                decision_id=identity, owner="world:subject", situation="Synthetic past task",
                source={"scope": scope.for_turn(identity).as_dict()}, context=marker,
                computation={"method": "synthetic_fixture", "offered_posture_revisions": refs})
        memory.record_feedback(retired["id"], expected_revision=1, verdict="withdrawn", evidence="The earlier generalization failed.")
        material = expert.work_materials(scope, expert.posture_materials(scope))
        prompt = make_work_prompt({}, material, model_round=0, posture_packet=expert.posture_materials(scope))
        assert "RETIRED_WORK_MARKER" not in prompt
        assert "UNATTRIBUTED_WORK_MARKER" not in prompt
        assert "CURRENT_WORK_MARKER" in prompt
        assert set(material["omitted_stale_posture_decisions"]) == {"old-A", "old-unattributed"}
        memory.revise_experience(current["id"], expected_revision=1, changes={"lesson": "A corrected narrower lesson."})
        material = expert.work_materials(scope, expert.posture_materials(scope))
        assert not material["previous_computations"]
    finally:
        expert.close()
        storage.close()


def test_disabling_posture_retains_ordinary_expert_history_without_reviving_posture(tmp_path):
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None, expert_active=True)
    scope = HarnessScope("world", "subject", "session", 0, "owner", context_id="origin", turn_id="next")
    try:
        for identity, refs, marker in (
            ("old-posture", [{"id": "former-experience", "revision": 1}], "DISABLED_POSTURE_MARKER"),
            ("ordinary-expert", [], "ORDINARY_EXPERT_MARKER"),
        ):
            decision = expert.store.record_computation(
                decision_id=identity, owner="world:subject", situation="Synthetic past task",
                source={"scope": scope.for_turn(identity).as_dict()}, context=marker,
                computation={"method": "synthetic_fixture", "offered_posture_revisions": refs})
            if refs:
                from pulse_harness.core.turn_advice import TurnAdvice
                cached = expert.current_advice(scope, TurnAdvice(context=marker, receipt=decision["computation"]))
                assert cached.context is None
                assert cached.receipt["reason"] == "posture_revision_no_longer_eligible"
        material = expert.work_materials(scope, None)
        prompt = make_work_prompt({}, material, model_round=0)
        assert "DISABLED_POSTURE_MARKER" not in prompt
        assert "ORDINARY_EXPERT_MARKER" in prompt
    finally:
        expert.close()


def test_review_context_is_turn_local_even_after_plugin_is_disabled(tmp_path, monkeypatch):
    from pulse_harness.core.response_review import ResponseReview

    class Correction:
        def review(self, request, response_text, *, round_id, cancel_event):
            return ResponseReview(context="OLD_REVIEW_MARKER: supply the missing result.",
                                  receipt={"plugin": "synthetic_review_fixture"})

    store = HarnessStore(tmp_path / "kernel.db")
    requests = []
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="first")
    first = TurnRunner(store, model_adapter=model_fixture(monkeypatch, requests),
                       tool_provider=NoTools(), response_reviewer=Correction())
    try:
        assert first.run(scope, prompt="First task").status is TurnStatus.SUCCEEDED
        assert len(requests) == 2
        assert "OLD_REVIEW_MARKER" in json.dumps(requests[-1]["messages"])
    finally:
        first.close()
    second = TurnRunner(store, model_adapter=model_fixture(monkeypatch, requests), tool_provider=NoTools())
    try:
        assert second.run(scope.for_turn("second"), prompt="An ordinary later task").status is TurnStatus.SUCCEEDED
        assert len(requests) == 3
        assert "OLD_REVIEW_MARKER" not in json.dumps(requests[-1]["messages"])
        assert any("OLD_REVIEW_MARKER" in message.content for message in TranscriptStore(store).messages(scope.session_scope()))
    finally:
        second.close()
        store.close()


def test_pending_review_resumes_after_restart_even_without_active_reviewer(tmp_path, monkeypatch):
    from pulse_harness.core.response_review import ResponseReview

    class SimulatedPowerLoss(BaseException):
        pass

    complete_round = TurnRunner._complete_model_round
    def crash_after_review_commit(self, *args, **kwargs):
        result = complete_round(self, *args, **kwargs)
        if kwargs.get("review_context"):
            raise SimulatedPowerLoss()
        return result

    class Correction:
        def review(self, request, response_text, *, round_id, cancel_event):
            return ResponseReview(context="PENDING_REVIEW_MARKER: use the available result.",
                                  receipt={"plugin": "synthetic_review_fixture"})

    requests = []
    path = tmp_path / "kernel.db"
    store = HarnessStore(path)
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="pending")
    first = TurnRunner(store, model_adapter=model_fixture(monkeypatch, requests),
                       tool_provider=NoTools(), response_reviewer=Correction())
    try:
        with monkeypatch.context() as fault:
            fault.setattr(TurnRunner, "_complete_model_round", crash_after_review_commit)
            with pytest.raises(SimulatedPowerLoss):
                first.run(scope, prompt="Complete the first task.")
        assert len(requests) == 1
    finally:
        first.close()
        store.close()
    reopened = HarnessStore(path)
    second = TurnRunner(reopened, model_adapter=model_fixture(monkeypatch, requests), tool_provider=NoTools())
    try:
        assert second.resume(scope).status is TurnStatus.SUCCEEDED
        assert len(requests) == 2
        assert "PENDING_REVIEW_MARKER" in json.dumps(requests[-1]["messages"])
        assert len([m for m in TranscriptStore(reopened).messages(scope) if m.item_id.startswith(RESPONSE_REVIEW_PREFIX)]) == 1
    finally:
        second.close()
        reopened.close()


@pytest.mark.parametrize("broken_neighbor", [False, True])
def test_combined_review_keeps_each_real_fork_cost_and_partial_failure(tmp_path, monkeypatch, broken_neighbor):
    from pulse_harness.plugins.coverme import WikiEntry, WikiIndex
    from pulse_system.service.coverme import CoverMeRuntime, recorded_fork_usage

    class BrokenReview:
        def review(self, *args, **kwargs):
            raise ValueError("A synthetic neighboring plugin failure")

    requests = []
    storage = Storage()
    memory = PostureMemory(storage, "world")
    memory.add_experience(**episode(scope="subject"))
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None, posture_memory=memory, expert_active=False)
    cover = CoverMeRuntime(tmp_path / "coverme.db", WikiIndex((WikiEntry(
        "fixture", "SYNTHETIC_FINAL_CANDIDATE", "A synthetic source used only for accounting.",
        "https://example.invalid/synthetic-source"),)), guard=lambda: None)
    store = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", "subject", "session", 0, "owner", context_id="new-task", turn_id="turn")
    model = model_fixture(monkeypatch, requests)
    neighbors = [("CoverMe", cover.reviewer(store, model, scope))]
    if broken_neighbor:
        neighbors.insert(0, ("broken_fixture", BrokenReview()))
    neighbors.append(("expert_computation", expert.posture_reviewer(store, model, scope)))
    runner = TurnRunner(store, model_adapter=model, tool_provider=NoTools(),
                        turn_advisor=expert.advisor(store, model, scope),
                        response_reviewer=CombinedResponseReviewer(tuple(neighbors)))
    try:
        assert runner.run(scope, prompt="Produce the requested first experience.").status is TurnStatus.SUCCEEDED
        assert len(requests) == 4
        expert_usage, expert_accounted = recorded_expert_usage(store, scope)
        cover_usage, cover_accounted = recorded_fork_usage(store, scope)
        assert expert_usage["provider_requests"] == 2 and expert_usage["input_tokens"] == 20
        assert cover_usage["provider_requests"] == 1 and cover_usage["input_tokens"] == 10
        assert expert_accounted is (not broken_neighbor)
        assert cover_accounted is (not broken_neighbor)
        envelope = next(decode_review_envelope(m.content) for m in TranscriptStore(store).messages(scope)
                        if m.item_id.startswith(RESPONSE_REVIEW_PREFIX))
        receipts = review_receipts(envelope["receipt"])
        assert {r["plugin"] for r in receipts} >= {"CoverMe", "expert_computation"}
        if broken_neighbor:
            assert next(r for r in receipts if r["plugin"] == "broken_fixture")["accounting_incomplete"]
    finally:
        runner.close()
        expert.close()
        cover.close()
        store.close()
        storage.close()


def test_cancelled_posture_boundary_never_calls_provider(tmp_path, monkeypatch):
    requests = []
    storage = Storage()
    memory = PostureMemory(storage, "world")
    memory.add_experience(**episode(context_id=""))
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None, posture_memory=memory, expert_active=False)
    store = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")
    model = model_fixture(monkeypatch, requests)
    request = ModelRequest(scope, messages=(), tools=(), cursor=0)
    cancelled = Event()
    cancelled.set()
    try:
        with pytest.raises(CancellationRequested):
            expert.advisor(store, model, scope).advise(request, cancel_event=cancelled)
        with pytest.raises(CancellationRequested):
            expert.posture_reviewer(store, model, scope).review(request, "Candidate", round_id="round", cancel_event=cancelled)
        assert requests == []
        assert expert.store.decisions() == []
    finally:
        expert.close()
        model.close()
        store.close()
        storage.close()


@pytest.mark.parametrize("checkpoint", ["before_action", "delivery"])
@pytest.mark.parametrize("revision_change", ["withdrawn", "revised"])
def test_unadmitted_saved_computation_respects_feedback_after_restart(tmp_path, monkeypatch, checkpoint, revision_change):
    requests = []
    storage = Storage(tmp_path / "world.db")
    memory = PostureMemory(storage, "world")
    record = memory.add_experience(**episode(context_id=""))
    path = tmp_path / "expert.db"
    expert = ExpertRuntime(path, guard=lambda: None, posture_memory=memory, expert_active=False)
    store = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="unadmitted")
    store.create_session(scope.session_scope())
    store.create_turn(scope, "A task interrupted before advice admission.")
    request = ModelRequest(scope, messages=(), tools=(), cursor=0)
    model = model_fixture(monkeypatch, requests, all_contributions=True)

    def compute(runtime):
        if checkpoint == "before_action":
            return runtime.advisor(store, model, scope).advise(request, cancel_event=Event())
        return runtime.posture_reviewer(store, model, scope).review(
            request, "A synthetic candidate", round_id="source-round", cancel_event=Event())

    try:
        first = compute(expert)
        assert first.context and len(requests) == 1
        assert not any(message.item_id.startswith((TURN_ADVICE_PREFIX, RESPONSE_REVIEW_PREFIX))
                       for message in TranscriptStore(store).messages(scope))
        expert.close()
        if revision_change == "withdrawn":
            memory.record_feedback(record["id"], expected_revision=1, verdict="withdrawn",
                                   evidence="A later actual result contradicted the original generalization.")
        else:
            memory.revise_experience(record["id"], expected_revision=1,
                                     changes={"lesson": "A newly narrowed lesson requires fresh consideration."})
        expert = ExpertRuntime(path, guard=lambda: None, posture_memory=memory, expert_active=False)
        recovered = compute(expert)
        assert recovered.context is None
        assert recovered.receipt["status"] == "suppressed_before_admission"
        assert recovered.receipt["suppressed_posture_revisions"] == [{"id": record["id"], "revision": 1}]
        assert recovered.receipt["fork"] == first.receipt["fork"]
        assert len(requests) == 1
        assert expert.store.decision(first.receipt["decision_id"])["context"] == first.context
    finally:
        expert.close()
        model.close()
        store.close()
        storage.close()


class ReviewFixture:
    def __init__(self, context=None, receipt=None):
        self.context, self.receipt = context, receipt if receipt is not None else {}

    def review(self, *args, **kwargs):
        return ResponseReview(context=self.context, receipt=self.receipt)


def test_invalid_neighbor_receipt_does_not_erase_valid_review_or_cost():
    valid = {"plugin": "valid_fixture", "provider_usage": {"input_tokens": 7}}
    reviewer = CombinedResponseReviewer((
        ("invalid_fixture", ReviewFixture("Invalid sibling", {"plugin": "invalid_fixture", "score": float("nan")})),
        ("valid_fixture", ReviewFixture("VALID_REVIEW_MARKER", valid)),
    ))
    result = reviewer.review(None, "Candidate", round_id="round", cancel_event=Event())
    assert result.context == "VALID_REVIEW_MARKER"
    receipts = review_receipts(result.receipt)
    assert receipts[0]["accounting_incomplete"]
    assert receipts[1]["provider_usage"] == valid["provider_usage"]


def test_invalid_review_context_keeps_its_receipt_and_valid_neighbor():
    reviewer = CombinedResponseReviewer((
        ("invalid_context", ReviewFixture(["not text"], {"plugin": "invalid_context", "provider_usage": {"input_tokens": 5}})),
        ("valid_fixture", ReviewFixture("VALID_NEIGHBOR_MARKER", {"plugin": "valid_fixture"})),
    ))
    result = reviewer.review(None, "Candidate", round_id="round", cancel_event=Event())
    assert result.context == "VALID_NEIGHBOR_MARKER"
    omitted = review_receipts(result.receipt)[0]
    assert omitted["context_reason"] == "invalid_context"
    assert omitted["provider_usage"]["input_tokens"] == 5
    assert not omitted.get("accounting_incomplete")


def test_combined_review_budgets_exact_host_envelope_and_keeps_whole_contexts():
    # Each text separately fits. Escaping plus the retained private candidate
    # and receipt metadata makes the first combination too large.
    reviewer = CombinedResponseReviewer((
        ("large_fixture", ReviewFixture('"' * 700, {"plugin": "large_fixture", "provider_usage": {"input_tokens": 5}})),
        ("small_fixture", ReviewFixture("COMPLETE_SMALL_CONTRIBUTION", {"plugin": "small_fixture"})),
    ), max_payload_bytes=1600)
    candidate = "Candidate " + "\\" * 100
    result = reviewer.review(None, candidate, round_id="round", cancel_event=Event())
    assert result.context == "COMPLETE_SMALL_CONTRIBUTION"
    receipts = review_receipts(result.receipt)
    assert receipts[0]["context_reason"] == "review_envelope_budget"
    assert receipts[0]["provider_usage"]["input_tokens"] == 5
    encoded = encode_review_envelope(round_id="round", response_id="round:response",
                                    response_digest="0" * 64, candidate_text=candidate,
                                    context=result.context, receipt=result.receipt)
    assert len(encoded.encode()) <= 1600


def test_unrepresentable_combined_receipt_budget_is_explicitly_unaccounted():
    reviewer = CombinedResponseReviewer(tuple(
        (f"reviewer_{index}", ReviewFixture(receipt={"plugin": f"reviewer_{index}", "metadata": "x" * 850}))
        for index in range(8)
    ), max_payload_bytes=1024)
    result = reviewer.review(None, "Candidate", round_id="round", cancel_event=Event())
    assert result.context is None
    assert any(receipt.get("accounting_incomplete") for receipt in review_receipts(result.receipt))
    encoded = encode_review_envelope(round_id="round", response_id="round:response", response_digest="0" * 64,
                                    candidate_text=None, context=None, receipt=result.receipt)
    assert len(encoded.encode()) <= 1024


def test_parallel_context_updates_do_not_overwrite_each_other():
    storage = Storage()
    memories = [PostureMemory(storage, "world"), PostureMemory(storage, "world")]
    try:
        def add(index):
            return memories[index].add_experience(**episode(context_id=f"context-{index}"))
        with ThreadPoolExecutor(max_workers=2) as executor:
            saved = list(executor.map(add, range(2)))
            changed = list(executor.map(lambda index: memories[index].revise_experience(
                saved[index]["id"], expected_revision=1, changes={"title": f"Revised in context {index}"}), range(2)))
        assert {record["id"] for record in memories[0].list_experiences(subject_id="subject")} == {record["id"] for record in saved}
        for index in range(2):
            local = memories[index].materials(subject_id="subject", context_id=f"context-{index}")["episodes"]
            assert len(local) == 1 and local[0]["revision"] == 2
            assert local[0]["title"] == changed[index]["title"]
    finally:
        storage.close()


def test_fence_between_read_and_write_does_not_save_prepared_revision():
    from pulse_harness.core.errors import LeaseFenced

    storage = Storage()
    memory = PostureMemory(storage, "world")
    saved = memory.add_experience(**episode())
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise LeaseFenced("The host writer lease was revoked before persistence.")

    fenced = PostureMemory(storage, "world", guard=guard)
    try:
        with pytest.raises(LeaseFenced):
            fenced.record_feedback(saved["id"], expected_revision=1, verdict="withdrawn",
                                   evidence="A correction prepared by a now-fenced turn.")
        assert calls == 3
        assert memory.get_experience(saved["id"]) == saved
    finally:
        storage.close()


@pytest.mark.parametrize("at_capacity", [False, True])
def test_world_publication_revoke_before_physical_commit_rolls_back_posture(monkeypatch, at_capacity):
    from pulse_system.core.runtime.publication import RuntimePublicationError, RuntimePublicationGate

    storage = Storage()
    memory = PostureMemory(storage, "world")
    saved = memory.add_experience(**episode())
    if at_capacity:
        size = len(json.dumps(storage.load_component_state(memory.component), ensure_ascii=False,
                              sort_keys=True, separators=(",", ":")).encode())
        monkeypatch.setattr("pulse_system.service.posture.MAX_STATE_BYTES", size + 1)
    gate = RuntimePublicationGate("owner", 1)
    storage.bind_runtime_publication_permit(gate.publication_permit)
    ready, proceed = Event(), Event()
    connection_type = type(storage._conn)
    actual_commit = connection_type.commit

    def pause_after_mutation(connection):
        if connection is storage._conn:
            ready.set()
            assert proceed.wait(5), "The test did not release the physical commit boundary."
        return actual_commit(connection)

    try:
        with monkeypatch.context() as fault:
            fault.setattr(connection_type, "commit", pause_after_mutation)
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(memory.record_feedback, saved["id"], expected_revision=1,
                                          verdict="withdrawn", evidence="A write racing with native shutdown.")
                try:
                    assert ready.wait(5), "The write did not reach its physical commit boundary."
                    assert gate.snapshot().active_publication_transactions == 1
                    gate.revoke(reason="test_posture_shutdown")
                finally:
                    proceed.set()
                with pytest.raises(RuntimePublicationError):
                    pending.result(timeout=5)
        assert memory.get_experience(saved["id"]) == saved
        assert not storage._conn.in_transaction
        assert gate.snapshot().active_publication_transactions == 0
    finally:
        proceed.set()
        storage.close()


def test_parent_cancellation_during_provider_stream_closes_fork_without_learning(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTURE_TEST_KEY", "synthetic-transport-key")
    cancelled, stream_closed = Event(), Event()
    requests = []

    class CancellingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"index":0,"delta":{"content":"Unfinished work"},"finish_reason":null}]}\n\n'
            cancelled.set()
            yield b'data: [DONE]\n\n'

        def close(self):
            stream_closed.set()

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=CancellingStream())

    storage = Storage()
    memory = PostureMemory(storage, "world")
    memory.add_experience(**episode(context_id=""))
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None, posture_memory=memory, expert_active=False)
    store = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="cancel-mid-stream")
    store.create_session(scope.session_scope())
    store.create_turn(scope, "A task cancelled while the optional computation runs.")
    request = ModelRequest(scope, messages=(), tools=(), cursor=0)
    model = ChatHTTPModelAdapter(
        ChatHTTPConfig("https://fixture.invalid/chat/completions", "posture-cancellation-fixture", credential_env="POSTURE_TEST_KEY"),
        transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CancellationRequested):
            expert.advisor(store, model, scope).advise(request, cancel_event=cancelled)
        assert len(requests) == 1 and stream_closed.is_set()
        assert expert.store.decisions() == []
        assert not any(message.item_id.startswith(TURN_ADVICE_PREFIX) for message in TranscriptStore(store).messages(scope))
        sessions = expert.trace.sessions_for_world("world")
        assert len(sessions) == 1
        rounds = expert.trace.model_rounds_for_session(sessions[0].scope)
        assert len(rounds) == 1 and rounds[0].status.value == "cancelled"
    finally:
        expert.close()
        model.close()
        store.close()
        storage.close()


def test_complete_prompt_budget_preserves_experience_boundaries_and_exposes_omissions():
    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        memory.add_experience(**episode(
            conditions={"all": ["Relevant condition " + "x" * 700 for _ in range(8)]},
            counterexamples=["Preserve exact task scope " + "y" * 1800 for _ in range(3)],
        ))
        posture = pack_posture_materials(memory.materials(subject_id="subject", context_id="origin"))
        assert len(posture["episodes"]) == 1
        posture["omitted_posture_revisions"] = [{"id": str(index) + "z" * 155, "revision": 1} for index in range(127)]
        before = json.dumps(posture, sort_keys=True)
        view = {"messages": [{"role": "user", "content": "q" * 4300} for _ in range(4)],
                "latest_tool_results": '"' * 6000}
        materials = {
            "episodes": [{"text": "u" * 1700} for _ in range(5)],
            "previous_computations": [{"id": f"past-{index}", "source": {}, "situation": "s" * 1000,
                                       "context": "c" * 1800, "outcome": {"result": "o" * 1400},
                                       "judgment": {"text": "j" * 1200}} for index in range(4)],
        }
        prompt = make_work_prompt(view, materials, model_round=1, posture_packet=posture,
                                  review_candidate='"' * 16000, byte_budget=64 * 1024)
        assert len(prompt.encode()) <= 64 * 1024
        packet = json.loads(prompt.split("WORK_MATERIAL_JSON\n", 1)[1])
        visible = packet["posture_experiences"]
        assert visible["episodes"] == posture["episodes"]
        assert visible["offered_posture_revisions"] == posture["offered_posture_revisions"]
        assert visible["omitted_posture_revision_count"] == 127
        assert len(visible["omitted_posture_revisions_digest"]) == 64
        assert "omitted_posture_revisions" not in visible
        assert packet["candidate_response_clipped"]
        assert packet["omitted_previous_computations"] == len(materials["previous_computations"])
        assert packet["omitted_episodes"] == len(materials["episodes"])
        assert json.dumps(posture, sort_keys=True) == before
        with pytest.raises(WorkMaterialBudgetExceeded):
            make_work_prompt({}, {"episodes": [], "previous_computations": []}, model_round=0,
                             posture_packet=posture, byte_budget=1024)
    finally:
        storage.close()


def test_unfit_complete_prompt_skips_both_checkpoints_without_provider_call(tmp_path, monkeypatch):
    storage = Storage()
    memory = PostureMemory(storage, "world")
    saved = memory.add_experience(**episode(context_id=""))
    expert = ExpertRuntime(tmp_path / "expert.db", guard=lambda: None, posture_memory=memory, expert_active=False)
    expert.trace.max_payload_bytes = 1024
    store = HarnessStore(tmp_path / "kernel.db")
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="bounded")
    store.create_session(scope.session_scope())
    store.create_turn(scope, "A task with a deliberately insufficient material budget.")
    request = ModelRequest(scope, messages=(), tools=(), cursor=0)
    requests = []
    model = model_fixture(monkeypatch, requests)
    try:
        before = expert.advisor(store, model, scope).advise(request, cancel_event=Event())
        delivery = expert.posture_reviewer(store, model, scope).review(
            request, "A synthetic candidate", round_id="round", cancel_event=Event())
        for result in (before, delivery):
            assert result.context is None
            assert result.receipt["status"] == "materials_exceed_budget"
            assert result.receipt["offered_posture_revisions"] == []
            assert result.receipt["omitted_posture_revisions"] == [{"id": saved["id"], "revision": 1}]
        assert requests == [] and expert.store.decisions() == []
    finally:
        expert.close()
        model.close()
        store.close()
        storage.close()


def test_full_journal_still_pauses_with_history_cas_and_restart(tmp_path, monkeypatch):
    path = tmp_path / "world.db"
    storage = Storage(path)
    memory = PostureMemory(storage, "world")
    records = [memory.add_experience(**episode(experience_id=f"full-{index}")) for index in range(4)]
    original = storage.load_component_state(memory.component)
    size = len(json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    try:
        with monkeypatch.context() as capacity:
            capacity.setattr("pulse_system.service.posture.MAX_STATE_BYTES", size + 1)
            paused = memory.revise_experience(records[0]["id"], expected_revision=1, changes={"active": False})
            harmful = memory.record_feedback(records[1]["id"], expected_revision=1, verdict="harmful",
                                             evidence="An actual user correction must remain possible at capacity.",
                                             source={"kind": "operator_report", "message_id": "later-feedback"})
            withdrawn = memory.record_feedback(records[2]["id"], expected_revision=1, verdict="withdrawn",
                                               evidence="Stop using the old generalization.")
            with pytest.raises(ValueError, match="exceeds its bound"):
                memory.revise_experience(records[3]["id"], expected_revision=1,
                                         changes={"active": False, "lesson": "This combined change must not be partly saved."})
            assert memory.get_experience(records[3]["id"]) == records[3]
            assert storage.load_component_state(memory.component) == original
            assert len(storage.load_component_state(memory.control_component)["stops"]) == 3
            storage.close()
            storage = Storage(path)
            memory = PostureMemory(storage, "world")
            for previous, result in zip(records, (paused, harmful, withdrawn)):
                assert memory.get_experience(previous["id"]) == result
                assert result["revision"] == 2 and not result["active"]
                assert result["history"] == [{key: value for key, value in previous.items() if key != "history"}]
                with pytest.raises(ValueError, match="revision conflict"):
                    memory.revise_experience(previous["id"], expected_revision=1, changes={"active": True})
            with pytest.raises(ValueError, match="exceeds its bound"):
                memory.revise_experience(records[0]["id"], expected_revision=2, changes={"active": True})
            assert [entry["id"] for entry in memory.materials(subject_id="subject", context_id="origin")["episodes"]] == [records[3]["id"]]
        # When the primary journal can hold a deliberate later revision, that
        # one durable write absorbs the control and its full evidence/history.
        resumed = memory.revise_experience(records[1]["id"], expected_revision=2, changes={"active": True})
        assert resumed["revision"] == 3 and resumed["active"]
        assert memory.get_experience(records[1]["id"]) == resumed
        assert resumed["feedback"] == harmful["feedback"]
        assert resumed["history"][-1]["active"] is False
        assert storage.load_component_state(memory.control_component)["stops"][records[1]["id"]]["revision"] == 2
    finally:
        storage.close()


def test_stop_control_capacity_covers_all_128_maximal_feedback_records(monkeypatch):
    from pulse_system.service.posture import MAX_CONTROL_STATE_BYTES, MAX_EXPERIENCES

    storage = Storage()
    memory = PostureMemory(storage, "world")
    try:
        records = [memory.add_experience(**episode(experience_id=f"p{index:03d}" + "x" * 156))
                   for index in range(MAX_EXPERIENCES)]
        original = storage.load_component_state(memory.component)
        size = len(json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        monkeypatch.setattr("pulse_system.service.posture.MAX_STATE_BYTES", size + 1)
        source = {"kind": "operator_report", "evidence": ""}
        source["evidence"] = "s" * (8192 - len(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()))
        for record in records:
            result = memory.record_feedback(record["id"], expected_revision=1, verdict="withdrawn",
                                            evidence="\x00" * 6000, source=source)
            assert result["revision"] == 2 and not result["active"]
        controls = storage.load_component_state(memory.control_component)
        assert len(controls["stops"]) == MAX_EXPERIENCES
        assert MAX_EXPERIENCES * (6000 * 6 + 8192 + 1024) + 4096 < MAX_CONTROL_STATE_BYTES
        assert len(json.dumps(controls, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= MAX_CONTROL_STATE_BYTES
        assert storage.load_component_state(memory.component) == original
        assert memory.materials(subject_id="subject", context_id="origin")["episodes"] == []
    finally:
        storage.close()
