"""Page-local activation fields inside the existing, single-subject World.

There is no model loop or conversation store here. Runtime advances these
fields at its ordinary tick boundary; threshold crossings enter its existing
causal ledger, Dendrite, Pulse Engine and Kernel. Notes are retained thoughts,
not system instructions. Cross-context connections carry numeric influence
and source addresses, never copies of another context's transcript.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Callable

from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core import HarnessScope, TurnStatus
from pulse_harness.core.contracts import ActionStatus
from pulse_system.core.types import HarnessTurnState, CausalEventStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContextBodyError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def number(value: Any, low: float, high: float, name: str) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ContextBodyError("invalid_attention", f"{name} must be a finite number from {low:g} to {high:g}.")
    return float(value)


@dataclass(frozen=True)
class ContextBodyConfig:
    excitation_tau: float = 30.0
    inhibition_tau: float = 90.0
    refractory_seconds: float = 20.0
    max_elapsed_seconds: float = 5.0

    def __post_init__(self):
        for name in ("excitation_tau", "inhibition_tau", "max_elapsed_seconds"):
            number(getattr(self, name), 0.001, 86400.0, name)
        number(self.refractory_seconds, 0.0, 86400.0, "refractory_seconds")


class ContextBody:
    """Durable numeric state and bounded causal ingress for Home and pages."""

    def __init__(self, runtime, *, configured: bool, example: bool = False,
                 config: ContextBodyConfig | None = None,
                 clock: Callable[[], datetime] = utcnow):
        self.runtime = runtime
        self.configured, self.example = configured, example
        self.config = config or ContextBodyConfig()
        self.clock = clock
        self.key = "per-pulse-context-body-v1:" + runtime.world_id
        # Reopening preserves the field but never simulates offline thinking.
        # The local activation grant is deliberately paused on process start.
        with self._transaction() as (conn, state):
            if not state:
                cursor = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM causal_events WHERE world_id = ?",
                                      (runtime.world_id,)).fetchone()[0]
                state.update(version=1, cursor=cursor, contexts={}, connections=[],
                             pendingUpdates={}, appliedActions=[], sequence=0)
            state.setdefault("turnRevisions", {})
            state.setdefault("derivedCount", 0)
            state.update(enabled=False, remaining=0, lastAdvancedAt=self.clock().isoformat())
            self._sync(state)
            self._cancel_queued(conn, state=state)

    @contextmanager
    def _transaction(self):
        r = self.runtime
        r._require_runtime_owner()
        fence = r._current_runtime_fence()
        with r.causal_ledger._transaction() as conn:
            r.causal_ledger._assert_runtime_fence_uncommitted(conn, fence)
            row = conn.execute("SELECT state FROM component_state WHERE component = ?", (self.key,)).fetchone()
            state = json.loads(row[0]) if row else {}
            yield conn, state
            self._write(conn, state)

    def _write(self, conn, state):
        conn.execute("INSERT INTO component_state (component, state, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(component) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                     (self.key, json.dumps(state, allow_nan=False), self.clock().isoformat()))

    def _scopes(self) -> dict[str, dict]:
        r = self.runtime
        scopes = {"home": {"pageId": "home", "title": "Home", "centerId": None,
                           "engramId": r.continuity_engram_id, "available": True}}
        for front in r.world.list_task_fronts():
            if front.focal_engram_id != r.continuity_engram_id:
                continue
            center = r.world.get_activity_center(front.center_id)
            scopes[front.id] = {"pageId": front.id, "title": front.title,
                                "centerId": front.center_id, "engramId": front.focal_engram_id,
                                "available": front.status.value == "open" and center is not None
                                and center.status.value in {"active", "dormant"}}
        return scopes

    def _sync(self, state):
        for page_id in self._scopes():
            state["contexts"].setdefault(page_id, {
                "note": "", "drive": 0.0, "excitation": 0.0, "inhibition": 0.0,
                "revision": 0, "pulseCount": 0, "resultCount": 0, "lastPulseAt": None,
                "pendingEventId": None, "lastResultId": None, "heldByTurn": None,
                "evidence": [],
            })

    def _page(self, page_id):
        scope = self._scopes().get(page_id)
        if scope is None:
            raise ContextBodyError("page_not_found", "This page does not exist.", 404)
        if not scope["available"]:
            raise ContextBodyError("page_unavailable", "This page is paused or closed.", 409)
        return scope

    def _scope_page(self, scope):
        for page in self._scopes().values():
            expected = "work:" + page["centerId"] if page["centerId"] else ""
            if scope.engram_id == page["engramId"] and scope.context_id == expected:
                return page["pageId"]
        raise ScopeConflict("attention belongs to this subject's current Home or Work context")

    def view(self) -> dict:
        r = self.runtime
        with r.storage._lock:
            state = r.storage.load_component_state(self.key)
            scopes = self._scopes()
            contexts = []
            for page_id, scope in scopes.items():
                field = state["contexts"].get(page_id)
                if field is None:
                    field = {"note": "", "drive": 0.0, "excitation": 0.0, "inhibition": 0.0,
                             "pulseCount": 0, "resultCount": 0, "lastPulseAt": None, "evidence": []}
                contexts.append({**scope, **field, "readiness": max(0.0, field["excitation"] - field["inhibition"])})
            return {"enabled": state["enabled"], "remaining": state["remaining"],
                    "configured": self.configured, "contexts": contexts,
                    "connections": state["connections"], "sequence": state["sequence"],
                    "derivedCount": state.get("derivedCount", 0),
                    "lastAdvancedAt": state["lastAdvancedAt"]}

    def _cancel_queued(self, conn, page_id: str | None = None, *, state: dict | None = None):
        now = self.clock().isoformat()
        # Admission and this cancellation serialize on the existing World
        # transaction. Already-running turns finish; human inputs are untouched.
        conn.execute("UPDATE causal_events SET status='cancelled', updated_at=?, settled_at=? "
                     "WHERE world_id=? AND status='queued' AND kind='spontaneous' "
                     "AND json_extract(metadata, '$.context_body') = ? "
                     "AND (? IS NULL OR json_extract(metadata, '$.page_id') = ?)",
                     (now, now, self.runtime.world_id, self.key, page_id, page_id))
        if page_id is None:
            conn.execute("UPDATE causal_events SET status='cancelled', updated_at=?, settled_at=? "
                         "WHERE world_id=? AND status='queued' AND EXISTS ("
                         "SELECT 1 FROM component_state request JOIN causal_events root "
                         "ON root.id=json_extract(request.state, '$.attention_root_event_id') "
                         "WHERE request.component=causal_events.idempotency_key "
                         "AND root.world_id=? AND json_extract(root.metadata, '$.context_body')=?)",
                         (now, now, self.runtime.world_id, self.runtime.world_id, self.key))
        if state is not None:
            for field in state["contexts"].values():
                pending = field["pendingEventId"]
                if pending and conn.execute("SELECT 1 FROM causal_events WHERE id=? AND status='cancelled'",
                                            (pending,)).fetchone():
                    field["pendingEventId"] = None

    def run(self, max_pulses: int) -> dict:
        if type(max_pulses) is not int or not 1 <= max_pulses <= 100:
            raise ContextBodyError("invalid_pulse_budget", "Choose from 1 to 100 local activations.")
        if not self.configured or self.example:
            raise ContextBodyError("provider_unconfigured", "Connect a model before continuing in the background.", 409)
        with self._transaction() as (_conn, state):
            self._sync(state)
            state.update(enabled=True, remaining=max_pulses, lastAdvancedAt=self.clock().isoformat())
        return self.view()

    def pause(self) -> dict:
        with self._transaction() as (conn, state):
            state.update(enabled=False, remaining=0)
            self._cancel_queued(conn, state=state)
        return self.view()

    def admit_derived(self, conn, *, source_event_id: str, request_id: str) -> None:
        """Reserve a continuation caused by a local pulse in its ingress commit.

        Native Work calls this only from its existing causal admission guard,
        after checking settled source/action ownership. Its idempotency lookup
        precedes the guard, so replaying delivery cannot charge twice.
        """
        from pulse_system.service.native_work import NativeWorkDeferred
        r = self.runtime
        root = r.causal_ledger.get_event(source_event_id)
        if (root is None or root.world_id != r.world_id
                or root.metadata.get("context_body") != self.key
                or root.source.value != "self" or root.kind.value != "spontaneous"):
            raise ScopeConflict("derived attention has no matching local root")
        row = conn.execute("SELECT state FROM component_state WHERE component=?", (self.key,)).fetchone()
        state = json.loads(row[0])
        if not state["enabled"] or state["remaining"] <= 0:
            raise NativeWorkDeferred("The shared background allowance is paused or exhausted.")
        state["remaining"] -= 1
        state["sequence"] += 1
        state["derivedCount"] = state.get("derivedCount", 0) + 1
        state["enabled"] = state["remaining"] > 0
        self._write(conn, state)

    @staticmethod
    def _validate_hold(note, drive):
        if not isinstance(note, str) or not note.strip() or len(note) > 4096 or "\x00" in note:
            raise ContextBodyError("invalid_attention", "A held thought must contain 1 to 4096 characters.")
        return note.strip(), number(drive, 0, 1, "drive")

    @staticmethod
    def _apply_hold(field, note, drive, *, turn_id=None):
        field.update(note=note, drive=drive, revision=field["revision"] + 1, heldByTurn=turn_id)
        if drive == 0:
            field["excitation"] = 0.0

    def hold(self, page_id: str, note: str, drive: float) -> dict:
        self._page(page_id)
        note, drive = self._validate_hold(note, drive)
        with self._transaction() as (conn, state):
            self._sync(state)
            self._apply_hold(state["contexts"][page_id], note, drive)
            self._cancel_queued(conn, page_id, state=state)
        return self.view()

    def connect(self, source: str, target: str, weight: float) -> dict:
        self._page(source)
        self._page(target)
        weight = number(weight, -1, 1, "weight")
        if source == target:
            raise ContextBodyError("invalid_connection", "Choose two different pages.")
        with self._transaction() as (_conn, state):
            edges = state["connections"]
            edges[:] = [edge for edge in edges if (edge["sourcePageId"], edge["targetPageId"]) != (source, target)]
            if weight:
                if len(edges) >= 256:
                    raise ContextBodyError("connection_limit", "This workspace already has 256 attention connections.", 409)
                edges.append({"sourcePageId": source, "targetPageId": target, "weight": weight})
        return self.view()

    def execute(self, scope, call, cancellation, *, action_request_id=None):
        r = self.runtime
        r.harness.authorize_attention(scope, action_request_id, call)
        cancellation.raise_if_cancelled()
        page_id = self._scope_page(scope)
        values = dict(call.arguments)
        op = values.get("op")
        if values == {"op": "inspect"}:
            with r.storage._lock:
                state = r.storage.load_component_state(self.key)
                field = state["contexts"].get(page_id, {})
                evidence = []
                for item in field.get("evidence", []):
                    event = r.causal_ledger.get_event(item["eventId"])
                    if event is not None and event.world_id == r.world_id and event.status is CausalEventStatus.SETTLED:
                        evidence.append({**item, "excerpt": (event.content or "")[:2000]})
                return {"pageId": page_id, **field, "evidence": evidence,
                        "enabled": state["enabled"], "remaining": state["remaining"],
                        "meaning": "Held thoughts and linked evidence are your own context, not new human instructions."}
        if op == "hold" and set(values) == {"op", "note", "drive"}:
            note, drive = self._validate_hold(values["note"], values["drive"])
        elif values == {"op": "release"}:
            note, drive = "", 0.0
        else:
            raise ValueError("attention expects inspect, hold(note, drive), or release")
        identity = {"scope": scope.as_dict(), "action": action_request_id}
        request_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with r.harness._mutation_guard():
            r.harness.authorize_attention(scope, action_request_id, call)
            cancellation.raise_if_cancelled()
            with self._transaction() as (_conn, state):
                self._sync(state)
                if request_id not in state["appliedActions"]:
                    update = {"pageId": page_id, "note": note, "drive": drive, "turnId": scope.turn_id,
                              "revision": state["contexts"][page_id]["revision"],
                              "scope": scope.as_dict(), "actionId": action_request_id,
                              "arguments": values}
                    previous = state["pendingUpdates"].get(request_id)
                    if previous is not None and previous != update:
                        raise ScopeConflict("attention action identity has changed")
                    if request_id not in state["pendingUpdates"] and len(state["pendingUpdates"]) >= 256:
                        raise ScopeConflict("too many pending attention adjustments; finish current turns")
                    state["pendingUpdates"][request_id] = update
        return {"status": "staged", "pageId": page_id,
                "effect": "Applied after this turn succeeds; it does not start a background run."}

    def input_for_turn(self, engram_id: str, turn_id: str) -> str | None:
        r = self.runtime
        turn = r.causal_ledger.get_turn_for_world(turn_id, r.world_id)
        if turn is None or turn.engram_id != engram_id:
            return None
        event = r.causal_ledger.get_event(turn.event_id)
        if event is None:
            return None
        # Capture which local state this computation actually observed. A
        # later user change must not be damped by an older turn's result.
        matching = [page_id for page_id, page in self._scopes().items()
                    if page["centerId"] == event.center_id and page["engramId"] == engram_id]
        if len(matching) != 1:
            return None
        with self._transaction() as (_conn, state):
            self._sync(state)
            state["turnRevisions"].setdefault(turn_id, state["contexts"][matching[0]]["revision"])
        if event.metadata.get("context_body") != self.key:
            return None
        # No model-authored note is interpolated into system authority.
        return ("A local attention field crossed its activation threshold in this same subject's context. "
                "This is internal continuation, not a new human message. Use attention inspect to read "
                "the retained thought and attributed evidence, and your existing history as needed. "
                "Continue only if useful; attention release lets this context become quiet. "
                "Event: " + event.id)

    def _settle_updates(self, conn, state):
        r = self.runtime
        successful = {}
        for request_id, update in list(state["pendingUpdates"].items()):
            turn = r.causal_ledger.get_turn_for_world(update["turnId"], r.world_id)
            if turn is not None and turn.state is HarnessTurnState.RUNNING:
                continue
            field = state["contexts"].get(update["pageId"])
            source_succeeded = False
            if turn is not None and turn.state is HarnessTurnState.SETTLED and field is not None:
                try:
                    scope = HarnessScope.from_dict(update["scope"])
                    action = r.kernel_service.store.get_action(scope, update["actionId"])
                    kernel_turn = r.kernel_service.store.get_turn(scope)
                    source_succeeded = (kernel_turn.status is TurnStatus.SUCCEEDED
                                        and action.status is ActionStatus.SUCCEEDED
                                        and action.tool_name == "attention"
                                        and dict(action.arguments) == update["arguments"])
                except ScopeConflict:
                    pass
            if source_succeeded:
                # Dict insertion order is action order. Retain the final
                # successfully persisted action, including hold -> release.
                successful[(update["turnId"], update["pageId"])] = update
            del state["pendingUpdates"][request_id]
            state["appliedActions"] = (state["appliedActions"] + [request_id])[-512:]
        for update in successful.values():
            field = state["contexts"][update["pageId"]]
            # A later user edit wins over a stale model's retained thought.
            if field["revision"] == update["revision"]:
                self._apply_hold(field, update["note"], update["drive"], turn_id=update["turnId"])
                self._cancel_queued(conn, update["pageId"], state=state)

    def _feedback(self, conn, state, scopes):
        r = self.runtime
        by_center = {scope["centerId"]: page_id for page_id, scope in scopes.items()}
        lineage = {row[0] for row in conn.execute(
            "WITH RECURSIVE lineage(id) AS (SELECT ? UNION SELECT g.predecessor_id "
            "FROM generation_transitions g JOIN lineage l ON g.successor_id=l.id WHERE g.state='committed') "
            "SELECT id FROM lineage", (r.continuity_engram_id,))}
        rows = conn.execute("SELECT seq,id,kind,source,center_id,engram_id,status FROM causal_events "
                            "WHERE world_id=? AND seq>? ORDER BY seq LIMIT 500",
                            (r.world_id, state["cursor"])).fetchall()
        for seq, event_id, kind, source, center, subject, status in rows:
            state["cursor"] = seq
            page_id = by_center.get(center)
            if page_id is None or subject not in lineage:
                continue
            field = state["contexts"][page_id]
            if kind == "stimulus" and source == "user":
                field["excitation"] = min(3.0, field["excitation"] + 0.2)
                continue
            if kind != "assistant_result" or status != "settled":
                continue
            turn = conn.execute("SELECT id FROM harness_turns WHERE result_event_id=? AND state='settled'",
                                (event_id,)).fetchone()
            if turn is None:
                continue
            field["resultCount"] += 1
            field["lastResultId"] = event_id
            admitted_revision = state["turnRevisions"].pop(turn[0], None)
            if field["heldByTurn"] != turn[0] and (admitted_revision is None or admitted_revision == field["revision"]):
                field["excitation"] *= 0.25
                field["drive"] *= 0.5
            from pulse_system.service import native_replay
            context_id = "work:" + center if center else ""
            kernel_scope = r.harness.scope_for(subject, context_id).for_turn(turn[0])
            if native_replay.mode(r.kernel_service.store, kernel_scope) is not None:
                continue
            for edge in state["connections"]:
                target = edge["targetPageId"]
                if edge["sourcePageId"] != page_id or target not in scopes:
                    continue
                receiving = state["contexts"][target]
                if edge["weight"] > 0:
                    receiving["excitation"] = min(3.0, receiving["excitation"] + edge["weight"])
                    evidence = {"eventId": event_id, "pageId": page_id}
                    receiving["evidence"] = (receiving["evidence"] + [evidence])[-8:]
                else:
                    receiving["inhibition"] = min(3.0, receiving["inhibition"] - edge["weight"])

    def advance(self, now: datetime | None = None) -> str | None:
        """Advance live state once, then offer at most one native local pulse."""
        r = self.runtime
        now = now or self.clock()
        if now.tzinfo is None:
            raise ValueError("attention time must be timezone-aware")
        # The storage lock spans state evolution and its separate ingress
        # transaction. No HTTP or model-state write can interleave them.
        with r.storage._lock:
            with self._transaction() as (conn, state):
                self._sync(state)
                scopes = self._scopes()
                self._settle_updates(conn, state)
                for turn_id in list(state["turnRevisions"]):
                    turn = r.causal_ledger.get_turn_for_world(turn_id, r.world_id)
                    if turn is None or turn.state not in {HarnessTurnState.RUNNING, HarnessTurnState.SETTLED}:
                        del state["turnRevisions"][turn_id]
                prior = datetime.fromisoformat(state["lastAdvancedAt"])
                elapsed = min(self.config.max_elapsed_seconds, max(0.0, (now - prior).total_seconds()))
                if now > prior:
                    state["lastAdvancedAt"] = now.isoformat()
                for field in state["contexts"].values():
                    target = 2.0 * field["drive"]
                    field["excitation"] = target + (field["excitation"] - target) * math.exp(-elapsed / self.config.excitation_tau)
                    field["inhibition"] *= math.exp(-elapsed / self.config.inhibition_tau)
                    pending = field["pendingEventId"]
                    if pending:
                        event = r.causal_ledger.get_event(pending)
                        if event is not None and event.status not in {CausalEventStatus.QUEUED, CausalEventStatus.RUNNING}:
                            field["pendingEventId"] = None
                            if event.status in {CausalEventStatus.FAILED, CausalEventStatus.UNCERTAIN}:
                                field["drive"] *= 0.5
                                field["inhibition"] = min(3.0, field["inhibition"] + 1.0)
                self._feedback(conn, state, scopes)
                candidates = []
                if state["enabled"] and state["remaining"] > 0 and self.configured and not self.example:
                    for page_id, scope in scopes.items():
                        field = state["contexts"][page_id]
                        if not scope["available"] or not field["note"] or field["drive"] <= 0 or field["pendingEventId"]:
                            continue
                        last = datetime.fromisoformat(field["lastPulseAt"]) if field["lastPulseAt"] else None
                        if last is not None and (now - last).total_seconds() < self.config.refractory_seconds:
                            continue
                        readiness = field["excitation"] - field["inhibition"]
                        if readiness < 1.0:
                            continue
                        pending = conn.execute("SELECT 1 FROM causal_events WHERE world_id=? AND center_id IS ? "
                                               "AND engram_id=? AND status IN ('queued','running') "
                                               "AND kind IN ('stimulus','spontaneous','propagation') LIMIT 1",
                                               (r.world_id, scope["centerId"], scope["engramId"])).fetchone()
                        if pending is None:
                            candidates.append((readiness, page_id))
                chosen = max(candidates, default=None)
            if chosen is None:
                return None
            page_id = chosen[1]
            scope, field = scopes[page_id], state["contexts"][page_id]
            sequence = state["sequence"] + 1
            event_id = "attention-" + hashlib.sha256(f"{self.key}:{sequence}".encode()).hexdigest()[:32]
            key = f"{self.key}:pulse:{sequence}"
            # The budget, local reset and event are one commit. A failed
            # insert rolls them all back, and a duplicate returns its event.
            def reserve(conn):
                state["remaining"] -= 1
                state["sequence"] = sequence
                state["enabled"] = state["remaining"] > 0
                field.update(excitation=0.0, lastPulseAt=now.isoformat(), pendingEventId=event_id,
                             pulseCount=field["pulseCount"] + 1)
                self._write(conn, state)
            event = r.causal_ledger.enqueue(
                world_id=r.world_id, domain="pulse", kind="spontaneous", source="self", content="",
                engram_id=scope["engramId"], center_id=scope["centerId"], event_id=event_id,
                idempotency_key=key, runtime_fence=r._current_runtime_fence(), admission_guard=reserve,
                metadata={"context_body": self.key, "page_id": page_id, "attention_revision": field["revision"],
                          "reason_code": "context_attention_threshold", "priority": 0.3},
            )
            return event.id
