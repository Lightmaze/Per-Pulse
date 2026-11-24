"""Browser fixture: synthetic transport, production autonomous World lifecycle.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
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
    parser.add_argument("--purpose-revision", action="store_true")
    parser.add_argument("--learned-revision", action="store_true")
    args = parser.parse_args()
    fixture = AutonomousPracticeFixture(os.environ.__setitem__, purpose_revision=args.purpose_revision,
                                        learned_revision=args.learned_revision)
    pulse = (PulseSettings(enabled=True, spontaneous_rate=0, refractory_seconds=0)
             if args.purpose_revision else PulseSettings(enabled=True, spontaneous_rate=0))
    app = create_app(args.data_dir, pulse=pulse,
                     posture=not args.purpose_revision or args.learned_revision, allow_workspace_actions=True, auto_apply_edits=True,
                     model_factory=fixture.factory)

    @app.get("/__autonomous/status")
    def status():
        runtime = app.state.pages.runtime
        roots = runtime.causal_ledger.list_events(world_id=runtime.world_id, limit=100)
        resume_inputs = []
        with fixture.lock:
            requests = list(fixture.requests)
        for request in requests:
            messages = json.loads(request["material"])
            if any(item["role"] == "system" and
                   "Internal provider continuation, not a user message." in item["content"]
                   for item in messages):
                latest = next((item["content"] for item in reversed(messages) if item["role"] == "user"), None)
                resume_inputs.append({"context": request["context"], "latestUser": latest})
        return {"requests": len(requests), "resumeInputs": resume_inputs,
                "continuations": [{"id": event.id, "status": event.status.value}
                                  for event in roots if event.metadata.get("context_pulse") == "provider_continuation"],
                "departures": [{"id": event.id, "source": event.source.value,
                                 "status": event.status.value, "parent": event.parent_event_id}
                                for event in roots if event.metadata.get("context_pulse") == "first_departure"],
                "userRoots": [event.id for event in roots if event.source.value == "user" and event.parent_event_id is None]}

    route = app.router.routes.pop()
    app.router.routes.insert(next(i for i, value in enumerate(app.router.routes)
                                if getattr(value, "path", None) == "/{route:path}"), route)
    if args.purpose_revision:
        from test_weekly_host import _enqueue_self

        @app.post("/__autonomous/reentry/{work_id}")
        def reentry(work_id: str):
            # Authored SELF activation for the browser fixture only; the normal
            # scheduler, provider gate and continuation execute it.
            runtime = app.state.pages.runtime
            center = runtime.world.get_task_front(work_id).center_id
            event_id = _enqueue_self(runtime, "WRITE_RESUME", "browser-purpose-reentry", center_id=center)
            return {"eventId": event_id}

        route = app.router.routes.pop()
        app.router.routes.insert(next(i for i, value in enumerate(app.router.routes)
                                    if getattr(value, "path", None) == "/{route:path}"), route)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
