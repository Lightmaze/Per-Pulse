"""Optional scale probe for native page reads; not a CI timing assertion.

Run from the repository root: python tests/per_pulse/benchmark_message_history.py
All records are explicit synthetic inputs in a disposable local database.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from datetime import datetime, timezone

from per_pulse.service import PageService
from pulse_system.core.types import CausalEvent, CausalEventDomain, CausalEventKind, CausalEventSource


def measure(pages, work_id, cursor):
    measurements = {}
    operations = {
        "recent_view": lambda: pages.view(work_id),
        "older_range": lambda: pages.history(work_id, before_message_id=cursor),
        "bootstrap": pages.bootstrap,
    }
    for name, operation in operations.items():
        values = []
        for index in range(25):
            start = time.perf_counter()
            operation()
            if index >= 5:
                values.append((time.perf_counter() - start) * 1000)
        measurements[name] = {"median_ms": statistics.median(values), "max_ms": max(values)}
    return measurements


def benchmark(rows):
    with tempfile.TemporaryDirectory(prefix="per-pulse-message-scale-") as directory:
        pages = PageService(directory, model_factory=None)
        runtime = pages.runtime
        try:
            work = pages.create_page("Sparse Work", "Explicit scale fixture")
            sibling = pages.create_page("Busy sibling", "Explicit scale fixture")
            work_id = work["page"]["id"]
            center = runtime.world.get_task_front(work_id).center_id
            sibling_center = runtime.world.get_task_front(sibling["page"]["id"]).center_id
            ids = []
            at = datetime.now(timezone.utc)
            with runtime.causal_ledger._transaction() as connection:
                for index in range(rows):
                    selected = center if index % 100 == 0 else sibling_center
                    event = CausalEvent(
                        world_id=runtime.world_id, domain=CausalEventDomain.PULSE,
                        kind=CausalEventKind.STIMULUS, source=CausalEventSource.USER,
                        engram_id=runtime.continuity_engram_id, center_id=selected,
                        content=f"Authored scale fixture {index}", created_at=at, updated_at=at,
                    )
                    runtime.causal_ledger._insert_event_uncommitted(connection, event)
                    if selected == center:
                        ids.append(event.id)
            cursor = ids[len(ids) // 2]
            # Only this disposable fixture loses the index temporarily. No
            # migration ledger or causal record is rewritten for comparison.
            with runtime.storage._lock:
                runtime.storage._conn.execute("DROP INDEX idx_causal_events_world_center_seq")
                runtime.storage._conn.commit()
            before = measure(pages, work_id, cursor)
            with runtime.storage._lock:
                runtime.storage._conn.execute(
                    "CREATE INDEX idx_causal_events_world_center_seq ON causal_events(world_id, center_id, seq)",
                )
                runtime.storage._conn.commit()
            after = measure(pages, work_id, cursor)
            return {"rows": rows, "provider": "none; synthetic native ledger", "without_context_index": before,
                    "with_context_index": after, "samples": 20, "warmup": 5}
        finally:
            runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    args = parser.parse_args()
    if not 2000 <= args.rows <= 1000000:
        parser.error("--rows must be between 2000 and 1000000")
    print(json.dumps(benchmark(args.rows), indent=2))
