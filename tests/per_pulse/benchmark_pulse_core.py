"""Manual native-core scaling probe; no external model or network calls.

Run with PYTHONPATH=src python tests/per_pulse/benchmark_pulse_core.py.
The synthetic SQLite body is temporary. Timings are local measurements, not
service-level guarantees. This file is deliberately outside pytest discovery.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import cProfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import pstats
import random
import sqlite3
import statistics
import sys
import tempfile
import time

from pulse_system.core.causality.ledger import CausalLedger, RuntimeFence
from pulse_system.core.pulse.context import ContextPulseConfig, ContextPulseController
from pulse_system.core.world.registry import WorldRegistry
from pulse_system.substrate.storage.store import Storage


def measured(call, repeats):
    durations = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        durations.append((time.perf_counter() - start) * 1000)
    ordered = sorted(durations)
    return {"samples": len(durations), "median_ms": round(statistics.median(durations), 3),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
            "max_ms": round(max(durations), 3)}


def profile_text(call):
    profile = cProfile.Profile()
    profile.runcall(call)
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(14)
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contexts", type=int, default=100)
    parser.add_argument("--events", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--rate", type=float, default=0.0)
    args = parser.parse_args()
    if args.contexts < 3 or args.events < 2 or args.events % 2 or args.repeats < 1:
        parser.error("contexts >= 3; events must be a positive even count; repeats >= 1")
    if not 0 <= args.rate <= 1:
        parser.error("rate must be in [0, 1]")
    random.seed(42)
    root = Path(__file__).resolve().parents[2]
    hashes = {}
    for name in ("pulse/context.py", "connection/context.py", "causality/ledger.py"):
        path = root / "src" / "pulse_system" / "core" / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="per-pulse-scale-") as temp:
        started = time.perf_counter()
        storage = Storage(Path(temp) / "world.db")
        storage.save_component_state("pulse.world.v1", {
            "version": 1, "world_id": "scale-world", "continuity_engram_id": "scale-subject",
        })
        storage.create_engram(engram_id="scale-subject")
        world, ledger = WorldRegistry(storage), CausalLedger(storage)
        centers = [world.create_task_bundle_for_existing_engram(f"Synthetic context {index}", "scale-subject").center.id
                   for index in range(args.contexts)]
        lease = storage.acquire_runtime_lease("scale-owner", now=datetime.now(timezone.utc), ttl_sec=3600)

        def result(center, number):
            event = ledger.enqueue("scale-world", kind="stimulus", domain="pulse", source="user",
                                   content=f"Synthetic input {number}.", engram_id="scale-subject", center_id=center)
            turn = ledger.begin_turn(event.id, "scale-subject", event.content, execution_context=center)
            return ledger.settle_turn(turn.id, f"Synthetic result {number}.")[1]

        for number in range(args.events // 2):
            last = result(centers[number % len(centers)], number)
        controller = ContextPulseController(
            storage, ledger, world, world_id="scale-world", subject=lambda: "scale-subject",
            fence=lambda: RuntimeFence(lease.owner_id, lease.epoch),
            config=ContextPulseConfig(enabled=True, spontaneous_rate=0, refractory_seconds=0,
                                      max_background_pulses_per_hour=12),
        )
        for index, center in enumerate(centers):
            controller.connect(center, centers[(index + 1) % len(centers)], 0.6)
            controller.connect(center, centers[(index + 2) % len(centers)], 0.6)
        setup_seconds = time.perf_counter() - started
        start = time.perf_counter()
        replay_ticks = 0
        replay_max = 0.0
        while controller._state["cursor"] < last.seq:
            tick_start = time.perf_counter()
            controller.tick()
            replay_max = max(replay_max, (time.perf_counter() - tick_start) * 1000)
            replay_ticks += 1
        historical_replay = {"total_ms": round((time.perf_counter() - start) * 1000, 3),
                             "ticks": replay_ticks, "max_tick_ms": round(replay_max, 3)}
        # Load historical evidence without consuming the autonomous quota, then
        # measure a fresh candidate pass under the requested production rate.
        idle = measured(controller.tick, args.repeats)
        controller.config = replace(controller.config, spontaneous_rate=args.rate)
        now = datetime.now(timezone.utc)
        iteration = 0

        def eligibility():
            nonlocal iteration
            iteration += 1
            controller.tick(now=now + timedelta(seconds=11 * iteration))

        used_before_eligibility = controller._used(now)
        first_eligible = measured(eligibility, 1)
        used_after_first_eligibility = controller._used(now)
        eligible = measured(eligibility, args.repeats)
        used_after_eligibility = controller._used(now)
        snapshot = measured(controller.snapshot, args.repeats)
        trace = []
        storage._conn.set_trace_callback(trace.append)
        controller.snapshot()
        snapshot_queries = len(trace)
        trace.clear()
        eligibility()
        eligibility_queries = len(trace)
        storage._conn.set_trace_callback(None)
        snapshot_profile = profile_text(controller.snapshot)
        eligibility_profile = profile_text(eligibility)
        quota_query = measured(lambda: controller._used(datetime.now(timezone.utc)), args.repeats)
        source_proof_query = measured(lambda: controller._source_root(last), args.repeats)
        source_proof_plan = [tuple(row) for row in storage._conn.execute(
            "EXPLAIN QUERY PLAN SELECT event_id, engram_id, state, prompt_accepted "
            "FROM harness_turns WHERE result_event_id = ?", (last.id,),
        )]

        # A fresh result per context creates a bounded, quota-saturated fanout.
        for number, center in enumerate(centers):
            result(center, args.events // 2 + number)
        burst = measured(controller.tick, 1)
        saturated = measured(controller.tick, args.repeats)
        quota_plan = [tuple(row) for row in storage._conn.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM causal_events WHERE world_id = ? "
            "AND kind IN ('spontaneous', 'propagation') "
            "AND (json_extract(metadata, '$.context_pulse') = 'reentry' "
            "OR json_extract(metadata, '$.context_connection_id') IS NOT NULL) "
            "AND julianday(created_at) >= julianday(?)",
            ("scale-world", (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()),
        )]
        output = {"synthetic": True, "external_model_calls": 0,
                  "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                  "schema_version": storage._conn.execute("PRAGMA user_version").fetchone()[0],
                  "spontaneous_rate": args.rate,
                  "contexts": len(centers), "directed_edges": len(centers) * 2,
                  "historical_causal_events": args.events, "setup_seconds": round(setup_seconds, 3),
                  "controller_source_sha256": hashes,
                  "historical_replay": historical_replay, "idle_tick": idle,
                  "first_eligibility_tick": first_eligible,
                  "eligibility_tick": eligible, "snapshot": snapshot,
                  "eligibility_quota": {"limit": 12, "before": used_before_eligibility,
                                        "after_first": used_after_first_eligibility,
                                        "after_repeats": used_after_eligibility},
                  "fresh_100_context_burst": burst, "quota_saturated_tick": saturated,
                  "snapshot_sql_statements": snapshot_queries,
                  "eligibility_sql_statements": eligibility_queries,
                  "quota_query": quota_query, "source_proof_query": source_proof_query,
                  "quota_query_plan": quota_plan, "source_proof_query_plan": source_proof_plan,
                  "snapshot_profile": snapshot_profile, "eligibility_profile": eligibility_profile}
        storage.close()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
