"""Real command pipes and native example persistence; model/control are fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import argparse
import json
import os
from pathlib import Path
import sys

import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_system.service import kernel_runtime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "per_pulse"))
from command_example_fixture import CommandExampleFixture
from command_output_fixture import LocalPipeCommandExecutor


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    count_file = args.data_dir / "explicit-command-count.json"
    counts = json.loads(count_file.read_text()) if count_file.exists() else {"commands": 0}

    class BrowserPipeExecutor(LocalPipeCommandExecutor):
        def execute(self, *call_args, **kwargs):
            counts["commands"] += 1
            count_file.write_text(json.dumps(counts))
            return super().execute(*call_args, **kwargs)

    fixture = CommandExampleFixture(os.environ.__setitem__)
    if args.offline:
        os.environ.pop("P_PULSE_COMMAND_IMAGE", None)
    else:
        os.environ["P_PULSE_COMMAND_IMAGE"] = "explicit-pipe-fixture"
        kernel_runtime.DockerCommandExecutor = BrowserPipeExecutor
    app = create_app(args.data_dir, allow_workspace_actions=not args.offline, posture=True,
                     model_factory=None if args.offline else fixture.factory, pulse=PulseSettings(enabled=False))

    @app.get("/__command_examples/evidence")
    def evidence():
        return {**counts, "requests": len(fixture.requests),
                "before_action": fixture.before_action}

    route = app.router.routes.pop()
    app.router.routes.insert(next(i for i, item in enumerate(app.router.routes)
                                 if getattr(item, "path", None) == "/{route:path}"), route)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
