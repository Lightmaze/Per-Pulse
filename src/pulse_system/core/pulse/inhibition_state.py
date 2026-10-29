"""Durable local inhibitory traces, not semantic tasks or Agent messages.

The original engine computes the dynamics. This sideband retains its sparse
numeric state so reopening a world does not silently remove lateral inhibition.
Only an applied impulse or committed succession writes; observation/restart is
read-only for the same decay policy and never allocates a model call.
"""
from __future__ import annotations

import math
from datetime import datetime

from pulse_system.core.types import EngramStatus, GenerationTransitionState

PREFIX = "pulse-inhibition-v1:"
EPSILON = 1e-4


def positive_number(value: object, name: str, *, zero: bool = False) -> float:
    try:
        finite = isinstance(value, (float, int)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if (isinstance(value, bool) or not finite or value < 0 or (not zero and value == 0)):
        raise ValueError(f"{name} must be a finite {'nonnegative' if zero else 'positive'} number")
    return float(value)


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("inhibition timestamp must be timezone-aware")
    return value


def decay(level: float, updated_at: datetime, now: datetime, tau: float) -> float:
    value = positive_number(level, "inhibition level", zero=True)
    span = max(0., (_aware(now) - _aware(updated_at)).total_seconds())
    return value * math.exp(-span / positive_number(tau, "inhibition tau"))


class InhibitionState:
    """A snapshot of current local state in the existing world component store.

    Standalone legacy engines without a world ID keep their in-memory behavior.
    The snapshot is sparse in inhibited nodes, not constant-cost in world size.
    It does not invent a history of earlier impulses or replay source turns.
    """

    def __init__(self, storage, *, world_id, ledger=None, fence=None):
        self.storage, self.world_id = storage, world_id
        self.ledger, self.fence = ledger, fence
        self.key = PREFIX + world_id if world_id else None

    def _current_target(self, engram_id: str) -> str | None:
        """Follow only committed succession, never names or uncommitted intent."""
        seen = set()
        while engram_id not in seen:
            seen.add(engram_id)
            engram = self.storage.get_engram(engram_id)
            if engram is None:
                raise ValueError("inhibition target is missing")
            if engram.status is EngramStatus.ACTIVE:
                return engram_id
            if self.ledger is None:
                return None
            transitions = self.ledger.list_generations(
                predecessor_id=engram_id, state=GenerationTransitionState.COMMITTED)
            if not transitions:
                return None  # Archived without succession is not reactivated.
            if len(transitions) != 1 or not transitions[0].successor_id:
                raise ValueError("inhibition target has ambiguous committed succession")
            engram_id = transitions[0].successor_id
        raise ValueError("inhibition succession is cyclic")

    def load(self, now: datetime, *, current_tau: float | None = None) -> dict[str, tuple[float, datetime]]:
        if self.key is None:
            return {}
        saved = self.storage.load_component_state(self.key)
        if saved is None:
            return {}
        if (not isinstance(saved, dict) or set(saved) != {"version", "world_id", "tau", "entries"}
                or type(saved["version"]) is not int or saved["version"] != 1
                or saved["world_id"] != self.world_id or not isinstance(saved["entries"], dict)):
            raise ValueError("invalid local inhibition snapshot")
        tau = positive_number(saved["tau"], "saved inhibition tau")
        result = {}
        for engram_id, entry in saved["entries"].items():
            if (not isinstance(engram_id, str) or not engram_id
                    or not isinstance(entry, dict) or set(entry) != {"level", "updated_at"}
                    or not isinstance(entry["updated_at"], str)):
                raise ValueError("invalid local inhibition entry")
            updated = _aware(datetime.fromisoformat(entry["updated_at"]))
            level = decay(entry["level"], updated, now, tau)
            target = self._current_target(engram_id)
            if target is not None and level >= EPSILON:
                # Downtime uses the recorded curve. New activity uses the
                # current engine policy. A backward clock cannot amplify state.
                at = max(now, updated)
                prior = result.get(target)
                if prior is not None:
                    # Ordinary snapshots have one entry per current target;
                    # reject ambiguous merged clock anchors rather than guess.
                    if prior[1] != at:
                        raise ValueError("inhibition successors have conflicting clock anchors")
                    level = positive_number(level + prior[0], "inhibition level", zero=True)
                result[target] = (level, at)
        if current_tau is not None and positive_number(current_tau, "current inhibition tau") != tau:
            # An explicitly different engine policy begins here, not retroactively
            # at the old impulse. Persist that boundary so repeated reopenings
            # cannot accidentally switch back to the earlier decay curve.
            self.save(result, current_tau, now)
        return result

    def save(self, entries: dict[str, tuple[float, datetime]], tau: float, now: datetime) -> None:
        if self.key is None:
            return
        tau = positive_number(tau, "inhibition tau")
        _aware(now)
        state = {"version": 1, "world_id": self.world_id, "tau": tau,
                 "entries": {eid: {"level": positive_number(level, "inhibition level", zero=True),
                                   "updated_at": _aware(at).isoformat()}
                             for eid, (level, at) in entries.items()}}
        if self.fence is None:
            self.storage.save_component_state(self.key, state)
        else:
            self.storage.save_component_state_fenced(
                self.key, state, runtime_owner_id=self.fence.owner_id,
                runtime_lease_epoch=self.fence.epoch, now=now)
