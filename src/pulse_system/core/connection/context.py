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
import json
import math
from typing import Any
from uuid import uuid4

from pulse_system.core.types import ConnectionType
from pulse_system.core.connection.network import ConnectionConfig
from pulse_system.core.learning_policy import OnlineLearningAudit, OnlineLearningChannel, OnlineLearningPolicy
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
    origin: str = "configured"
    factory_weight: float | None = None
    learned_weight: float | None = None
    learning_source_ids: list[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("context connection ID must be non-empty")
        _center(self.source_center_id)
        _center(self.target_center_id)
        if self.source_center_id == self.target_center_id:
            raise ValueError("a context connection must join distinct local contexts")
        _number(self.weight, "connection weight", unit=True)
        if self.origin not in {"configured", "learned"}:
            raise ValueError("invalid context connection origin")
        for value in (self.factory_weight, self.learned_weight):
            if value is not None:
                _number(value, "connection weight layer", unit=True)
        if self.learned_weight is not None and self.weight != self.learned_weight:
            raise ValueError("effective weight must match its learned layer")
        if self.learning_source_ids is not None and (
                len(self.learning_source_ids) != 2 or any(
                    not isinstance(value, str) or not value for value in self.learning_source_ids)):
            raise ValueError("invalid context learning source receipts")
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
    Runtime publication permit fences every save. Snapshots are read-only.

    Inhibitory results are consumed in causal sequence order. Their per-target
    high-water marks make a coordinator restart/replay idempotent without an
    ever-growing set of result IDs. No model calls occur in this component.
    """

    def __init__(self, storage: Storage, world_id: str, *, inhibition_tau: float = 60.0,
                 learning_config: ConnectionConfig | None = None,
                 learning_policy: OnlineLearningPolicy | None = None,
                 learning_audit: OnlineLearningAudit | None = None):
        if not isinstance(world_id, str) or not world_id.strip():
            raise ValueError("world_id must be non-empty")
        self.storage = storage
        self.world_id = world_id
        self.inhibition_tau = _number(inhibition_tau, "inhibition tau", positive=True)
        self.component_key = _PREFIX + world_id
        self.learning_config = learning_config or ConnectionConfig()
        self.learning_policy = learning_policy or OnlineLearningPolicy()
        self.learning_audit = learning_audit or OnlineLearningAudit()
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
                or set(state) - {"learning"} != {"version", "world_id", "connections", "inhibition"}
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
        if "learning" in state:
            learning = state["learning"]
            if (not isinstance(learning, dict)
                    or set(learning) != {"start_seq", "cursor", "recent", "blocked", "last_decay_at", "pairs", "pruned"}
                    or any(type(learning.get(name)) is not int or learning[name] < 0
                           for name in ("cursor", "pairs", "pruned"))
                    or not isinstance(learning["recent"], list) or len(learning["recent"]) > 256
                    or not isinstance(learning["blocked"], list)):
                raise ValueError("invalid context connection learning state")
            start = learning["start_seq"]
            if start is not None and (type(start) is not int or not 0 <= start <= learning["cursor"]):
                raise ValueError("invalid context learning start receipt")
            for item in learning["recent"]:
                if (not isinstance(item, dict) or set(item) != {"center_id", "event_id", "at"}
                        or not isinstance(item["event_id"], str) or not item["event_id"]):
                    raise ValueError("invalid context eligibility trace")
                _center(item["center_id"])
                _time(datetime.fromisoformat(item["at"]))
            for pair in learning["blocked"]:
                if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
                    raise ValueError("invalid blocked context connection")
                _center(pair[0])
                _center(pair[1])
            if learning["last_decay_at"] is not None:
                _time(datetime.fromisoformat(learning["last_decay_at"]))
        return state

    @staticmethod
    def _learning(state: dict) -> dict:
        return state.setdefault("learning", {
            "start_seq": None, "cursor": 0, "recent": [], "blocked": [], "last_decay_at": None,
            "pairs": 0, "pruned": 0,
        })

    def start_learning(self) -> None:
        """Attach once, without training retrospectively on an imported history.

        Persist the starting sequence before any new turns arrive. A restart
        before their observation must still retain those new opportunities.
        """
        with self.storage._lock:
            state = self._load()
            learning = self._learning(state)
            if learning["start_seq"] is not None:
                return
            cutoff = self.storage._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM causal_events WHERE world_id = ?",
                (self.world_id,),
            ).fetchone()[0]
            learning.update(start_seq=cutoff, cursor=cutoff, recent=[])
            self.storage.save_component_state(self.component_key, state)

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
            learning = self._learning(state)
            learning["blocked"] = [pair for pair in learning["blocked"]
                                   if pair != [source_center_id, target_center_id]]
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
                factory_weight=weight,
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
            learning = self._learning(state)
            pair = [source_center_id, target_center_id]
            if pair not in learning["blocked"]:
                learning["blocked"].append(pair)
            before = len(state["connections"])
            state["connections"] = [item for item in state["connections"]
                                    if (item["source_center_id"], item["target_center_id"])
                                    != (source_center_id, target_center_id)]
            self.storage.save_component_state(self.component_key, state)
            return len(state["connections"]) != before

    def observe_activities(self, results, *, eligible, enabled: bool = True) -> None:
        """Learn Work-to-Work timing from accepted ordinary results.

        The controller supplies its canonical source/ownership predicate. Graph
        deltas, eligibility traces and the result cursor commit together, so a
        crash before the controller checkpoint cannot learn a pair twice.
        No transcript, task text or fabricated Engram is used as a feature.
        """
        with self.storage._lock:
            state = self._load()
            learning = self._learning(state)
            results = [result for result in results if result.seq > learning["cursor"]]
            if not results:
                return
            config = self.learning_config
            edges = {(item["source_center_id"], item["target_center_id"]): item
                     for item in state["connections"]}
            blocked = {tuple(pair) for pair in learning["blocked"]}
            cutoff = self.storage._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM causal_events WHERE world_id = ?",
                (self.world_id,),
            ).fetchone()[0]
            eligibility = {}

            def allowed(center):
                if center not in eligibility:
                    eligibility[center] = eligible(center)
                return eligibility[center]

            for result in results:
                at = _time(result.settled_at or result.created_at)
                recent = [item for item in learning["recent"]
                          if abs((at - datetime.fromisoformat(item["at"])).total_seconds())
                          <= config.coactivation_window and allowed(item["center_id"])]
                can_learn = enabled and allowed(result.center_id)
                for prior in recent if can_learn else ():
                    if prior["center_id"] == result.center_id:
                        continue
                    channel = OnlineLearningChannel.CONNECTION_STDP
                    self.learning_audit.record_attempt(channel)
                    if not self.learning_policy.allows(channel):
                        continue
                    earlier, later = sorted((prior, {"center_id": result.center_id,
                        "event_id": result.id, "at": at.isoformat()}), key=lambda item: item["at"])
                    gap = abs((at - datetime.fromisoformat(prior["at"])).total_seconds())
                    decay = math.exp(-gap / config.stdp_tau)
                    sources = [earlier["event_id"], later["event_id"]]
                    changed = self._learn_edge(state, edges, blocked, cutoff,
                        earlier["center_id"], later["center_id"], config.stdp_strength * decay,
                        sources, at, create=True)
                    changed += self._learn_edge(state, edges, blocked, cutoff,
                        later["center_id"], earlier["center_id"], -config.ltd_strength * decay,
                        sources, at, create=False)
                    learning["pairs"] += 1
                    self.learning_audit.record_applied(channel, changed)
                if can_learn:
                    learning["recent"] = ([item for item in recent if item["center_id"] != result.center_id]
                                          + [{"center_id": result.center_id, "event_id": result.id,
                                              "at": at.isoformat()}])[-256:]
                else:
                    # An excluded context cannot erase another context's live
                    # trace. Disabling learning still clears the whole window.
                    learning["recent"] = recent if enabled else []
                learning["cursor"] = result.seq
            self.storage.save_component_state(self.component_key, state)

    def _learn_edge(self, state, edges, blocked, cutoff, source, target, delta, receipts, at, *, create) -> int:
        if delta == 0 or (source, target) in blocked:
            return 0
        prior = edges.get((source, target))
        # Timing alone never invents an inhibitory relation or changes its sign.
        if prior is not None and prior["kind"] != "excitatory":
            return 0
        if prior is None:
            if not create:
                return 0
            stamp = _now().isoformat()
            prior = asdict(ContextConnection(
                id="context-connection_" + uuid4().hex, source_center_id=source,
                target_center_id=target, weight=0.0, kind="excitatory",
                created_after_seq=cutoff, created_at=stamp, updated_at=stamp,
                origin="learned", factory_weight=0.0,
            ))
            state["connections"].append(prior)
            edges[source, target] = prior
        weight = min(self.learning_config.weight_cap, max(0.0, prior["weight"] + delta))
        if weight == prior["weight"]:
            return 0
        prior.setdefault("factory_weight", prior["weight"])
        prior["weight"] = prior["learned_weight"] = weight
        prior["learning_source_ids"] = receipts
        prior["updated_at"] = max(datetime.fromisoformat(prior["updated_at"]), at).isoformat()
        return 1

    def decay_and_prune(self, *, now: datetime | None = None, enabled: bool = True) -> list[str]:
        """Time-based field decay; explicit edges retain their factory baseline.

        One minute is the decay unit, independent of polling frequency. Only
        learned edges are pruned. Operator removals remain persistent exclusions.
        """
        now = _time(now or _now())
        with self.storage._lock:
            state = self._load()
            learning = self._learning(state)
            previous = learning["last_decay_at"]
            elapsed = (now - datetime.fromisoformat(previous)).total_seconds() if previous else 0.0
            if previous is not None and 0 <= elapsed < 60:
                return []
            channel = OnlineLearningChannel.CONNECTION_DECAY_PRUNE
            if enabled:
                self.learning_audit.record_attempt(channel)
            removed = []
            if enabled and elapsed > 0 and self.learning_policy.allows(channel):
                # Numerical forgetting affects future influence. Inputs that
                # already arrived (including a paused/fused input) retain their
                # original edge until consumed; only an operator edit revokes
                # such deliveries through the existing cancellation path.
                retained = set()
                for (raw,) in self.storage._conn.execute(
                    "SELECT metadata FROM causal_events WHERE world_id = ? "
                    "AND status IN ('queued', 'running') AND "
                    "(json_extract(metadata, '$.context_connection_id') IS NOT NULL OR "
                    "json_extract(metadata, '$.context_connection_ids') IS NOT NULL)",
                    (self.world_id,),
                ):
                    metadata = json.loads(raw)
                    retained.update(metadata.get("context_connection_ids", []))
                    if metadata.get("context_connection_id"):
                        retained.add(metadata["context_connection_id"])
                multiplier = math.exp(-self.learning_config.decay_rate * elapsed / 60)
                for edge in state["connections"]:
                    if edge.get("learned_weight") is None:
                        continue
                    edge["weight"] = edge["learned_weight"] = edge["learned_weight"] * multiplier
                    edge["updated_at"] = max(now, datetime.fromisoformat(edge["updated_at"])).isoformat()
                    if (edge.get("origin") == "learned" and edge["weight"] < self.learning_config.prune_threshold
                            and edge["id"] not in retained):
                        removed.append(edge["id"])
                state["connections"] = [edge for edge in state["connections"] if edge["id"] not in removed]
                learning["pruned"] += len(removed)
                self.learning_audit.record_applied(channel, len(state["connections"]) + len(removed))
            learning["last_decay_at"] = now.isoformat()
            self.storage.save_component_state(self.component_key, state)
            return removed

    def outgoing(self, source_center_id: str | None) -> tuple[ContextConnection, ...]:
        _center(source_center_id)
        with self.storage._lock:
            if not self._read_header():
                return ()
            # Admission often asks about one source while a worker is busy.
            # Project that adjacency in SQLite instead of deserializing and
            # validating the entire learned graph for each queued candidate.
            rows = self.storage._conn.execute(
                "SELECT edge.value FROM component_state AS body, "
                "json_each(body.state, '$.connections') AS edge "
                "WHERE body.component = ? AND json_extract(edge.value, '$.source_center_id') IS ?",
                (self.component_key, source_center_id),
            ).fetchall()
            return tuple(ContextConnection(**json.loads(row[0])) for row in rows)

    def _read_header(self) -> bool:
        identity = self.storage.load_component_state(_WORLD_COMPONENT)
        if not isinstance(identity, dict) or identity.get("world_id") != self.world_id:
            raise ValueError("context network must belong to this Storage's PulseWorld")
        row = self.storage._conn.execute(
            "SELECT json_extract(state, '$.version'), json_extract(state, '$.world_id'), "
            "json_type(state, '$.connections'), json_type(state, '$.inhibition') "
            "FROM component_state WHERE component = ?", (self.component_key,),
        ).fetchone()
        if row is None:
            return False
        if tuple(row) != (1, self.world_id, "array", "object"):
            raise ValueError("invalid context connection state")
        return True

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
            if not self._read_header():
                return 0.0
            raw = self.storage._conn.execute(
                "SELECT json_extract(state, '$.inhibition') FROM component_state WHERE component = ?",
                (self.component_key,),
            ).fetchone()[0]
            trace = json.loads(raw).get(_key(target_center_id))
            if trace is not None:
                if trace.get("center_id") != target_center_id:
                    raise ValueError("invalid context inhibition target")
                _number(trace["level"], "inhibition level")
                _number(trace["tau"], "inhibition tau", positive=True)
            return self._level(trace, now)

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
                "learning": {**{key: value for key, value in self._learning(state).items()
                                if key != "recent"},
                             "eligibleTraces": len(self._learning(state)["recent"]),
                             "audit": self.learning_audit.snapshot(self.learning_policy)},
            }
