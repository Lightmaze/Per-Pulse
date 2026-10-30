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


INSTRUCTION = "Inspect the cache warmup and identify the exact source of startup latency."
START = "Select one collaborator with prior work outcomes"


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
            if last.get("content") == START:
                arguments = {
                    "op": "consult_adaptive",
                    "candidates": {state["bad"]: 0.6, state["good"]: 0.4},
                    "instruction": INSTRUCTION,
                }
                return _stream({"tool_calls": [{
                    "index": 0, "id": "rim-choice", "type": "function",
                    "function": {"name": "team", "arguments": json.dumps(arguments)},
                }]}, finish="tool_calls")
            if last.get("content") == INSTRUCTION:
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
        model_factory=factory, allow_mutations=True,
    )
    return RuntimeService(config, harness_factory=harness)


def _tick(world, predicate, *, seconds=12, drain=True):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, (
            world.storage.native_team_requests(limit=64),
            world.storage.list_delegations(limit=16),
        )
        world.engine.tick()
        if drain:
            world._native_team.drain()
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

        assert len(world.storage.list_delegations(caller_id=owner)) == 2
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
        assert len(world.storage.list_delegations(caller_id=owner)) == 2
        saved_request_id = pending["request_id"]
        persisted_preference = json.dumps(preference, sort_keys=True)
    finally:
        world.close()

    monkeypatch.setattr(random.SystemRandom, "random", lambda self: 0.0)
    reopened = _world(tmp_path, factory)
    try:
        _tick(reopened, lambda: reopened.storage.native_team_requests()[0]["status"]
              == "delivered" and len(reopened.storage.list_delegations(caller_id=owner)) == 3
              and all(row["result_summary"] is not None for row in
                      reopened.storage.list_delegations(caller_id=owner)))
        recovered = reopened.storage.native_team_requests()[0]
        assert recovered["request_id"] == saved_request_id
        assert json.dumps(recovered["preference"], sort_keys=True) == persisted_preference
        new_record = reopened.storage.get_delegation(recovered["record_id"])
        assert new_record["target_id"] == state["good"]
        assert new_record["task"] == INSTRUCTION
        assert new_record["outcome"] is None  # completion never becomes reward
        assert reopened.causal_ledger.find_delegation_result_by_record_id(
            reopened.world_id, new_record["id"]
        ) is not None

        # Later explicit contrary feedback changes only the NEXT choice.
        reopened.record_delegation_outcome(new_record["id"], "discarded")
        selection_scope = reopened.harness.scope_for(owner, "work:" + front.center_id)
        later = choose(reopened._native_team, selection_scope, front, {
            "candidates": {state["bad"]: 0.6, state["good"]: 0.4},
            "instruction": INSTRUCTION,
        })
        assert later["probabilities"][state["good"]] < preference["probabilities"][state["good"]]
        assert new_record["id"] in {row["id"] for row in later["evidence"]}
        assert recovered["preference"]["selected_expert_id"] == state["good"]
        for _ in range(3):
            reopened._native_team.drain()
        assert len(reopened.storage.list_delegations(caller_id=owner)) == 3

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
