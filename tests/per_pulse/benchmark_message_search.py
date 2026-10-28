"""Optional dense native search probe; synthetic data, no CI timing assertion.

Run: python tests/per_pulse/benchmark_message_search.py --rows 100000
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import argparse
from datetime import datetime, timezone
import json
import statistics
import tempfile
import time

from per_pulse.service import PageService
from pulse_system.core.types import CausalEvent, CausalEventDomain, CausalEventKind, CausalEventSource


def benchmark(rows):
    with tempfile.TemporaryDirectory(prefix="per-pulse-search-scale-") as directory:
        pages = PageService(directory, model_factory=None)
        try:
            work = pages.create_page("Dense search fixture", "An authored starting note.")["page"]["id"]
            sibling = pages.create_page("Separate context", "A separate authored note.")["page"]["id"]
            runtime = pages.runtime
            center = runtime.world.get_task_front(work).center_id
            sibling_center = runtime.world.get_task_front(sibling).center_id
            at = datetime.now(timezone.utc)
            with runtime.causal_ledger._transaction() as connection:
                for index in range(rows):
                    event = CausalEvent(
                        world_id=runtime.world_id, domain=CausalEventDomain.PULSE,
                        kind=CausalEventKind.STIMULUS, source=CausalEventSource.USER,
                        engram_id=runtime.continuity_engram_id,
                        center_id=sibling_center if index % 5 == 4 else center,
                        content=("An old unique Straße observation." if index == 0 else
                                 f"Authored dense search observation {index}. " + "Retain original evidence. " * 4),
                        created_at=at, updated_at=at,
                    )
                    runtime.causal_ledger._insert_event_uncommitted(connection, event)
            measurements = {}
            for label, query in (("common", "dense search"), ("old_unique", "STRASSE"), ("absent", "missing-search-value")):
                durations = []
                for sample in range(8):
                    started = time.perf_counter()
                    result = pages.search_messages(work, query)
                    if sample >= 2:
                        durations.append((time.perf_counter() - started) * 1000)
                assert len(result["messages"]) == {"common": 100, "old_unique": 1, "absent": 0}[label]
                measurements[label] = {"median_ms": statistics.median(durations), "max_ms": max(durations)}
            return {"rows": rows, "selected_context_rows": rows - rows // 5 + 1,
                    "provider": "none; authored native records", "samples": 6, "warmup": 2,
                    "queries": measurements}
        finally:
            pages.runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    args = parser.parse_args()
    if not 2000 <= args.rows <= 1000000:
        parser.error("--rows must be between 2000 and 1000000")
    print(json.dumps(benchmark(args.rows), indent=2))
