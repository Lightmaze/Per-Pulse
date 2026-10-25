"""Local context dynamics inside one continuing subject's native World.

This component is driven by RuntimeService's existing tick. It has no model
loop, conversation store, or synthetic prompt. Settled Kernel results are the
only content that can cross a context connection; quiet reentry carries no
new text. The causal ledger owns every admitted unit of background work.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import json
import random
from typing import Callable

from pulse_system.core.connection.context import ContextConnectionNetwork
from pulse_system.core.causality import may_emit_content_propagation
from pulse_system.core.types import (
    ActivityCenterStatus, CausalEventDomain, CausalEventKind,
    CausalEventSource, CausalEventStatus, EngramStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ContextPulseConfig:
    enabled: bool = False
    check_interval_seconds: float = 10.0
    spontaneous_rate: float = 0.1
    refractory_seconds: float = 60.0
    max_background_pulses_per_hour: int = 12
    max_propagation_depth: int = 2
    propagation_threshold: float = 0.3
    activity_halflife_seconds: float = 600.0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        for name in ("check_interval_seconds", "refractory_seconds",
                     "activity_halflife_seconds", "spontaneous_rate", "propagation_threshold"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite nonnegative number")
        if self.check_interval_seconds == 0 or self.activity_halflife_seconds == 0:
            raise ValueError("check interval and activity half-life must be positive")
        if self.spontaneous_rate > 1 or self.propagation_threshold > 1:
            raise ValueError("probability and threshold must be at most 1")
        for name in ("max_background_pulses_per_hour", "max_propagation_depth"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a nonnegative integer")


class _QuotaReached(Exception):
    pass


class ContextPulseController:
    """Durable result projection, local reentry and bounded result diffusion.

    Progress is checkpointed only after its effects are durable. A crash
    between an effect and its checkpoint retries stable ledger keys, without
    replaying a model turn or refilling the rolling one-hour admission quota.
    """

    def __init__(self, storage, ledger, world, *, world_id: str,
                 subject: Callable[[], str], fence: Callable,
                 config: ContextPulseConfig):
        self.storage, self.ledger, self.world = storage, ledger, world
        self.world_id, self.subject, self.fence, self.config = world_id, subject, fence, config
        self.connections = ContextConnectionNetwork(storage, world_id)
        self.key = f"pulse-context-dynamics-v1:{world_id}"
        with storage._lock:
            checkpoint = storage.load_component_state(self.key)
            self._state = self._validate_checkpoint(checkpoint) if checkpoint is not None else {
                "version": 1, "world_id": world_id, "cursor": 0,
                "observed_cursor": 0, "last_check_at": None, "contexts": {},
                "paused": False,
            }

    @staticmethod
    def _checkpoint_time(value) -> datetime:
        if not isinstance(value, str):
            raise ValueError("invalid context pulse checkpoint timestamp")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid context pulse checkpoint timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("invalid context pulse checkpoint timestamp")
        return parsed

    def _validate_checkpoint(self, checkpoint) -> dict:
        """Restore only ledger-backed progress, never invented activity.

        Results remain valid through committed subject succession and context
        archival. The immutable causal receipt, rather than current admission
        eligibility, proves their identity and original activity time.
        """
        required = {"version", "world_id", "cursor", "last_check_at", "contexts"}
        optional = {"observed_cursor", "paused"}
        if (not isinstance(checkpoint, dict)
                or set(checkpoint) - optional != required
                or type(checkpoint.get("paused", False)) is not bool
                or type(checkpoint.get("version")) is not int
                or checkpoint["version"] != 1
                or checkpoint["world_id"] != self.world_id
                or not isinstance(checkpoint["contexts"], dict)):
            raise ValueError("invalid context pulse checkpoint")
        state = deepcopy(checkpoint)
        # The first checkpoint revision used one cursor for both passes.
        state.setdefault("observed_cursor", state["cursor"])
        state.setdefault("paused", False)
        cursors = (state["cursor"], state["observed_cursor"])
        maximum = self.storage._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM causal_events WHERE world_id = ?",
            (self.world_id,),
        ).fetchone()[0]
        if (any(type(value) is not int or value < 0 or value > maximum for value in cursors)
                or cursors[0] > cursors[1]):
            raise ValueError("invalid context pulse checkpoint cursor")
        for cursor in set(cursors) - {0}:
            receipt = self.storage._conn.execute(
                "SELECT 1 FROM causal_events WHERE seq = ? AND world_id = ? "
                "AND kind = 'assistant_result' AND status = 'settled'",
                (cursor, self.world_id),
            ).fetchone()
            if receipt is None:
                raise ValueError("invalid context pulse checkpoint cursor receipt")
        if state["last_check_at"] is not None:
            self._checkpoint_time(state["last_check_at"])
        lineage = self._subject_lineage()
        for key, entry in state["contexts"].items():
            if (not isinstance(entry, dict)
                    or set(entry) != {"center_id", "result_event_id", "settled_at"}
                    or (entry["center_id"] is not None
                        and (not isinstance(entry["center_id"], str) or not entry["center_id"].strip()))
                    or key != self._key(entry["center_id"])
                    or not isinstance(entry["result_event_id"], str)
                    or not entry["result_event_id"]):
                raise ValueError("invalid context pulse checkpoint context")
            self._checkpoint_time(entry["settled_at"])
            result = self.ledger.get_event(entry["result_event_id"])
            if (result is None or result.world_id != self.world_id
                    or result.kind is not CausalEventKind.ASSISTANT_RESULT
                    or result.status is not CausalEventStatus.SETTLED
                    or result.center_id != entry["center_id"]
                    or result.engram_id not in lineage
                    or result.seq > state["observed_cursor"]
                    or entry["settled_at"] != (result.settled_at or result.created_at).isoformat()
                    or self._source_root(result) is None):
                raise ValueError("invalid context pulse checkpoint result receipt")
        return state

    @staticmethod
    def _key(center_id: str | None) -> str:
        return "center:" + center_id if center_id is not None else "home"

    def _save(self, now: datetime) -> None:
        if self.storage.load_component_state(self.key) == self._state:
            return
        fence = self.fence()
        self.storage.save_component_state_fenced(
            self.key, self._state, runtime_owner_id=fence.owner_id,
            runtime_lease_epoch=fence.epoch, now=now,
        )

    def set_paused(self, paused: bool) -> None:
        """Atomically hold native background admission without erasing influence.

        Foreground user turns and already-running Kernel calls are not
        cancelled. Pending observations and queued events keep their causal
        identities so the next permitted tick can continue from them.
        """
        if type(paused) is not bool:
            raise ValueError("paused must be a boolean")
        with self.storage._lock:
            previous = self._state["paused"]
            if previous == paused:
                return
            self._state["paused"] = paused
            try:
                self._save(_now())
            except BaseException:
                self._state["paused"] = previous
                raise

    def _current_context(self, center_id: str | None, *, retain_paused: bool = False) -> bool:
        """Check body membership separately from permission to execute.

        A paused local context still retains completed incoming influence.
        Only the observation/delivery pass opts into that state; spontaneous
        reentry and the engine's admission gate continue to require ACTIVE.
        """
        engram = self.storage.get_engram(self.subject())
        if engram is None or engram.status is not EngramStatus.ACTIVE:
            return False
        if center_id is None:
            return True
        center = self.world.get_activity_center(center_id)
        statuses = {ActivityCenterStatus.ACTIVE}
        if retain_paused:
            statuses.add(ActivityCenterStatus.PAUSED)
        if center is None or center.status not in statuses:
            return False
        if center.focal_engram_id != self.subject():
            return False
        return self.storage._conn.execute(
            "SELECT 1 FROM center_memberships WHERE center_id = ? AND engram_id = ?",
            (center_id, self.subject()),
        ).fetchone() is not None

    def _subject_lineage(self) -> set[str]:
        rows = self.storage._conn.execute(
            "WITH RECURSIVE lineage(id) AS (SELECT ? UNION "
            "SELECT g.predecessor_id FROM generation_transitions g JOIN lineage l "
            "ON g.successor_id = l.id WHERE g.state = 'committed') SELECT id FROM lineage",
            (self.subject(),),
        ).fetchall()
        return {row[0] for row in rows}

    def _used(self, now: datetime, conn=None) -> int:
        conn = conn or self.storage._conn
        return conn.execute(
            "SELECT COUNT(*) FROM causal_events WHERE world_id = ? "
            "AND kind IN ('spontaneous', 'propagation') "
            "AND (json_extract(metadata, '$.context_pulse') = 'reentry' "
            "OR json_extract(metadata, '$.context_connection_id') IS NOT NULL) "
            "AND julianday(created_at) >= julianday(?)",
            (self.world_id, (now - timedelta(hours=1)).isoformat()),
        ).fetchone()[0]

    def _guard(self, conn) -> None:
        if self._used(_now(), conn) >= self.config.max_background_pulses_per_hour:
            raise _QuotaReached()

    def _open(self, center_id: str | None) -> bool:
        return self.storage._conn.execute(
            "SELECT 1 FROM causal_events WHERE world_id = ? AND engram_id = ? "
            "AND center_id IS ? AND status IN ('queued', 'running') LIMIT 1",
            (self.world_id, self.subject(), center_id),
        ).fetchone() is not None

    def _ready(self, center_id: str | None, now: datetime, *, inhibition: float | None = None) -> bool:
        return self._context_deferred_reason(center_id, now, inhibition=inhibition) is None

    def _context_deferred_reason(self, center_id: str | None, now: datetime, *,
                                 inhibition: float | None = None) -> str | None:
        if not self._current_context(center_id):
            center = self.world.get_activity_center(center_id) if center_id is not None else None
            if (center is not None and center.status is ActivityCenterStatus.PAUSED
                    and self._current_context(center_id, retain_paused=True)):
                return "context_paused"
            return "context_inactive"
        entry = self._state["contexts"].get(self._key(center_id))
        settled_at = datetime.fromisoformat(entry["settled_at"]) if entry else None
        # Workers may settle after the observation pass and before this tick's
        # admission selection. Read the authoritative latest ordinary result
        # so that a stale projection cannot bypass the local refractory period.
        latest = self.storage._conn.execute(
            "SELECT result.settled_at FROM causal_events result "
            "JOIN causal_events root ON root.id = result.parent_event_id "
            "JOIN harness_turns turn ON turn.result_event_id = result.id "
            "WHERE result.world_id = ? AND result.center_id IS ? "
            "AND result.engram_id IN (WITH RECURSIVE lineage(id) AS (SELECT ? UNION "
            "SELECT g.predecessor_id FROM generation_transitions g JOIN lineage l "
            "ON g.successor_id = l.id WHERE g.state = 'committed') SELECT id FROM lineage) "
            "AND result.kind = 'assistant_result' AND result.status = 'settled' "
            "AND root.kind IN ('stimulus', 'spontaneous', 'propagation', 'pulse') "
            "AND root.status = 'settled' AND (root.flow IS NULL OR root.flow = 'content') "
            "AND turn.state = 'settled' AND turn.prompt_accepted = 1 "
            "AND COALESCE(json_extract(result.metadata, '$.execution_regime'), '') <> 'replay' "
            "AND COALESCE(json_extract(root.metadata, '$.execution_regime'), '') <> 'replay' "
            "AND json_extract(root.metadata, '$.generation_stage') IS NULL "
            "ORDER BY result.seq DESC LIMIT 1",
            (self.world_id, center_id, self.subject()),
        ).fetchone()
        if latest and latest[0]:
            authoritative_at = datetime.fromisoformat(latest[0])
            settled_at = max(settled_at, authoritative_at) if settled_at else authoritative_at
        if settled_at:
            elapsed = (now - settled_at).total_seconds()
            if elapsed < self.config.refractory_seconds:
                return "refractory"
        if inhibition is None:
            inhibition = self.connections.inhibition_level(center_id, now=now)
        return "inhibited" if inhibition >= 1.0 else None

    def _source_root(self, result):
        """Only ordinary accepted turns may seed future autonomous work."""
        if (result.engram_id is None or result.parent_event_id is None
                or result.metadata.get("execution_regime") == "replay"
                or result.domain is not CausalEventDomain.HARNESS
                or result.source is not CausalEventSource.SELF):
            return None
        root = self.ledger.get_event(result.parent_event_id)
        if (root is None or root.status is not CausalEventStatus.SETTLED
                or root.engram_id != result.engram_id or root.center_id != result.center_id
                or root.causal_id != result.causal_id or root.world_id != self.world_id
                or root.kind not in {CausalEventKind.STIMULUS, CausalEventKind.SPONTANEOUS, CausalEventKind.PROPAGATION, CausalEventKind.PULSE}
                or root.metadata.get("execution_regime") == "replay"
                or root.metadata.get("generation_stage")
                or not may_emit_content_propagation(root)):
            return None
        turns = self.storage._conn.execute(
            "SELECT event_id, engram_id, state, prompt_accepted FROM harness_turns WHERE result_event_id = ?",
            (result.id,),
        ).fetchall()
        return root if (len(turns) == 1 and tuple(turns[0]) == (
            root.id, result.engram_id, "settled", 1,
        )) else None

    def connect(self, source_center_id, target_center_id, weight=0.6, kind="excitatory"):
        with self.storage._lock:
            prior = next((edge for edge in self.connections.outgoing(source_center_id)
                          if edge.target_center_id == target_center_id), None)
            edge = self.connections.connect(source_center_id, target_center_id, weight, kind)
            if prior and prior.id != edge.id:
                self.ledger.cancel_context_connection(prior.id, world_id=self.world_id,
                                                      runtime_fence=self.fence())
            return edge

    def disconnect(self, source_center_id, target_center_id) -> bool:
        with self.storage._lock:
            prior = next((edge for edge in self.connections.outgoing(source_center_id)
                          if edge.target_center_id == target_center_id), None)
            removed = self.connections.disconnect(source_center_id, target_center_id)
            if prior:
                self.ledger.cancel_context_connection(prior.id, world_id=self.world_id,
                                                      runtime_fence=self.fence())
            return removed

    def _repair_revocations(self, graph: dict) -> None:
        """Finish a graph edit interrupted before its queued work was revoked."""
        current = {edge["id"] for edge in graph["connections"]
                   if edge["kind"] == "excitatory"}
        queued = self.storage._conn.execute(
            "SELECT metadata FROM causal_events WHERE world_id = ? AND status = 'queued' "
            "AND kind IN ('propagation', 'pulse') AND (json_extract(metadata, '$.context_connection_id') IS NOT NULL "
            "OR json_extract(metadata, '$.context_connection_ids') IS NOT NULL)",
            (self.world_id,),
        ).fetchall()
        ids = set()
        for (raw,) in queued:
            meta = json.loads(raw)
            ids.update(meta.get("context_connection_ids", []))
            if meta.get("context_connection_id"):
                ids.add(meta["context_connection_id"])
        for edge_id in ids:
            if edge_id not in current:
                self.ledger.cancel_context_connection(edge_id, world_id=self.world_id,
                                                      runtime_fence=self.fence())

    def admits(self, event, observed_at: datetime) -> bool:
        """Pure engine filter; never spends budget during candidate scans."""
        return self.deferred_reason(event, observed_at) is None

    def deferred_reason(self, event, observed_at: datetime) -> str | None:
        """Explain the same context gate used by admission, without mutating it.

        A clear gate is not a scheduling promise: Dendrite readiness, worker
        capacity, execution-context exclusion and claim checks still apply.
        """
        if event.event_id is None:
            return None
        fact = self.ledger.get_event(event.event_id)
        if fact is None or not (fact.metadata.get("context_pulse") in {"reentry", "convergence"}
                                or fact.metadata.get("context_connection_id")):
            return None
        if not self.config.enabled:
            return "profile_paused"
        with self.storage._lock:
            if self._state["paused"]:
                return "operator_paused"
            if event.depth > self.config.max_propagation_depth:
                return "depth_limit"
            fused_ids = fact.metadata.get("context_connection_ids", [])
            if fused_ids:
                available = {edge["id"] for edge in self.connections.snapshot()["connections"]
                             if edge["kind"] == "excitatory" and edge["target_center_id"] == event.center_id}
                if not set(fused_ids).issubset(available):
                    return "connection_revoked"
            edge_id = fact.metadata.get("context_connection_id")
            if edge_id and not any(edge.id == edge_id and edge.kind == "excitatory"
                                   for edge in self.connections.outgoing(fact.metadata.get("source_center_id"))):
                return "connection_revoked"
            return self._context_deferred_reason(event.center_id, observed_at)

    def tick(self, *, now: datetime | None = None) -> None:
        now = now or _now()
        with self.storage._lock:
            before = deepcopy(self._state)
            try:
                self._tick(now)
            except BaseException:
                # A failed checkpoint must not silently move the live cursor
                # past effects that still need reconciliation on the next tick.
                self._state = self.storage.load_component_state(self.key) or before
                raise

    def _tick(self, now: datetime) -> None:
        with self.storage._lock:
            # A tick holds Storage's lock: reuse this validated graph while
            # traversing local contexts instead of reloading every edge list.
            graph = self.connections.snapshot(now=now)
            self._repair_revocations(graph)
            if not self.config.enabled or self._state["paused"]:
                # Retain both observation cursors, including settled results
                # from in-flight turns, until the operator resumes activity.
                return
            outgoing = {}
            for edge in graph["connections"]:
                outgoing.setdefault(edge["source_center_id"], []).append(edge)
            retained_contexts = {}

            def retained(center_id):
                if center_id not in retained_contexts:
                    retained_contexts[center_id] = self._current_context(center_id, retain_paused=True)
                return retained_contexts[center_id]

            inhibition_changed = False
            lineage = self._subject_lineage()
            observations = self.ledger.list_events(
                after_seq=self._state["observed_cursor"], limit=100, world_id=self.world_id,
                kind=CausalEventKind.ASSISTANT_RESULT, status=CausalEventStatus.SETTLED,
            )
            for result in observations:
                if result.engram_id in lineage and self._source_root(result) is not None:
                    self._state["contexts"][self._key(result.center_id)] = {
                        "center_id": result.center_id, "result_event_id": result.id,
                        "settled_at": (result.settled_at or result.created_at).isoformat(),
                    }
                    # Numeric inhibition is independent of the content quota.
                    # Observe all local brakes before considering new content.
                    if retained(result.center_id):
                        for edge in outgoing.get(result.center_id, ()):
                            if (edge["kind"] == "inhibitory" and result.seq > edge["created_after_seq"]
                                    and edge["weight"] >= self.config.propagation_threshold
                                    and retained(edge["target_center_id"])):
                                self.connections.apply_inhibition(
                                    edge["target_center_id"], edge["weight"],
                                    now=result.settled_at or result.created_at,
                                    source_event_id=result.id,
                                )
                                inhibition_changed = True
                self._state["observed_cursor"] = result.seq
            results = self.ledger.list_events(
                after_seq=self._state["cursor"], limit=100, world_id=self.world_id,
                kind=CausalEventKind.ASSISTANT_RESULT, status=CausalEventStatus.SETTLED,
            )
            for result in results:
                root = self._source_root(result)
                if result.engram_id not in lineage or root is None:
                    self._state["cursor"] = result.seq
                    continue
                center_id = result.center_id
                depth = root.metadata.get("depth", 0) if root else 0
                depth = depth if type(depth) is int and depth >= 0 else 0
                if retained(center_id):
                    for edge in outgoing.get(center_id, ()):
                        if (result.seq <= edge["created_after_seq"]
                                or edge["weight"] < self.config.propagation_threshold
                                or not retained(edge["target_center_id"])):
                            continue
                        if edge["kind"] == "excitatory" and depth < self.config.max_propagation_depth and result.content:
                            try:
                                self.ledger.enqueue_context_propagation(
                                    source_result_id=result.id, target_center_id=edge["target_center_id"],
                                    connection_id=edge["id"], depth=depth + 1, engram_id=self.subject(),
                                    priority=edge["weight"], admission_guard=self._guard,
                                    runtime_fence=self.fence(),
                                )
                            except _QuotaReached:
                                # Persist observed state but hold the cursor at
                                # this result. Earlier deliveries are idempotent.
                                self._save(now)
                                return
                self._state["cursor"] = result.seq
            if results or observations:
                self._save(now)
            # Drain history before making fresh local opportunities.
            if len(results) == 100 or len(observations) == 100:
                return
            prior = self._state.get("last_check_at")
            if prior:
                elapsed = (now - self._checkpoint_time(prior)).total_seconds()
                if elapsed < 0:
                    # A wall-clock correction must neither freeze opportunities
                    # indefinitely nor grant an immediate fresh check. Reanchor
                    # once, durably, and wait one complete configured interval.
                    self._state["last_check_at"] = now.isoformat()
                    self._save(now)
                    return
                if elapsed < self.config.check_interval_seconds:
                    return
            self._state["last_check_at"] = now.isoformat()
            if (self.config.spontaneous_rate == 0
                    or self._used(now) >= self.config.max_background_pulses_per_hour):
                self._save(now)
                return
            if inhibition_changed:
                graph = self.connections.snapshot(now=now)
            inhibition_by_center = {trace["center_id"]: trace["level"] for trace in graph["inhibition"]}
            # Oldest eligible local result gets first opportunity; newly
            # settled results move to the end instead of monopolizing budget.
            entries = sorted(self._state["contexts"].values(), key=lambda item: (item["settled_at"], item["center_id"] or ""))
            for entry in entries:
                center_id = entry["center_id"]
                inhibition = inhibition_by_center.get(center_id, 0.0)
                if not self._ready(center_id, now, inhibition=inhibition) or self._open(center_id):
                    continue
                age = max(0.0, (now - datetime.fromisoformat(entry["settled_at"])).total_seconds())
                activity = math.exp(-math.log(2) * age / self.config.activity_halflife_seconds)
                probability = self.config.spontaneous_rate * activity / (1.0 + inhibition)
                if random.random() >= probability:
                    continue
                result = self.ledger.get_event(entry["result_event_id"])
                if result is None or result.engram_id not in lineage:
                    continue
                root = self.ledger.get_event(result.parent_event_id) if result.parent_event_id else None
                depth = root.metadata.get("depth", 0) if root else 0
                try:
                    self.ledger.enqueue(
                        world_id=self.world_id, flow=None, domain=CausalEventDomain.PULSE,
                        kind=CausalEventKind.SPONTANEOUS, source=CausalEventSource.SELF,
                        content=None, parent_event_id=result.id, engram_id=self.subject(),
                        center_id=center_id,
                        metadata={"context_pulse": "reentry", "priority": probability,
                                  "source_result_id": result.id, "depth": depth},
                        idempotency_key=f"context-reentry:{self.world_id}:{result.id}",
                        admission_guard=self._guard, runtime_fence=self.fence(),
                    )
                except _QuotaReached:
                    break
            self._save(now)

    def snapshot(self, *, now: datetime | None = None) -> dict:
        now = now or _now()
        with self.storage._lock:
            used = self._used(now)
            graph = self.connections.snapshot(now=now)
            inhibition_by_center = {trace["center_id"]: trace["level"] for trace in graph["inhibition"]}
            recent = self.storage._conn.execute(
                "SELECT id, center_id, kind, status, created_at, parent_event_id FROM causal_events "
                "WHERE world_id = ? AND kind IN ('spontaneous', 'propagation', 'pulse') "
                "AND (json_extract(metadata, '$.context_pulse') IN ('reentry', 'convergence') "
                "OR json_extract(metadata, '$.context_connection_id') IS NOT NULL) "
                "ORDER BY seq DESC LIMIT 20", (self.world_id,),
            ).fetchall()
            # Count the entire durable backlog, independently of the engine's
            # bounded working set and the twenty-row recent activity display.
            queue = self.storage._conn.execute(
                "SELECT COUNT(CASE WHEN status = 'queued' THEN 1 END), "
                "COUNT(CASE WHEN status = 'running' THEN 1 END), "
                "MIN(CASE WHEN status = 'queued' THEN created_at END) "
                "FROM causal_events WHERE world_id = ? AND status IN ('queued', 'running') "
                "AND kind IN ('spontaneous', 'propagation', 'pulse') "
                "AND (json_extract(metadata, '$.context_pulse') IN ('reentry', 'convergence') "
                "OR json_extract(metadata, '$.context_connection_id') IS NOT NULL)",
                (self.world_id,),
            ).fetchone()
            activations = []
            # Import only at observation time to avoid a context/engine module
            # dependency during construction. The sample stays bounded at 20.
            from pulse_system.core.pulse.engine import PulseEvent
            for row in recent:
                item = dict(zip(("eventId", "centerId", "kind", "status", "createdAt", "sourceEventId"), row))
                if item["status"] == "queued":
                    event = self.ledger.get_event(item["eventId"])
                    item["deferredReason"] = self.deferred_reason(PulseEvent.from_causal_event(event), now)
                else:
                    item["deferredReason"] = None
                activations.append(item)
            return {
                "enabled": self.config.enabled,
                "paused": self._state["paused"],
                "budget": {"limit": self.config.max_background_pulses_per_hour,
                           "used": used, "remaining": max(0, self.config.max_background_pulses_per_hour - used),
                           "windowSeconds": 3600, "unit": "background pulse admissions"},
                "cursor": self._state["cursor"],
                "observedCursor": self._state["observed_cursor"],
                "contexts": [dict(item, inhibition=inhibition_by_center.get(item["center_id"], 0.0))
                             for item in self._state["contexts"].values()],
                "connections": graph.get("connections", []),
                "queue": {"queued": queue[0], "running": queue[1], "oldestQueuedAt": queue[2]},
                "recentActivations": activations,
            }
