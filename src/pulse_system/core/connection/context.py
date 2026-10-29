"""Durable connections between local contexts of the same continuing subject.

An ActivityCenter identifies a local context; ``None`` identifies Home. These
edges carry neither a transcript nor a second execution queue. The native
Runtime projects settled results through the causal ledger and remains the
only authority for admission, membership, budgets, and execution.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any
from uuid import uuid4

from pulse_system.core.types import ConnectionType
from pulse_system.substrate.storage.store import Storage


_PREFIX = "pulse.context-connections.v1:"
_WORLD_COMPONENT = "pulse.world.v1"
_EPSILON = 1e-4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("context connection timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _number(value: Any, name: str, *, positive: bool = False, unit: bool = False) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if (not valid or value < 0 or (positive and value == 0)
            or (unit and value > 1)):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'non-negative'} number"
                         + (" in [0, 1]" if unit else ""))
    return float(value)


def _center(value: Any) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("context must be an ActivityCenter ID or None for Home")
    return value


def _key(center_id: str | None) -> str:
    # A prefix avoids collisions between Home and a real center named "home".
    return "home" if center_id is None else "center:" + center_id


@dataclass(frozen=True, slots=True)
class ContextConnection:
    id: str
    source_center_id: str | None
    target_center_id: str | None
    weight: float
    kind: str
    created_after_seq: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("context connection ID must be non-empty")
        _center(self.source_center_id)
        _center(self.target_center_id)
        if self.source_center_id == self.target_center_id:
            raise ValueError("a context connection must join distinct local contexts")
        _number(self.weight, "connection weight", unit=True)
        if self.kind not in {item.value for item in ConnectionType}:
            raise ValueError("connection kind must be excitatory or inhibitory")
        if type(self.created_after_seq) is not int or self.created_after_seq < 0:
            raise ValueError("created_after_seq must be a non-negative integer")
        created = _time(datetime.fromisoformat(self.created_at))
        updated = _time(datetime.fromisoformat(self.updated_at))
        if updated < created:
            raise ValueError("connection updated_at must not precede created_at")


class ContextConnectionNetwork:
    """A graph and sparse numeric traces in the existing World component store.

    Each operation reloads under Storage's lock before writing, so multiple
    facades in the same Runtime cannot lose each other's updates. The bound
    Runtime publication permit fences every save. Observation never writes.

    Inhibitory results are consumed in causal sequence order. Their per-target
    high-water marks make a coordinator restart/replay idempotent without an
    ever-growing set of result IDs. No model calls occur in this component.
    """

    def __init__(self, storage: Storage, world_id: str, *, inhibition_tau: float = 60.0):
        if not isinstance(world_id, str) or not world_id.strip():
            raise ValueError("world_id must be non-empty")
        self.storage = storage
        self.world_id = world_id
        self.inhibition_tau = _number(inhibition_tau, "inhibition tau", positive=True)
        self.component_key = _PREFIX + world_id
        with storage._lock:
            self._load()

    def _load(self) -> dict[str, Any]:
        identity = self.storage.load_component_state(_WORLD_COMPONENT)
        if not isinstance(identity, dict) or identity.get("world_id") != self.world_id:
            raise ValueError("context network must belong to this Storage's PulseWorld")
        state = self.storage.load_component_state(self.component_key)
        if state is None:
            return {"version": 1, "world_id": self.world_id,
                    "connections": [], "inhibition": {}}
        if (not isinstance(state, dict)
                or set(state) != {"version", "world_id", "connections", "inhibition"}
                or type(state["version"]) is not int or state["version"] != 1
                or state["world_id"] != self.world_id
                or not isinstance(state["connections"], list)
                or not isinstance(state["inhibition"], dict)):
            raise ValueError("invalid context connection state")
        pairs, ids = set(), set()
        for item in state["connections"]:
            if not isinstance(item, dict):
                raise ValueError("invalid context connection entry")
            try:
                edge = ContextConnection(**item)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid context connection entry") from exc
            pair = (edge.source_center_id, edge.target_center_id)
            if pair in pairs or edge.id in ids:
                raise ValueError("duplicate context connection identity")
            pairs.add(pair)
            ids.add(edge.id)
        for key, trace in state["inhibition"].items():
            if (not isinstance(trace, dict)
                    or set(trace) != {"center_id", "level", "updated_at", "tau", "source_seq"}
                    or key != _key(_center(trace["center_id"]))
                    or type(trace["source_seq"]) is not int or trace["source_seq"] < 0):
                raise ValueError("invalid context inhibition entry")
            _number(trace["level"], "inhibition level")
            _number(trace["tau"], "inhibition tau", positive=True)
            _time(datetime.fromisoformat(trace["updated_at"]))
        return state

    def _validate_endpoint(self, center_id: str | None) -> None:
        _center(center_id)
        if center_id is not None:
            center = self.storage.get_activity_center(center_id)
            if center is None:
                raise ValueError(f"ActivityCenter {center_id!r} does not exist in this World")
            identity = self.storage.load_component_state(_WORLD_COMPONENT)
            subject_id = identity.get("continuity_engram_id") if identity else None
            if subject_id is not None and center.focal_engram_id != subject_id:
                raise ValueError("context connections must stay within the continuing subject")

    def connect(self, source_center_id: str | None, target_center_id: str | None,
                weight: float = 0.6, kind: str = "excitatory") -> ContextConnection:
        weight = _number(weight, "connection weight", unit=True)
        kind = kind.value if isinstance(kind, ConnectionType) else kind
        if not isinstance(kind, str) or kind not in {item.value for item in ConnectionType}:
            raise ValueError("connection kind must be excitatory or inhibitory")
        if source_center_id == target_center_id:
            raise ValueError("a context connection must join distinct local contexts")
        with self.storage._lock:
            state = self._load()
            self._validate_endpoint(source_center_id)
            self._validate_endpoint(target_center_id)
            prior = next((item for item in state["connections"]
                          if (item["source_center_id"], item["target_center_id"])
                          == (source_center_id, target_center_id)), None)
            if prior is not None and prior["weight"] == weight and prior["kind"] == kind:
                return ContextConnection(**prior)
            now = _now()
            cutoff = self.storage._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM causal_events WHERE world_id = ?",
                (self.world_id,),
            ).fetchone()[0]
            edge = ContextConnection(
                # A weight/type edit is a new causal edge revision. Old
                # deliveries retain their original identity and priority;
                # retrying a lagging result cursor cannot reinterpret them.
                id="context-connection_" + uuid4().hex,
                source_center_id=source_center_id, target_center_id=target_center_id,
                weight=weight, kind=kind,
                created_after_seq=cutoff,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
            state["connections"] = [item for item in state["connections"]
                                    if (item["source_center_id"], item["target_center_id"])
                                    != (source_center_id, target_center_id)] + [asdict(edge)]
            self.storage.save_component_state(self.component_key, state)
            return edge

    def disconnect(self, source_center_id: str | None, target_center_id: str | None) -> bool:
        _center(source_center_id)
        _center(target_center_id)
        with self.storage._lock:
            state = self._load()
            before = len(state["connections"])
            state["connections"] = [item for item in state["connections"]
                                    if (item["source_center_id"], item["target_center_id"])
                                    != (source_center_id, target_center_id)]
            if len(state["connections"]) == before:
                return False
            self.storage.save_component_state(self.component_key, state)
            return True

    def outgoing(self, source_center_id: str | None) -> tuple[ContextConnection, ...]:
        _center(source_center_id)
        with self.storage._lock:
            state = self._load()
            return tuple(ContextConnection(**item) for item in state["connections"]
                         if item["source_center_id"] == source_center_id)

    @staticmethod
    def _level(trace: dict | None, now: datetime) -> float:
        if trace is None:
            return 0.0
        span = max(0.0, (now - _time(datetime.fromisoformat(trace["updated_at"]))).total_seconds())
        value = trace["level"] * math.exp(-span / trace["tau"])
        return value if value >= _EPSILON else 0.0

    def inhibition_level(self, target_center_id: str | None, *, now: datetime | None = None) -> float:
        _center(target_center_id)
        now = _time(now or _now())
        with self.storage._lock:
            return self._level(self._load()["inhibition"].get(_key(target_center_id)), now)

    def apply_inhibition(self, target_center_id: str | None, amount: float, *,
                         now: datetime | None = None, source_event_id: str | None = None) -> float:
        amount = _number(amount, "inhibitory impulse")
        now = _time(now or _now())
        with self.storage._lock:
            state = self._load()
            self._validate_endpoint(target_center_id)
            trace = state["inhibition"].get(_key(target_center_id))
            level = self._level(trace, now)
            source_seq = trace["source_seq"] if trace else 0
            if source_event_id is not None:
                if not isinstance(source_event_id, str) or not source_event_id.strip():
                    raise ValueError("inhibition source_event_id must be non-empty")
                source = self.storage._conn.execute(
                    "SELECT seq, world_id, kind, status FROM causal_events WHERE id = ?",
                    (source_event_id,),
                ).fetchone()
                if (source is None or source[1] != self.world_id
                        or source[2] != "assistant_result" or source[3] != "settled"):
                    raise ValueError("inhibition requires a settled result from this World")
                if source[0] <= source_seq:
                    return level
                source_seq = source[0]
            if trace is not None:
                now = max(now, _time(datetime.fromisoformat(trace["updated_at"])))
            level = _number(level + amount, "inhibition level")
            state["inhibition"][_key(target_center_id)] = {
                "center_id": target_center_id, "level": level,
                "updated_at": now.isoformat(), "tau": self.inhibition_tau,
                "source_seq": source_seq,
            }
            self.storage.save_component_state(self.component_key, state)
            return level

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = _time(now or _now())
        with self.storage._lock:
            state = self._load()
            return {
                "version": 1, "world_id": self.world_id,
                "connections": state["connections"],
                "inhibition": [
                    {**trace, "level": self._level(trace, now)}
                    for trace in state["inhibition"].values()
                ],
            }
