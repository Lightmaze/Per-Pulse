"""Synthetic model/website HTTP; real native autonomous lifecycle and files.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import argparse
from pathlib import Path
import sys

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).parents[1] / "per_pulse"))
from web_read_fixture import WebFixture
from per_pulse.app import create_app
from per_pulse.service import PulseSettings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    if args.installed:
        assert sys.prefix != sys.base_prefix, "installed browser checks require a virtual environment"
        for name in ("per_pulse", "pulse_harness", "pulse_system"):
            origin = Path(__import__(name).__file__).resolve()
            assert origin.is_relative_to(Path(sys.prefix).resolve()) and "site-packages" in origin.parts, origin
    fixture = WebFixture(pytest.MonkeyPatch())
    fixture.body += "<p>" + "Further original field observations. " * 1000 + "</p>"
    app = create_app(args.data_dir, model_factory=fixture.factory, posture=True,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=True, spontaneous_rate=0))

    @app.get("/__web/status")
    def status():
        return {"requests": len(fixture.requests), "webRequests": len(fixture.web_requests), "installed": args.installed}
    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
