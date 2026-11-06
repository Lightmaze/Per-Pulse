"""Durable weekly admission for autonomous physical provider requests.

This is a World component, not another runtime or billing authority. The host
is responsible for deriving a trusted original causal event before calling
``reserve``. Every wire attempt (including a retry) needs a fresh ticket.
An attempt is marked dispatched *before* the HTTP send; loss of the process
therefore consumes an uncertain slot rather than granting a free replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4


class WeeklyRequestUnavailable(RuntimeError):
    """The person's autonomous request allowance is disabled or exhausted."""


@dataclass(frozen=True, slots=True)
class RequestTicket:
    attempt_id: str
    week: str
    source_event_id: str


def _utc(now: datetime | None) -> datetime:
    stamp = now or datetime.now(timezone.utc)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("time must be timezone aware")
    return stamp.astimezone(timezone.utc)


def _week(now: datetime) -> str:
    return (now.date() - timedelta(days=now.weekday())).isoformat()


class WeeklyRequestBudget:
    """Serialize native World-component request accounting under its lock.

    The policy is opt-in and remains disabled until a person enables it. A
    reservation holds one slot; dispatch consumes it permanently for that UTC
    calendar week, regardless of provider usage reports or transport success.
    Nothing on this surface can authorize a tool effect or a new model turn.
    """

    def __init__(self, storage, world_id: str):
        if not isinstance(world_id, str) or not world_id.strip():
            raise ValueError("world identity is required")
        self.storage = storage
        self.world_id = world_id
        self._policy_key = f"autonomous-provider-policy-v1:{world_id}"
        self._key_prefix = f"autonomous-provider-week-v1:{world_id}:"

    def _load_policy(self) -> dict:
        state = self.storage.load_component_state(self._policy_key)
        if state is None:
            return {"version": 1, "enabled": False, "weekly_limit": 0}
        if (state.get("version") != 1 or type(state.get("enabled")) is not bool
                or type(state.get("weekly_limit")) is not int
                or not 0 <= state["weekly_limit"] <= 100_000):
            raise ValueError("invalid persistent weekly request policy")
        return state

    def _load_week(self, week: str) -> dict:
        state = self.storage.load_component_state(self._key_prefix + week)
        if state is None:
            return {"version": 1, "world_id": self.world_id, "week": week, "attempts": {}}
        if (state.get("version") != 1 or state.get("world_id") != self.world_id
                or state.get("week") != week or not isinstance(state.get("attempts"), dict)):
            raise ValueError("invalid persistent weekly request ledger")
        return state

    def _save_week(self, state: dict) -> None:
        self.storage.save_component_state(self._key_prefix + state["week"], state)

    def configure(self, *, enabled: bool, weekly_limit: int) -> dict:
        if type(enabled) is not bool or type(weekly_limit) is not int or not 0 <= weekly_limit <= 100_000:
            raise ValueError("weekly request settings must be a boolean and 0..100000 requests")
        if enabled and weekly_limit == 0:
            raise ValueError("enabled autonomous requests need a positive weekly allowance")
        with self.storage._lock:
            policy = {"version": 1, "enabled": enabled, "weekly_limit": weekly_limit}
            self.storage.save_component_state(self._policy_key, policy)
            return dict(policy)

    def status(self, *, now: datetime | None = None) -> dict:
        week = _week(_utc(now))
        with self.storage._lock:
            policy, state = self._load_policy(), self._load_week(week)
            counts = {name: 0 for name in ("reserved", "dispatched", "completed", "rejected", "uncertain", "abandoned")}
            for item in state["attempts"].values():
                counts[item["status"]] += 1
            held = counts["reserved"]
            consumed = counts["dispatched"] + counts["completed"] + counts["rejected"] + counts["uncertain"]
            return {"world_id": self.world_id, "week": week, **policy,
                    "held": held, "consumed": consumed,
                    "remaining": max(0, policy["weekly_limit"] - held - consumed),
                    "attempts": sum(counts.values()), "states": counts}

    def reserve(self, *, source_event_id: str, now: datetime | None = None) -> RequestTicket:
        """Hold one physical send; source_event_id must come from trusted World admission."""
        if not isinstance(source_event_id, str) or not source_event_id.strip():
            raise ValueError("a trusted causal source identity is required")
        stamp = _utc(now)
        week = _week(stamp)
        with self.storage._lock:
            policy = self._load_policy()
            state = self._load_week(week)
            active = sum(item["status"] != "abandoned" for item in state["attempts"].values())
            if not policy["enabled"] or active >= policy["weekly_limit"]:
                raise WeeklyRequestUnavailable("autonomous weekly provider requests are paused or exhausted")
            attempt_id = uuid4().hex
            state["attempts"][attempt_id] = {"source_event_id": source_event_id,
                "status": "reserved", "created_at": stamp.isoformat(), "dispatched_at": None,
                "settled_at": None}
            self._save_week(state)
            return RequestTicket(attempt_id, week, source_event_id)

    def _change(self, ticket: RequestTicket, *, old: str, new: str, now: datetime | None) -> None:
        if not isinstance(ticket, RequestTicket):
            raise ValueError("request ticket is required")
        stamp = _utc(now)
        with self.storage._lock:
            state = self._load_week(ticket.week)
            entry = state["attempts"].get(ticket.attempt_id)
            if entry is None or entry["source_event_id"] != ticket.source_event_id:
                raise ValueError("unknown or mismatched provider request ticket")
            if entry["status"] == new:
                if new == "dispatched":
                    raise ValueError("one provider ticket cannot authorize two physical sends")
                return
            if entry["status"] != old:
                raise ValueError("provider request ticket cannot be reused or rewritten")
            if new == "dispatched":
                # Recheck the policy at the wire boundary, not only when the
                # ticket was reserved. A lowered limit must fence outstanding
                # reservations before they become new provider charges.
                policy = self._load_policy()
                if not policy["enabled"]:
                    raise WeeklyRequestUnavailable("autonomous provider requests are paused")
                if ticket.week != _week(stamp):
                    raise WeeklyRequestUnavailable("weekly reservation expired before dispatch")
                consumed = sum(
                    item["status"] in {"dispatched", "completed", "rejected", "uncertain"}
                    for item in state["attempts"].values()
                )
                if consumed >= policy["weekly_limit"]:
                    raise WeeklyRequestUnavailable("autonomous weekly allowance was lowered")
            entry["status"] = new
            if new == "dispatched":
                entry["dispatched_at"] = stamp.isoformat()
            else:
                entry["settled_at"] = stamp.isoformat()
            self._save_week(state)

    def dispatch(self, ticket: RequestTicket, *, now: datetime | None = None) -> None:
        """Durably consume a slot before the HTTP send (no refund on errors)."""
        self._change(ticket, old="reserved", new="dispatched", now=now)

    def settle(self, ticket: RequestTicket, outcome: str, *, now: datetime | None = None) -> None:
        if outcome not in {"completed", "rejected", "uncertain"}:
            raise ValueError("invalid provider attempt outcome")
        self._change(ticket, old="dispatched", new=outcome, now=now)

    def abandon(self, ticket: RequestTicket, *, now: datetime | None = None) -> None:
        """Release a ticket only if dispatch was never recorded."""
        self._change(ticket, old="reserved", new="abandoned", now=now)

    def reconcile(self, *, now: datetime | None = None) -> int:
        """On restart, classify old in-flight dispatches as uncertain (not free)."""
        stamp = _utc(now)
        with self.storage._lock:
            state = self._load_week(_week(stamp))
            changed = 0
            for entry in state["attempts"].values():
                if entry["status"] == "dispatched":
                    entry["status"] = "uncertain"
                    entry["settled_at"] = stamp.isoformat()
                    changed += 1
                elif entry["status"] == "reserved":
                    # The send path always commits dispatch *before* calling HTTP.
                    entry["status"] = "abandoned"
                    entry["settled_at"] = stamp.isoformat()
                    changed += 1
            if changed:
                self._save_week(state)
            return changed


class WeeklyRequestGate:
    """Trusted Kernel/World bridge for one adapter's physical send boundary.

    The resolver is host-supplied and must walk the original World causal
    source, including autonomous descendants; it is never a model argument.
    ``None`` means a genuine user-sourced request outside this pool.
    """

    def __init__(self, budget: WeeklyRequestBudget, source_for_scope, *, admission_guard=None):
        if not callable(source_for_scope):
            raise TypeError("a trusted scope-to-causal-source resolver is required")
        if admission_guard is not None and not callable(admission_guard):
            raise TypeError("the admission guard must be callable")
        self.budget = budget
        self.source_for_scope = source_for_scope
        self.admission_guard = admission_guard

    def reserve(self, scope):
        from pulse_harness.core.errors import ProviderUnavailable
        source_event_id = self.source_for_scope(scope)
        if source_event_id is None:
            return None
        try:
            return self.budget.reserve(source_event_id=source_event_id)
        except WeeklyRequestUnavailable as exc:
            raise ProviderUnavailable(
                "Autonomous weekly provider requests are disabled or exhausted.",
                code="autonomous_weekly_requests_exhausted",
            ) from exc

    def dispatch(self, ticket):
        if ticket is not None:
            from pulse_harness.core.errors import ProviderUnavailable
            try:
                if self.admission_guard is not None:
                    self.admission_guard()
                self.budget.dispatch(ticket)
            except WeeklyRequestUnavailable as exc:
                raise ProviderUnavailable(
                    "Autonomous weekly provider requests were paused before dispatch.",
                    code="autonomous_weekly_requests_paused",
                ) from exc

    def settle(self, ticket, outcome):
        if ticket is not None:
            self.budget.settle(ticket, outcome)

    def abandon(self, ticket):
        if ticket is not None:
            self.budget.abandon(ticket)


def world_source_for_scope(ledger, world_id: str, scope) -> str | None:
    """Derive the original request pool from immutable World causal ancestry.

    A direct user stimulus is the *only* free foreground root. SELF and
    unattended environment roots consume autonomous allowance. Delegation,
    TUNNEL and response-review descendants retain their ancestral pool; a
    missing, cyclic or foreign ledger lineage fails closed instead of being
    silently billed to the user's direct Work.
    """
    from pulse_harness.core.errors import ProviderUnavailable
    from pulse_system.core.types import CausalEventKind, CausalEventSource

    def unknown():
        return ProviderUnavailable(
            "The original World causal source cannot be verified; request not sent.",
            code="autonomous_request_source_unverified",
        )

    if (not isinstance(world_id, str) or not world_id.strip()
            or getattr(scope, "world_id", None) != world_id
            or not getattr(scope, "turn_id", None)):
        raise unknown()
    turn = ledger.get_turn_for_world(scope.turn_id, world_id)
    if turn is None or turn.engram_id != scope.engram_id:
        raise unknown()
    event = ledger.get_event(turn.event_id)
    seen: set[str] = set()
    autonomous_root: str | None = None
    for _ in range(256):
        if (event is None or event.world_id != world_id or event.id in seen):
            raise unknown()
        seen.add(event.id)
        # A real user stimulus begins a fresh foreground request, even if the
        # original Work was opened from an older autonomous or succession
        # event. An autonomous activation *after* that user input is still
        # charged. Do not let an earlier SELF ancestor relabel a newer user
        # instruction as unattended spending.
        if event.source is CausalEventSource.USER and event.kind is CausalEventKind.STIMULUS:
            return autonomous_root
        # Assistant results and tool receipts are SELF-authored even during
        # an ordinary user-requested Work. They do not start a new autonomous
        # request pool. Only a native *activation/input* can convert a user
        # lineage into autonomous work (e.g. a later spontaneous reentry).
        if (
            event.source in {
                CausalEventSource.SELF, CausalEventSource.HABITAT,
                CausalEventSource.SENSORY, CausalEventSource.PROPAGATION,
            }
            and event.kind in {
                CausalEventKind.STIMULUS,
                CausalEventKind.SPONTANEOUS,
                CausalEventKind.PULSE,
                CausalEventKind.PROPAGATION,
                CausalEventKind.HABITAT_OBSERVATION,
                CausalEventKind.HABITAT_CONSEQUENCE,
            }
        ):
            autonomous_root = event.id
        if event.parent_event_id is None:
            if autonomous_root is not None:
                return autonomous_root
            if event.source is CausalEventSource.USER and event.kind is CausalEventKind.STIMULUS:
                return None
            # A standalone SYSTEM or DELEGATION event cannot obtain a free
            # foreground attribution by falling through the source resolver.
            raise unknown()
        event = ledger.get_event(event.parent_event_id)
    raise unknown()
