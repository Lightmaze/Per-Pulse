"""Browser fixture: synthetic transport, production autonomous World lifecycle.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import uvicorn

sys.path.insert(0, str(Path(__file__).parents[1] / "per_pulse"))
from autonomous_practice_fixture import AutonomousPracticeFixture
from per_pulse.app import create_app
from per_pulse.service import PulseSettings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    fixture = AutonomousPracticeFixture(os.environ.__setitem__)
    app = create_app(args.data_dir, pulse=PulseSettings(enabled=True, spontaneous_rate=0),
                     posture=True, allow_workspace_actions=True, auto_apply_edits=True,
                     model_factory=fixture.factory)

    @app.get("/__autonomous/status")
    def status():
        runtime = app.state.pages.runtime
        roots = runtime.causal_ledger.list_events(world_id=runtime.world_id, limit=100)
        return {"requests": len(fixture.requests),
                "departures": [{"id": event.id, "source": event.source.value,
                                 "status": event.status.value, "parent": event.parent_event_id}
                                for event in roots if event.metadata.get("context_pulse") == "first_departure"],
                "userRoots": [event.id for event in roots if event.source.value == "user" and event.parent_event_id is None]}

    route = app.router.routes.pop()
    app.router.routes.insert(next(i for i, value in enumerate(app.router.routes)
                                if getattr(value, "path", None) == "/{route:path}"), route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
