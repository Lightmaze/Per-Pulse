"""Native replay browser fixture with explicitly scripted provider choices.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import uvicorn
from per_pulse.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "per_pulse"))
from replay_fixture import ReplayFixture


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    fixture = ReplayFixture(os.environ.__setitem__, hold=True)
    app = create_app(args.data_dir, posture=True, allow_workspace_actions=True,
                     model_factory=None if args.offline else fixture.factory)

    @app.post("/__replay/release")
    def release():
        fixture.release.set()
        return {"released": True}

    app.router.routes.insert(0, app.router.routes.pop())

    @app.post("/__replay/hold")
    def hold():
        fixture.release.clear()
        fixture.entered.clear()
        return {"held": True}

    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
