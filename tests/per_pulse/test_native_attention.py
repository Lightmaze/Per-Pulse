"""Focused native attention tests on one real World and durable ContextPulseController.

The Kernel action and provider terminal states are explicit synthetic fixtures.
These tests do not establish real-provider attention quality or spending limits.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from per_pulse.service import PageService, PulseSettings
from pulse_harness.core import HarnessScope
from pulse_harness.core.contracts import ActionStatus, ToolCall, TurnStatus
from pulse_harness.core.provider import CancellationToken
from pulse_system.service.native_attention import NativeAttention, validate


@pytest.fixture
def native(tmp_path, monkeypatch):
    service = PageService(
        tmp_path, model_factory=None, pulse=PulseSettings(
            enabled=True, check_interval_seconds=0.01,
            spontaneous_rate=0, refractory_seconds=0,
        ),
    )
    runtime = service.runtime
    subject = runtime.continuity_engram_id
    initial = runtime.inject(subject, "Explore a useful design before building a form.")
    turn = runtime.causal_ledger.begin_turn(
        initial, subject, "Explore a useful design before building a form.",
        execution_context="", runtime_fence=runtime._current_runtime_fence(),
    )
    scope = HarnessScope(
        runtime.world_id, subject, "synthetic-session", 0, "synthetic-owner",
        context_id="", turn_id=turn.id,
    )
    values = {"op": "hold", "note": "Test interactive value first.", "drive": 0.65}
    states = SimpleNamespace(
        turn=TurnStatus.SUCCEEDED, action=ActionStatus.SUCCEEDED,
        name="attention", arguments=values,
    )
    monkeypatch.setattr(runtime.harness, "authorize_attention", lambda *args: None)
    monkeypatch.setattr(runtime.kernel_service.store, "get_turn",
                        lambda _: SimpleNamespace(status=states.turn))
    monkeypatch.setattr(runtime.kernel_service.store, "get_action",
                        lambda *_: SimpleNamespace(
                            status=states.action, tool_name=states.name,
                            arguments=states.arguments,
                        ))
    env = SimpleNamespace(
        runtime=runtime, subject=subject, initial=initial, turn=turn,
        scope=scope, states=states, values=values, tool=NativeAttention(runtime),
        controller=runtime.context_pulse,
    )
    yield env
    runtime.close()


def stage(env, values=None, action="synthetic-attention-action"):
    return env.tool.execute(
        env.scope, ToolCall("synthetic-call", "attention", values or env.values),
        CancellationToken(), action_request_id=action,
    )


def settle(env):
    env.runtime.causal_ledger.settle_turn(
        env.turn.id, "I compared useful interaction paths.",
        runtime_fence=env.runtime._current_runtime_fence(),
        metadata={"evidence_class": "TEST_DOUBLE"},
    )


def retained(env):
    return env.controller.inspect_attention(None)["retained"]


def test_hold_is_only_applied_after_exact_world_and_kernel_success(native):
    receipt = stage(native)
    assert receipt["status"] == "staged"
    assert retained(native)["note"] == ""
    native.controller.tick()
    assert retained(native)["note"] == ""
    settle(native)
    native.controller.tick()
    value = retained(native)
    assert value["note"] == "Test interactive value first."
    assert value["drive"] == 0.65
    assert value["sourceTurnId"] == native.turn.id
    assert value["sourceActionId"] == "synthetic-attention-action"
    assert value["sourceEventId"] == native.initial
    assert native.controller.snapshot()["budget"]["used"] == 0
    native.controller.tick()
    assert retained(native) == value


@pytest.mark.parametrize("field,value", [
    ("turn", TurnStatus.CANCELLED),
    ("turn", TurnStatus.FAILED),
    ("action", ActionStatus.FAILED),
    ("name", "another_tool"),
])
def test_unsuccessful_or_wrong_source_action_cannot_change_local_thought(native, field, value):
    stage(native)
    settle(native)
    setattr(native.states, field, value)
    native.controller.tick()
    assert retained(native)["note"] == ""
    assert native.controller.snapshot()["budget"]["used"] == 0


def test_tampered_action_arguments_are_not_applied(native):
    stage(native)
    settle(native)
    native.states.arguments = {"op": "hold", "note": "Different note", "drive": 0.65}
    native.controller.tick()
    assert retained(native)["note"] == ""


def test_later_human_input_preempts_older_staged_model_attention(native):
    stage(native)
    later = native.runtime.inject(native.subject, "No—do not retain that assumption.")
    assert later != native.initial
    settle(native)
    native.controller.tick()
    assert retained(native)["note"] == ""


def test_local_operator_hold_preempts_the_pending_model_intent(native):
    stage(native)
    native.controller.set_context_paused(None, True)
    settle(native)
    native.controller.tick()
    assert retained(native)["note"] == ""
    assert native.controller.inspect_attention(None)["contextPaused"] is True
    native.controller.set_context_paused(None, False)
    native.controller.tick()
    assert retained(native)["note"] == ""


def test_paused_background_still_reconciles_a_finished_action(native):
    stage(native)
    native.controller.set_paused(True)
    settle(native)
    native.controller.tick()
    # Operator global pause holds admission; model thought is still durably
    # settled and must not silently launch a background turn.
    assert retained(native)["note"] == "Test interactive value first."
    assert native.controller.snapshot()["paused"] is True
    assert native.controller.snapshot()["budget"]["used"] == 0


def test_identical_staging_is_idempotent_and_different_arguments_rejected(native):
    first = stage(native)
    assert stage(native) == first
    with pytest.raises(ValueError, match="identity changed"):
        stage(native, {"op": "release"})


@pytest.mark.parametrize("payload", [
    {"op": "hold", "note": "", "drive": 0.4},
    {"op": "hold", "note": "Retain", "drive": True},
    {"op": "hold", "note": "Retain", "drive": float("nan")},
    {"op": "hold", "note": "Retain", "drive": 1.1},
    {"op": "inspect", "page_id": "other-work"},
    {"op": "release", "drive": 0.5},
])
def test_attention_rejects_ambiguous_or_unsafe_arguments(payload):
    with pytest.raises(ValueError):
        validate(payload)
