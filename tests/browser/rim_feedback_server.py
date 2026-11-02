"""Source-bound native RIM fixture for the browser consent/withdrawal journey.

The actual World, Kernel, SQLite, team tool, result journal and HTTP host run.
The model transport is imported from the explicitly synthetic native test;
no external provider is called or credited with learning.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import sys

import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PageService

# Reuse the exact synthetic model tool calls and native settlement loop from
# the RIM integration regression; do not build a second fake choice engine.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "per_pulse"))
from test_rim_native_integration import _model_factory, _tick, START, INSTRUCTION


def seed(data_dir: Path, model_factory):
    pages = PageService(data_dir, model_factory=model_factory, allow_workspace_actions=True)
    world = pages.runtime
    owner = world.continuity_engram_id
    source = world.world.create_task_bundle_for_existing_engram(
        "Original collaborator outcome", owner
    ).front
    baseline = world.world.create_task_bundle_for_existing_engram(
        "Before explicit sharing", owner
    ).front
    shared = world.world.create_task_bundle_for_existing_engram(
        "After explicit sharing", owner
    ).front
    withdrawn = world.world.create_task_bundle_for_existing_engram(
        "After withdrawal", owner
    ).front
    first = world.engrams.create(auto_name=False).id
    second = world.engrams.create(auto_name=False).id
    bad, good = sorted([first, second])
    for collaborator in (bad, good):
        world.storage.add_center_membership(source.center_id, collaborator)
        # The receiving Works need the same collaborators to make a real choice.
        for front in (baseline, shared, withdrawn):
            world.storage.add_center_membership(front.center_id, collaborator)
    try:
        for collaborator, outcome in ((bad, "discarded"), (good, "adopted")):
            record_id = world.delegate(
                INSTRUCTION, to=collaborator, caller_id=owner,
                center_id=source.center_id,
                idempotency_key="browser-prior-" + collaborator,
            )
            _tick(world, lambda record_id=record_id:
                  world.storage.get_delegation(record_id)["result_summary"] is not None
                  and world.causal_ledger.find_delegation_result_by_record_id(
                      world.world_id, record_id) is not None
                  and not world.harness._active, seconds=20)

        # Native RIM first selects and executes an actual adaptive team action
        # in the original Work. The UI can edit only its completed result.
        world.send_task_front_message(source.id, START)

        def original_delivered():
            rows = [row for row in world.storage.native_team_requests(limit=64)
                    if row["work_id"] == source.id and row["status"] == "delivered"]
            if len(rows) != 1:
                return False
            record = world.storage.get_delegation(rows[0]["record_id"])
            return bool(record and record["result_summary"] is not None
                        and world.causal_ledger.find_delegation_result_by_record_id(
                            world.world_id, record["id"]) is not None)

        _tick(world, original_delivered, seconds=25)
        choice = pages.adaptive_choices(source.id)["choices"]
        assert len(choice) == 1 and choice[0]["resultMessageId"], choice
        rated = pages.rate_adaptive_choice(source.id, choice[0]["recordId"], "adopted")
        assert rated["outcome"] == "adopted"
        return {"source": source.id, "baseline": baseline.id,
                "shared": shared.id, "withdrawn": withdrawn.id,
                "record": choice[0]["recordId"], "good": good, "bad": bad}
    finally:
        # Release the ownership lease before the real HTTP host opens the same
        # World. A second in-process World must never become authoritative.
        world.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    os.environ["P_PULSE_RIM_POLICY"] = "1"
    os.environ["PER_PULSE_RIM_TEST_KEY"] = "EXPLICIT_TEST_ONLY"
    random.SystemRandom.random = lambda self: 0.5  # deterministic fixture choice
    state, seen = {}, []
    model_factory = _model_factory(state, seen)

    # The test-only synthetic responder needs the same fixed collaborators
    # across the seeding session and the resumed HTTP service.
    data_dir = Path(args.data_dir)
    metadata = seed(data_dir, model_factory)
    state["bad"], state["good"] = metadata["bad"], metadata["good"]
    app = create_app(data_dir, allow_workspace_actions=True, model_factory=model_factory)

    @app.get("/__rim/fixture")
    def fixture():
        return {"works": metadata, "provider": "explicit synthetic HTTP transport",
                "modelRequests": len(seen)}

    route = app.router.routes.pop()
    if getattr(route, "path", None) == "/__rim/fixture":
        insert = next(i for i, item in enumerate(app.router.routes)
                      if getattr(item, "path", None) == "/{route:path}")
        app.router.routes.insert(insert, route)
    else:
        app.router.routes.append(route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
