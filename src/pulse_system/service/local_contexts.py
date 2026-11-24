"""Ordinary product access to the existing local Engram network.

Creation supplies display identity only. Content still enters through Runtime's
durable inject path; the original Dendrite and PulseEngine choose and execute
the subsequent cognition. No Work, role prompt or parallel scheduler is made.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from pulse_system.core.types import ConnectionType, EngramStatus, RuntimeLeaseError
from pulse_system.service.runtime import RuntimeService, ServiceError

CONNECTION_VIEW_LIMIT = 100


def _fields(body: dict, allowed: set[str]) -> None:
    if not isinstance(body, dict) or set(body) - allowed:
        raise ServiceError(
            "unknown_local_context_field", "the request contains unsupported fields",
            f"send only {sorted(allowed)}", status=400,
        )


def _engram(runtime: RuntimeService, engram_id: str, *, active: bool = False):
    item = runtime.storage.get_engram(engram_id)
    if item is None:
        raise ServiceError(
            "unknown_engram", "the local context does not exist",
            "refresh GET /engrams and choose an existing context", status=404,
        )
    if active and item.status is not EngramStatus.ACTIVE:
        raise ServiceError(
            "archived_engram", "this local context no longer pulses",
            "choose its active successor or another active context", status=409,
        )
    return item


def _identity(item, message_count: int) -> dict:
    return {
        "id": item.id, "name": item.name, "name_origin": item.name_origin,
        "nickname": item.nickname, "project_id": item.project_id,
        "status": item.status.value, "created_at": item.created_at.isoformat(),
        "last_pulse_at": item.last_pulse_at.isoformat() if item.last_pulse_at else None,
        "total_pulses": item.total_pulses, "message_count": message_count,
    }


def _connection(item) -> dict:
    return {
        "from_id": item.from_id, "to_id": item.to_id,
        "weight": item.weight, "conn_type": item.conn_type.value,
        "factory_weight": item.factory_weight, "field_weight": item.learned_weight,
        "updated_at": item.last_activated_at.isoformat(),
    }


def create_local_context(runtime: RuntimeService, body: dict) -> dict:
    _fields(body, {"name"})
    name = body.get("name")
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise ServiceError(
                "invalid_name", "name must be nonempty text or omitted",
                "omit name for an unnamed local context", status=400,
            )
        name = " ".join(name.split())
        if len(name) > 80:
            raise ServiceError(
                "name_too_long", "the display name exceeds 80 characters",
                "shorten the name to 80 characters or fewer", status=400,
            )
    with runtime._close_lock:
        fence = runtime._current_runtime_fence()
        try:
            item = runtime.engrams.create(
                auto_name=False, name=name, runtime_fence=fence,
            )
        except RuntimeLeaseError as exc:
            raise runtime._runtime_lease_service_error(exc) from exc
        runtime.metrics.record("local_context_created", engram=item.id, world=runtime.world_id)
        return {"world_id": runtime.world_id, "engram": _identity(item, 0)}


def local_state(runtime: RuntimeService, engram_id: str) -> dict:
    """Observe actual live numeric state and retained edges, without an LLM."""
    with runtime._close_lock:
        if runtime._closed:
            raise ServiceError(
                "runtime_unavailable", "this runtime has closed",
                "reconnect to the runtime that has opened the saved world", status=503,
            )
        item = _engram(runtime, engram_id)
        at = datetime.now(timezone.utc)
        outgoing = sorted(runtime.storage.get_outgoing(engram_id), key=lambda edge: edge.to_id)
        incoming = sorted(runtime.storage.get_incoming(engram_id), key=lambda edge: edge.from_id)
        return {
            "world_id": runtime.world_id, "observed_at": at.isoformat(),
            "engram": _identity(item, runtime.storage.get_message_count(engram_id)),
            "recent_activity": item.metadata.recent_activity,
            "self_excitability": item.metadata.self_excitability,
            "self_adjustments": runtime.storage.local_excitability_effects(engram_id),
            "inhibition": runtime.engine.inhibition_level(engram_id, now=at),
            "budget_per_tick": min(runtime.engine.config.budget_per_tick,
                                   runtime.resources.config.budget_per_tick),
            "connections": {
                "outgoing": [_connection(edge) for edge in outgoing[:CONNECTION_VIEW_LIMIT]],
                "incoming": [_connection(edge) for edge in incoming[:CONNECTION_VIEW_LIMIT]],
                "outgoing_total": len(outgoing), "incoming_total": len(incoming),
                "limit": CONNECTION_VIEW_LIMIT,
            },
        }


def configure_connection(
    runtime: RuntimeService, from_id: str, to_id: str, body: dict,
) -> dict:
    _fields(body, {"weight", "conn_type"})
    weight = body.get("weight")
    try:
        valid_weight = type(weight) in (int, float) and math.isfinite(weight) and 0 <= weight <= 1
    except OverflowError:
        valid_weight = False
    if not valid_weight:
        raise ServiceError(
            "invalid_weight", "weight must be a finite number in [0, 1]",
            "send a numeric weight between zero and one", status=400,
        )
    try:
        kind = ConnectionType(body.get("conn_type"))
    except (TypeError, ValueError):
        raise ServiceError(
            "invalid_connection_type", "conn_type must be excitatory or inhibitory",
            "send the intended numeric connection type", status=400,
        ) from None
    with runtime._close_lock:
        fence = runtime._current_runtime_fence()
        _engram(runtime, from_id, active=True)
        _engram(runtime, to_id, active=True)
        try:
            connection = runtime.connections.configure_field_connection(
                from_id, to_id, float(weight), kind,
                runtime_owner_id=fence.owner_id, runtime_lease_epoch=fence.epoch,
            )
        except RuntimeLeaseError as exc:
            raise runtime._runtime_lease_service_error(exc) from exc
        except ValueError as exc:
            # The transaction rechecks endpoints and type after concurrent
            # engine activity; a stale selection cannot silently change type.
            raise ServiceError(
                "connection_conflict", str(exc),
                "refresh local state; an existing pair keeps its connection type",
                status=409,
            ) from exc
        runtime.metrics.record(
            "local_connection_configured", world=runtime.world_id,
            source=from_id, target=to_id, conn_type=kind.value, weight=float(weight),
            layer="field", origin="operator",
        )
        return {"world_id": runtime.world_id, "connection": _connection(connection)}


def configure_excitability(runtime: RuntimeService, engram_id: str, body: dict) -> dict:
    """Edit one local tendency, not a semantic task, global budget or stop."""
    _fields(body, {"self_excitability", "expected_self_excitability"})
    value, expected = body.get("self_excitability"), body.get("expected_self_excitability")
    for number, ceiling in ((value, 1.0), (expected, None)):
        try:
            valid = (type(number) in (int, float) and math.isfinite(number)
                     and number >= 0 and (ceiling is None or number <= ceiling))
        except OverflowError:
            valid = False
        if not valid:
            raise ServiceError(
                "invalid_excitability", "send a finite tendency in [0, 1] and the last observed value",
                "reload local state and retain expected_self_excitability", status=400,
            )
    with runtime._close_lock:
        fence = runtime._current_runtime_fence()
        _engram(runtime, engram_id, active=True)
        try:
            saved = runtime.storage.configure_local_excitability(
                engram_id, float(value), float(expected),
                runtime_owner_id=fence.owner_id, runtime_lease_epoch=fence.epoch,
            )
        except RuntimeLeaseError as exc:
            raise runtime._runtime_lease_service_error(exc) from exc
        except ValueError as exc:
            raise ServiceError("local_rhythm_conflict", str(exc),
                               "refresh the saved value; your draft can be kept", status=409) from exc
        runtime.metrics.record("local_excitability_configured", world=runtime.world_id,
                               engram=engram_id, previous=expected, value=saved, origin="operator")
        return {"world_id": runtime.world_id, "engram_id": engram_id,
                "self_excitability": saved}
