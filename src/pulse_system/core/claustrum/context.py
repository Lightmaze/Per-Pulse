"""Apply the existing spectrum learner to actual Home/Work contexts.

Only numeric state enters this adapter. It neither creates Engrams nor calls a
model. Its slots, field weights and held credit live in the original World DB.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime
import math

import numpy as np

from .modulator import ClaustrumConfig, ClaustrumModulator, EngramState


class ContextSpectrum(ClaustrumModulator):
    """The same MLP and credit rule, with stable context IDs as slot keys."""

    def __init__(self, storage, world_id: str, *, config=None, learning_policy=None,
                 learning_audit=None):
        self.COMPONENT = "pulse.context-spectrum.v1:" + world_id
        self.last_sample_at: datetime | None = None
        self._requested_ids: set[str] = set()
        self.tide = {"active": 0, "total": 0, "ratio": 0.0}
        super().__init__(storage, config=config or ClaustrumConfig(
            hidden_dim=64, noise_hot=0.1, noise_cold=0.02,
            hold_ticks=4, learn_interval=8, buffer_cap=64,
        ), learning_policy=learning_policy, learning_audit=learning_audit)
        state = storage.load_weight_state(self.COMPONENT, "field")
        runtime = state.get("context_runtime") if state else None
        if runtime is not None:
            self._restore_runtime(runtime)

    def update(self, states: list[EngramState], *, now: datetime,
               active: int, interval: float) -> None:
        keys = {state.engram_id for state in states}
        prior = self.last_sample_at
        if prior is not None and keys == self._requested_ids:
            elapsed = (now - prior).total_seconds()
            if elapsed < 0:
                self.last_sample_at = now
                self._save_state()
                return
            if elapsed < interval:
                return
        # Releasing a context resets its numeric slot before another can use
        # it. A committed Engram succession leaves all context IDs unchanged.
        for key in set(self._slots.live_ids()) - keys:
            self.mask_engram(key)
        self.tide = {"active": active, "total": len(states),
                     "ratio": active / len(states) if states else 0.0}
        self.last_sample_at = now
        self._requested_ids = keys
        self.modulate(states)
        self.observe_mind_tide(self.tide["ratio"])
        # Persist the sample, its random state, credit and weights together.
        # A polling retry/restart is not a new hold or a free learning step.
        self._save_state()

    def factors(self, context_key: str) -> tuple[float, float, float, float]:
        return self._held_factors.get(context_key, (1.0, 1.0, 1.0, 1.0))

    def snapshot(self) -> dict:
        return {"lastUpdatedAt": self.last_sample_at.isoformat() if self.last_sample_at else None,
                "tide": dict(self.tide), "observations": self._observations,
                "capacity": self.capacity_snapshot(),
                "learning": self.learning_audit_snapshot(),
                "factors": {key: list(value) for key, value in self._held_factors.items()}}

    @staticmethod
    def _pack_sample(sample):
        return [value.tolist() if isinstance(value, np.ndarray) else value for value in sample]

    def _field_state(self) -> dict:
        state = super()._field_state()
        state["context_runtime"] = {
            "version": 1,
            "last_sample_at": self.last_sample_at.isoformat() if self.last_sample_at else None,
            "requested_ids": sorted(self._requested_ids),
            "tide": self.tide, "rng": self._rng.bit_generator.state,
            "held_factors": self._held_factors, "hold_left": self._hold_left,
            "ticks_into_hold": getattr(self, "_ticks_into_hold", 0),
            "ratio_acc": self._ratio_acc, "coherent_acc": self._coherent_acc,
            "breadth_acc": self._breadth_acc,
            "pending": self._pack_sample(self._pending) if self._pending is not None else None,
            "buffer": [self._pack_sample(sample) for sample in self._buffer],
        }
        return state

    def _restore_runtime(self, state: dict) -> None:
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("invalid context spectrum checkpoint")
        stamp = state["last_sample_at"]
        self.last_sample_at = datetime.fromisoformat(stamp) if stamp else None
        if self.last_sample_at is not None and self.last_sample_at.utcoffset() is None:
            raise ValueError("context spectrum time must be timezone-aware")
        factors = state["held_factors"]
        low, high = math.exp(-self._config.mod_log_range), math.exp(self._config.mod_log_range)
        if (not isinstance(factors, dict) or len(factors) > self._config.max_slots
                or any(not isinstance(key, str) or len(value) != 4
                       or any(type(v) not in (float, int) or not math.isfinite(v)
                              or not low <= v <= high for v in value)
                       for key, value in factors.items())):
            raise ValueError("invalid context spectrum factors")
        self._held_factors = {key: tuple(value) for key, value in factors.items()}
        requested = state.get("requested_ids", list(factors))
        if (not isinstance(requested, list) or any(not isinstance(key, str) for key in requested)
                or len(set(requested)) != len(requested) or not set(factors).issubset(requested)):
            raise ValueError("invalid context spectrum population")
        self._requested_ids = set(requested)
        self._last_requested = len(requested)
        self._last_overflow = max(0, len(requested) - len(factors))
        self._hold_left = state["hold_left"]
        self._ticks_into_hold = state["ticks_into_hold"]
        if any(type(v) is not int or v < 0 for v in (self._hold_left, self._ticks_into_hold)):
            raise ValueError("invalid context spectrum hold")
        self.tide = state["tide"]
        self._ratio_acc = list(state["ratio_acc"])
        self._coherent_acc = list(state["coherent_acc"])
        self._breadth_acc = list(state["breadth_acc"])
        self._rng.bit_generator.state = state["rng"]

        def array(value, size, *, mask=False):
            result = np.asarray(value, dtype=bool if mask else float)
            if result.shape != (size,) or (not mask and not np.all(np.isfinite(result))):
                raise ValueError("invalid context spectrum sample")
            return result

        def sample(values, *, pending=False):
            if len(values) != (4 if pending else 6):
                raise ValueError("invalid context spectrum sample")
            x = array(values[0], self._mlp.input_dim)
            applied = array(values[1], self._mlp.output_dim)
            if pending:
                return x, applied, array(values[2], self._mlp.output_dim), array(values[3], self._mlp.output_dim, mask=True)
            reward, demand = float(values[2]), float(values[4])
            if not math.isfinite(reward) or not 0 <= demand <= 1:
                raise ValueError("invalid context spectrum credit")
            return x, applied, reward, array(values[3], self._mlp.output_dim), demand, array(values[5], self._mlp.output_dim, mask=True)

        self._pending = sample(state["pending"], pending=True) if state["pending"] is not None else None
        if len(state["buffer"]) > self._config.buffer_cap:
            raise ValueError("invalid context spectrum buffer")
        self._buffer = [sample(values) for values in state["buffer"]]
