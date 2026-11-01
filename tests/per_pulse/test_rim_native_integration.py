"""Actual native RIM selection -> TUNNEL -> restart integration.

Adapted from the upstream P-Pulse RIM #44 source-bound acceptance test,
pinned at 0c696b015c9a94961ffddc6ae87c3bd9e56b6506.
The HTTP model transport below is explicitly scripted. It tests real
World/Kernel/SQLite execution, not provider learning or quality improvement.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json
import random
import time

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_system.service.kernel_runtime import KernelHarnessFactory
from pulse_system.service.runtime import RuntimeService, RuntimeServiceConfig
from pulse_system.service.execution_preference import choose
from pulse_system.service.shared_contributions import retained as retained_shared, selected_for_inspection
from pulse_harness.core.errors import ScopeConflict


INSTRUCTION = "Inspect the cache warmup and identify the exact source of startup latency."
START = "Select one collaborator with prior work outcomes"
INSPECT = "Inspect the earlier selected consultation in this Work."


def _stream(delta, finish="stop"):
    chunks = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        {"choices": [], "usage": {"prompt_tokens": 16, "completion_tokens": 8, "total_tokens": 24}},
    ]
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(part) + "\n\n" for part in chunks)
        + "data: [DONE]\n\n",
    )


def _answer(content):
    return _stream({"content": content})


def _model_factory(state, seen):
    def factory(scope):
        def respond(request):
            body = json.loads(request.content)
            seen.append((scope.engram_id, body))
            last = body["messages"][-1]
            if last["role"] == "tool":
                payload = json.loads(last["content"])
                if isinstance(payload, dict) and "output" in payload:
                    state["tool_output"] = payload["output"]
                return _answer("Consultation prepared in the original Work.")
            if last.get("content") == INSPECT:
                return _stream({"tool_calls": [{
                    "index": 0, "id": "rim-inspect", "type": "function",
                    "function": {"name": "team", "arguments": json.dumps({
                        "op": "inspect", "record_id": state["inspect_record_id"],
                    })},
                }]}, finish="tool_calls")
            if last.get("content") == START:
                arguments = {
                    "op": "consult_adaptive",
                    "candidates": {state["bad"]: 0.6, state["good"]: 0.4},
                    "instruction": INSTRUCTION,
                }
                if "reference_record_id" in state:
                    arguments["contributions"] = [{
                        "record_id": state["reference_record_id"],
                        "excerpt": state["reference_excerpt"],
                    }]
                return _stream({"tool_calls": [{
                    "index": 0, "id": "rim-choice", "type": "function",
                    "function": {"name": "team", "arguments": json.dumps(arguments)},
                }]}, finish="tool_calls")
            if (last.get("content") or "").startswith(INSTRUCTION):
                return _answer("A real native TUNNEL contribution about cache warmup.")
            return _answer("The original Work received and used the collaboration.")

        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions",
                           "EXPLICIT_SCRIPTED_RIM_FIXTURE",
                           credential_env="PER_PULSE_RIM_TEST_KEY"),
            transport=httpx.MockTransport(respond),
        )
    return factory


def _world(path, factory):
    config = RuntimeServiceConfig(
        db_path=path / "world.db", workspace=path, harness_kind="kernel",
        mock=False, silence_threshold=0, default_max_wait=0,
        base_spontaneous_rate=0, harness_turn_timeout_sec=5,
        runtime_shutdown_timeout_sec=2,
    )
    harness = KernelHarnessFactory(
        db_path=path / "kernel.db", workspace=path / "files",
        model_factory=factory, allow_unconfigured=factory is None,
        allow_mutations=True,
    )
    return RuntimeService(config, harness_factory=harness)


def _tick(world, predicate, *, seconds=12, drain=True):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, (
            world.storage.native_team_requests(limit=64),
            world.storage.list_delegations(limit=16),
        )
        # Team TUNNEL results are reconciled outside engine.tick().
        # Mirror the native runtime acceptance loop so rated prior
        # delegations actually settle before we inspect their provenance.
        if drain:
            world._native_team.drain()
            world._reconcile_durable_delegations()
            world._native_work.drain()
        world.engine.tick()
        time.sleep(0.01)


def test_real_rated_outcomes_change_native_team_choice_then_recovery_delivers_once(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("P_PULSE_RIM_POLICY", "1")
    monkeypatch.setenv("PER_PULSE_RIM_TEST_KEY", "EXPLICIT_TEST_ONLY")
    monkeypatch.setattr(random.SystemRandom, "random", lambda self: 0.5)
    state, seen = {}, []
    factory = _model_factory(state, seen)

    world = _world(tmp_path, factory)
    owner = world.continuity_engram_id
    front = world.world.create_task_bundle_for_existing_engram(
        "Startup performance investigation", owner
    ).front
    first = world.engrams.create(auto_name=False).id
    second = world.engrams.create(auto_name=False).id
    state["bad"], state["good"] = sorted([first, second])
    for collaborator in (first, second):
        world.storage.add_center_membership(front.center_id, collaborator)
    records = []
    try:
        for collaborator, outcome in (
            (state["bad"], "discarded"), (state["good"], "adopted")
        ):
            record_id = world.delegate(
                INSTRUCTION, to=collaborator, caller_id=owner,
                center_id=front.center_id, idempotency_key="prior-" + collaborator,
            )
            _tick(world, lambda record_id=record_id:
                  world.storage.get_delegation(record_id)["result_summary"] is not None
                  and not world.harness._active)
            update = world.record_delegation_outcome(record_id, outcome)
            assert update["outcome"] == outcome
            records.append(record_id)

        # A real, separate Work already contains a completed collaborator
        # result. Selecting one exact excerpt is a narrow material handoff,
        # NOT a cross-Work RIM reward or permission to read its transcript.
        reference_work = world.world.create_task_bundle_for_existing_engram(
            "Earlier startup investigation", owner
        ).front
        world.storage.add_center_membership(reference_work.center_id, state["good"])
        reference_id = world.delegate(
            INSTRUCTION, to=state["good"], caller_id=owner,
            center_id=reference_work.center_id, idempotency_key="source-reference",
        )
        _tick(world, lambda: world.storage.get_delegation(reference_id)["result_summary"] is not None
              and world.causal_ledger.find_delegation_result_by_record_id(
                  world.world_id, reference_id) is not None and not world.harness._active)
        source_text = world.storage.get_delegation(reference_id)["result_summary"]
        state["reference_record_id"] = reference_id
        state["reference_excerpt"] = source_text[:96]
        assert source_text.startswith("A real native TUNNEL contribution")
        assert len(world.storage.list_delegations(caller_id=owner)) == 3
        world.send_task_front_message(front.id, START)
        # Hold the native dispatch while persisting exactly one model-selected
        # consultation: an interrupted process must not resample its target.
        _tick(world, lambda: bool(world.storage.native_team_requests())
              and not world.harness._active, drain=False)
        pending = world.storage.native_team_requests()[0]
        preference = pending["preference"]
        assert pending["status"] == "prepared"
        assert preference["selected_expert_id"] == state["good"]
        assert preference["probabilities"][state["good"]] > 0.4
        assert set(item["id"] for item in preference["evidence"]) == set(records)
        assert len(preference["evidence"]) == 2
        assert all(len(item["sources"]) == 2 for item in preference["evidence"])
        assert preference["kl"] <= 0.15 + 1e-12
        assert len(world.storage.list_delegations(caller_id=owner)) == 3
        assert pending["shared_contributions"][0]["source"]["record_id"] == reference_id
        assert pending["shared_contributions"][0]["source"]["work_id"] == reference_work.id
        saved_request_id = pending["request_id"]
        persisted_preference = json.dumps(preference, sort_keys=True)
    finally:
        world.close()

    monkeypatch.setattr(random.SystemRandom, "random", lambda self: 0.0)
    reopened = _world(tmp_path, factory)
    try:
        # A durable delegate's result_summary may arrive before the causal
        # delivery is reconciled. Keep pumping the real native loop until the
        # exact selected record has a settled result anchor too; checking only
        # the SQLite summary would falsely declare the user-visible return done.
        def _returned_with_origin():
            requests = reopened.storage.native_team_requests()
            if not requests or requests[0]["status"] != "delivered":
                return False
            rows = reopened.storage.list_delegations(caller_id=owner)
            if len(rows) != 4 or not all(row["result_summary"] is not None for row in rows):
                return False
            selected = requests[0]["record_id"]
            return reopened.causal_ledger.find_delegation_result_by_record_id(
                reopened.world_id, selected
            ) is not None

        _tick(reopened, _returned_with_origin)
        recovered = reopened.storage.native_team_requests()[0]
        assert recovered["request_id"] == saved_request_id
        assert json.dumps(recovered["preference"], sort_keys=True) == persisted_preference
        new_record = reopened.storage.get_delegation(recovered["record_id"])
        assert new_record["target_id"] == state["good"]
        assert new_record["task"].startswith(INSTRUCTION)
        assert "Selected contributions shared for review" in new_record["task"]
        assert reference_id in new_record["task"]
        assert new_record["outcome"] is None  # completion never becomes reward
        assert reopened.causal_ledger.find_delegation_result_by_record_id(
            reopened.world_id, new_record["id"]
        ) is not None
        original_request = reopened.causal_ledger.get_event(
            recovered["delegation_event_id"]
        )
        # An operator can inspect the original, fixed choice and its native
        # source IDs after restart, without recomputation or unrelated Works.
        inspected_choice = reopened._native_team._retained_adaptive_choice(
            owner, new_record, original_request, front)
        assert inspected_choice == preference
        assert inspected_choice is not recovered["preference"]
        assert {entry["id"] for entry in inspected_choice["evidence"]} == set(records)
        assert reopened._native_team._retained_adaptive_choice(
            owner, new_record, original_request, reference_work) is None
        # Exercise the ordinary model-facing team.inspect action in the same
        # native Work (not only the read helper). A completed tool response is
        # source-bound and must still show the original selection after reboot.
        state["inspect_record_id"] = new_record["id"]
        state.pop("tool_output", None)
        reopened.send_task_front_message(front.id, INSPECT)
        _tick(reopened, lambda: isinstance(state.get("tool_output"), dict)
              and "adaptive_preference" in state["tool_output"]
              and not reopened.harness._active)
        inspected_output = state["tool_output"]
        assert inspected_output["record_id"] == new_record["id"]
        assert inspected_output["adaptive_preference"] == preference
        assert inspected_output["result"].startswith("A real native TUNNEL contribution")
        explicit_sources = retained_shared(reopened, owner, original_request)
        assert len(explicit_sources) == 1
        assert explicit_sources[0]["source"]["record_id"] == reference_id
        assert selected_for_inspection(reopened, recovered, reference_id) == explicit_sources[0]
        with pytest.raises(ScopeConflict):
            selected_for_inspection(reopened, recovered, records[0])

        # Later explicit contrary feedback changes only the NEXT choice.
        reopened.record_delegation_outcome(new_record["id"], "discarded")
        selection_scope = reopened.harness.scope_for(owner, "work:" + front.center_id)
        later = choose(reopened._native_team, selection_scope, front, {
            "candidates": {state["bad"]: 0.6, state["good"]: 0.4},
            "instruction": INSTRUCTION,
        })
        # A contrary rated outcome must lower the underlying preference.
        # At the KL bound, two different preference values can project to
        # the same probabilities: an additional negative outcome may not
        # shift an already-saturated candidate distribution.
        assert (later["feedback_values"][state["good"]]
                < preference["feedback_values"][state["good"]])
        assert (later["probabilities"][state["good"]]
                <= preference["probabilities"][state["good"]] + 1e-12)
        if abs(later["probabilities"][state["good"]] - preference["probabilities"][state["good"]]) < 1e-10:
            assert abs(preference["kl"] - 0.15) < 1e-8
            assert abs(later["kl"] - 0.15) < 1e-8
        assert new_record["id"] in {row["id"] for row in later["evidence"]}
        assert recovered["preference"]["selected_expert_id"] == state["good"]
        for _ in range(3):
            reopened._native_team.drain()
        assert len(reopened.storage.list_delegations(caller_id=owner)) == 4

        # The same words in an unrelated Work are NOT transferable evidence.
        other = reopened.world.create_task_bundle_for_existing_engram(
            "Unrelated private investigation", owner
        ).front
        other_scope = reopened.harness.scope_for(owner, "work:" + other.center_id)
        neutral = choose(reopened._native_team, other_scope, other, {
            "candidates": {state["bad"]: 0.6, state["good"]: 0.4},
            "instruction": INSTRUCTION,
        })
        assert not neutral["changed"]
        assert neutral["evidence"] == []
        assert neutral["probabilities"] == neutral["prior"]
        assert len(reopened.storage.list_engrams()) == 3
    finally:
        reopened.close()


@pytest.mark.parametrize("flag", ["0", "1"])
def test_offline_runtime_never_invents_an_adaptive_team_action(tmp_path, monkeypatch, flag):
    monkeypatch.setenv("P_PULSE_RIM_POLICY", flag)
    runtime = _world(tmp_path, None)
    try:
        assert runtime.harness._rim_policy is (flag == "1")
        assert runtime.storage.native_team_requests(limit=64) == []
        assert runtime.storage.list_delegations() == []
    finally:
        runtime.close()
