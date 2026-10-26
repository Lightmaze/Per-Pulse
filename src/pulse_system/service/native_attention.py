"""Native model attention for the existing local Pulse computation.

A model can inspect or propose a retained thought in its own Home/Work
context. Holds and releases are *staged* Kernel actions, not immediate World
effects: the single ContextPulseController reconciles them only after the
original World turn and exact Kernel action both succeed. Human input and
operator controls take priority. No second scheduler, context store or model
runner is introduced.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from pulse_harness.core.contracts import ActionStatus, ToolDescriptor, TurnStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.scope import HarnessScope
from pulse_system.core.types import HarnessTurnState

NAME = "attention"
SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["inspect", "hold", "release"]},
        "note": {"type": "string", "maxLength": 4096},
        "drive": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["op"],
    "additionalProperties": False,
}


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        NAME,
        "Inspect or retain a local thought in the continuing subject's current "
        "Home or Work. inspect returns only this context's note, drive, causal "
        "source and operator admission state. hold(note,drive) records a bounded "
        "thought and its prospective local motivation; release clears it. "
        "Both changes are staged until the exact action and whole original "
        "turn succeed. Later human input or operator edits take precedence. "
        "A retained thought does not create a new human instruction, grant "
        "tools, escape this context or bypass Pulse pauses, quotas, inhibition "
        "or the existing causal admission path. Do not use this to continuously "
        "generate tokens; inspect and release are valid when nothing is needed.",
        capability="attention.internal", source="pulse.native-attention",
    )


def validate(arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise ValueError("attention arguments must be an object")
    values = dict(arguments)
    if values == {"op": "inspect"} or values == {"op": "release"}:
        return values
    if set(values) != {"op", "note", "drive"} or values["op"] != "hold":
        raise ValueError("attention expects inspect, hold(note, drive), or release")
    note = values["note"]
    if not isinstance(note, str) or not note.strip() or len(note) > 4096 or "\x00" in note:
        raise ValueError("attention note must contain 1 to 4096 characters")
    drive = values["drive"]
    if (type(drive) not in (int, float) or not math.isfinite(drive)
            or not 0 <= drive <= 1):
        raise ValueError("attention drive must be a finite number in [0, 1]")
    return {"op": "hold", "note": note.strip(), "drive": float(drive)}


class NativeAttention:
    def __init__(self, runtime):
        self.runtime = runtime

    def execute(self, scope, call, cancellation, *, action_request_id=None):
        r = self.runtime
        values = validate(call.arguments)
        r.harness.authorize_attention(scope, action_request_id, call)
        cancellation.raise_if_cancelled()
        controller = r.context_pulse
        if controller is None or not controller.config.enabled:
            raise ScopeConflict("native Pulse attention is disabled")
        center_id = None if not scope.context_id else scope.context_id.removeprefix("work:")
        if scope.context_id and (not scope.context_id.startswith("work:") or not center_id):
            raise ScopeConflict("attention must stay in its original local context")
        if values["op"] == "inspect":
            return controller.inspect_attention(center_id)
        with r.harness._mutation_guard():
            r.harness.authorize_attention(scope, action_request_id, call)
            cancellation.raise_if_cancelled()
            return controller.stage_attention(
                center_id, scope.as_dict(), action_request_id, values,
            )

    def outcome(self, pending: dict) -> dict:
        """Resolve a staged intent from *both* durable authorities.

        Kernel success by itself is insufficient: a result lost or rejected
        during World publication must not silently change the Context Body.
        """
        r = self.runtime
        scope = HarnessScope.from_dict(pending["scope"])
        turn = r.causal_ledger.get_turn_for_world(scope.turn_id, r.world_id)
        if turn is None or turn.state is HarnessTurnState.RUNNING:
            return {"state": "pending"}
        if turn.state is not HarnessTurnState.SETTLED or turn.engram_id != scope.engram_id:
            return {"state": "discard"}
        try:
            kernel_turn = r.kernel_service.store.get_turn(scope)
            action = r.kernel_service.store.get_action(scope, pending["actionId"])
        except (KeyError, ScopeConflict):
            return {"state": "discard"}
        if (kernel_turn.status is not TurnStatus.SUCCEEDED
                or action.status is not ActionStatus.SUCCEEDED
                or action.tool_name != NAME
                or dict(action.arguments) != pending["arguments"]):
            return {"state": "discard"}
        source = r.causal_ledger.get_event(turn.event_id)
        if (source is None or source.world_id != r.world_id
                or source.engram_id != scope.engram_id
                or source.center_id != pending["centerId"]):
            return {"state": "discard"}
        return {"state": "succeeded", "sourceEventId": source.id, "sourceSeq": source.seq}
