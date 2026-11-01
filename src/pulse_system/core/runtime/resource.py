"""Runtime resource allocator.

Manages API call budgets, cost tracking, and global stability monitoring.
Does NOT make cognitive decisions (the non-cognitive infrastructure boundary).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StabilityAdvice(str, Enum):
    NORMAL = "normal"
    REDUCE_SPONTANEOUS = "reduce_spontaneous"
    INCREASE_SPONTANEOUS = "increase_spontaneous"


@dataclass
class RuntimeConfig:
    budget_per_tick: int = 5
    hourly_token_budget: int = 100_000
    daily_token_budget: int = 2_000_000
    heartbeat_high: float = 0.7   # n/N above this → runaway excitation
    heartbeat_low: float = 0.05   # n/N below this → too quiet
    # Price ratio of a cached input token vs. an uncached one — should
    # mirror the LLM provider's cache contract (DeepSeek ≈ 0.1,
    # OpenAI ≈ 0.5, Anthropic reads ≈ 0.1). See LLMAdapter.cache_read_discount.
    cache_read_discount: float = 0.1
    # Extra cost multiplier for cache *writes* on providers with explicit
    # breakpoints: Anthropic bills cache_creation tokens at 1.25x for the
    # 5-minute TTL, i.e. a 0.25 premium on top of their nominal input price.
    # Auto-caching providers (DeepSeek/OpenAI) have no write premium.
    cache_write_premium: float = 0.25


@dataclass
class RuntimeStats:
    total_pulses: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_input_tokens: int = 0
    total_cache_hits: int = 0
    total_cache_misses: int = 0
    tokens_this_hour: int = 0   # billable-equivalent tokens (cache-discounted)
    tokens_today: int = 0       # billable-equivalent tokens (cache-discounted)
    hour_start: datetime = field(default_factory=_now)
    day_start: datetime = field(default_factory=_now)


class RuntimeManager:
    """Resource allocator — budget, cost tracking, global stability."""

    def __init__(self, config: RuntimeConfig | None = None):
        self._config = config or RuntimeConfig()
        self._stats = RuntimeStats()
        # Pulse turns, succession workers and coordinator observations share
        # one in-process allocator. Keep counters and reads in one snapshot.
        # This is NOT an across-process reservation or a provider billing cap.
        self._lock = threading.RLock()

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    def get_budget(
        self, *, unsettled_provider_usage: tuple[int, int, int] | None = (0, 0, 0),
    ) -> int:
        """Observe one coherent in-process budget before Pulse admission.

        This does not reserve provider spend or recover lost process state.
        """
        with self._lock:
            return self._get_budget_unlocked(
                unsettled_provider_usage=unsettled_provider_usage,
            )

    def _get_budget_unlocked(
        self, *, unsettled_provider_usage: tuple[int, int, int] | None = (0, 0, 0),
    ) -> int:
        """Admit against settled spend *and* source-verified attached spend.

        The optional (input, output, cache-write) triple is a conservative
        upper bound for already completed provider calls on still-attached
        native Kernel turns. Cached reads count at their full input price.
        It is NOT charged to stats here: the original causal Future charges
        once at final settlement. None means unavailable/unknown evidence and
        denies new admission rather than treating unknown spend as zero.
        """
        if unsettled_provider_usage is None:
            return 0
        if (
            not isinstance(unsettled_provider_usage, tuple)
            or len(unsettled_provider_usage) != 3
            or any(type(value) is not int or value < 0 for value in unsettled_provider_usage)
            or unsettled_provider_usage[2] > unsettled_provider_usage[0]
        ):
            raise ValueError("unsettled_provider_usage must be non-negative (input, output, cache-write)")
        pending_input, pending_output, pending_writes = unsettled_provider_usage
        pending_upper = (
            pending_input + pending_output
            + math.ceil(pending_writes * max(0.0, self._config.cache_write_premium))
        )
        self._maybe_reset_windows()
        hourly_remaining = self._config.hourly_token_budget - self._stats.tokens_this_hour
        daily_remaining = self._config.daily_token_budget - self._stats.tokens_today
        remaining = min(hourly_remaining, daily_remaining) - pending_upper
        if remaining <= 0:
            return 0
        if self._stats.total_pulses > 0:
            billed = self._stats.tokens_this_hour + self._stats.tokens_today
            # Rough per-pulse average over both windows (each pulse counted twice).
            avg_per_pulse = max(1, billed // (2 * self._stats.total_pulses))
            allowed = remaining // avg_per_pulse
            return max(0, min(self._config.budget_per_tick, allowed))
        return self._config.budget_per_tick

    def consume_budget(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """Atomically settle one observed Pulse usage increment in-process."""
        with self._lock:
            self._consume_budget_unlocked(
                input_tokens, output_tokens,
                cached_input_tokens, cache_write_tokens,
            )

    def _consume_budget_unlocked(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """Record consumption from one pulse (per-call increments, not totals).

        Budget windows accumulate billable-equivalent tokens: cached input
        tokens count at `cache_read_discount` of their nominal size, and
        cache writes add `cache_write_premium` on top of their nominal cost
        (they are already part of input_tokens), per the provider's contract.
        """
        # Settlement may be the first allocator interaction after an hour/day
        # boundary. Roll the windows *before* charging it; otherwise the next
        # admission read resets this just-observed provider usage to zero.
        self._maybe_reset_windows()
        cached = min(max(cached_input_tokens, 0), input_tokens)
        writes = min(max(cache_write_tokens, 0), input_tokens - cached)
        billable = (
            output_tokens
            + (input_tokens - cached)
            + int(cached * self._config.cache_read_discount)
            + int(writes * self._config.cache_write_premium)
        )
        self._stats.total_pulses += 1
        self._stats.total_input_tokens += input_tokens
        self._stats.total_output_tokens += output_tokens
        self._stats.total_cached_input_tokens += cached
        self._stats.tokens_this_hour += billable
        self._stats.tokens_today += billable
        if cached > 0:
            self._stats.total_cache_hits += 1
        else:
            self._stats.total_cache_misses += 1

    def check_global_stability(
        self, active_count: int, total_count: int
    ) -> StabilityAdvice:
        """Check heartbeat n/N and return stability advice."""
        if total_count == 0:
            return StabilityAdvice.NORMAL
        heartbeat = active_count / total_count
        if heartbeat > self._config.heartbeat_high:
            return StabilityAdvice.REDUCE_SPONTANEOUS
        if heartbeat < self._config.heartbeat_low:
            return StabilityAdvice.INCREASE_SPONTANEOUS
        return StabilityAdvice.NORMAL

    def get_stats(self) -> RuntimeStats:
        return self._stats

    def snapshot(self) -> dict:
        """Read counters and remaining allowances from one coherent instant."""
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict:
        """Point-in-time view of budget and consumption, for CLI/monitoring."""
        self._maybe_reset_windows()
        s = self._stats
        return {
            "total_pulses": s.total_pulses,
            "total_input_tokens": s.total_input_tokens,
            "total_output_tokens": s.total_output_tokens,
            "total_cached_input_tokens": s.total_cached_input_tokens,
            "cache_hit_calls": s.total_cache_hits,
            "cache_miss_calls": s.total_cache_misses,
            "billable_tokens_this_hour": s.tokens_this_hour,
            "billable_tokens_today": s.tokens_today,
            "hourly_budget_remaining": max(
                0, self._config.hourly_token_budget - s.tokens_this_hour
            ),
            "daily_budget_remaining": max(
                0, self._config.daily_token_budget - s.tokens_today
            ),
        }

    def _maybe_reset_windows(self) -> None:
        now = _now()
        if (now - self._stats.hour_start).total_seconds() >= 3600:
            self._stats.tokens_this_hour = 0
            self._stats.hour_start = now
        if (now - self._stats.day_start).total_seconds() >= 86400:
            self._stats.tokens_today = 0
            self._stats.day_start = now
