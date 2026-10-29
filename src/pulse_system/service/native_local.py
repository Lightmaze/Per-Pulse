"""A local context can inspect and adjust its existing spontaneous tendency.

This is an ordinary, source-bound numeric tool effect. It neither supplies a
new purpose nor schedules a turn. The original Engine still decides whether a
local opportunity fires, and normal content propagation remains unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping

from pulse_harness.core.contracts import ToolDescriptor
from pulse_harness.core.errors import ScopeConflict
from pulse_system.core.types import EngramStatus, HarnessTurnState

NAME = "local"
PREFIX = "native-local-effect-v1:"


def schema(*, allow_mutations: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {"op": {"type": "string", "enum": ["inspect"]}}
    if allow_mutations:
        properties["op"]["enum"].append("set_excitability")
        properties["value"] = {"type": "number", "minimum": 0, "maximum": 1}
    return {"type": "object", "properties": properties,
            "required": ["op"], "additionalProperties": False}


def descriptor(*, allow_mutations: bool) -> ToolDescriptor:
    description = (
        "Inspect this local context's actual activity, incoming/outgoing connections, "
        "inhibition and shared pulse cap. Its own original history remains available "
        "through the history tools. No other context or subject can be selected, "
        "and Work contexts cannot use it."
    )
    if allow_mutations:
        description += (
            " In the local Home context, set_excitability(value) "
            "adjusts only its existing spontaneous-activation tendency, from 0 to 1. "
            "Zero becomes quiet for future spontaneous checks; incoming user or propagated "
            "material can still activate the context. This does not archive a context, "
            "cancel admitted inputs, change connection weights, or grant more resources. "
            "The ordinary Engine, inhibition, rhythm and shared budget still govern activity. "
            "A successful adjustment is an immediate durable numeric effect: a later turn "
            "cancellation does not roll it back. Make a change only when warranted by the "
            "current experience; no adjustment or particular value is required."
        )
    return ToolDescriptor(NAME, description,
        capability="local.internal", source="pulse.native-local")


def validate(arguments: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise ValueError("local arguments must be an object")
    values = dict(arguments)
    if values == {"op": "inspect"}:
        return values
    if set(values) != {"op", "value"} or values["op"] != "set_excitability":
        raise ValueError("unknown local operation or fields")
    number = values["value"]
    try:
        valid = type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 1
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("local excitability must be a finite number in [0, 1]")
    return values


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class NativeLocal:
    def __init__(self, runtime):
        self.runtime = runtime

    def execute(self, scope, call, cancellation, *, action_request_id=None):
        r = self.runtime
        values = validate(call.arguments)
        r.harness.authorize_local(scope, action_request_id, call)
        cancellation.raise_if_cancelled()
        if values["op"] == "inspect":
            from .local_contexts import local_state
            return local_state(r, scope.engram_id)
        if not r.harness._allow_mutations:
            raise ScopeConflict("this runtime profile does not allow local adjustments")
        with r.harness._mutation_guard():
            r.harness.authorize_local(scope, action_request_id, call)
            cancellation.raise_if_cancelled()
            source = r.causal_ledger.get_turn_for_world(scope.turn_id, r.world_id)
            item = r.storage.get_engram(scope.engram_id)
            if (source is None or source.engram_id != scope.engram_id
                    or source.state is not HarnessTurnState.RUNNING
                    or r._native_kernel_context_for_turn(scope.engram_id, source.id) != scope.context_id or item is None
                    or item.status is not EngramStatus.ACTIVE):
                raise ScopeConflict("local adjustment requires its live local source turn")
            identity = {"source_scope": scope.as_dict(), "source_action_id": action_request_id}
            record = {
                "version": 1, "request_id": PREFIX + _digest(identity),
                "world_id": r.world_id, "engram_id": scope.engram_id,
                **identity, "arguments": values, "arguments_digest": _digest(values),
                "before": item.metadata.self_excitability, "after": float(values["value"]),
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "effect": "immediate_numeric_change_not_rolled_back_by_later_cancel",
            }
            fence = r._current_runtime_fence()
            return r.storage.apply_local_excitability(
                record, runtime_owner_id=fence.owner_id, runtime_lease_epoch=fence.epoch,
                now=datetime.now(timezone.utc))
