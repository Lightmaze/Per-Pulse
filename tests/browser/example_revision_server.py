"""Quiet interpretation revision over the actual World and native Kernel.

Model choices are an explicit synthetic HTTP fixture.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import argparse
import os
from pathlib import Path
import sys

import uvicorn
from per_pulse.app import create_app
from per_pulse.service import PulseSettings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "per_pulse"))
from example_revision_fixture import ExampleRevisionFixture


class BrowserRevisionFixture(ExampleRevisionFixture):
    def handler(self, request):
        response = super().handler(request)
        if self.quiet is None and self.prepared and self.prepared.get("output", {}).get("status") == "prepared":
            self.quiet = {"op": "revise", "experience_id": self.prepared["output"]["experience_id"],
                          "expected_revision": 1, "changes": {
                              "title": "Compare complete source characters",
                              "note": "Map whole original characters before comparing offsets in a folded string."}}
        return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    fixture = BrowserRevisionFixture(os.environ.__setitem__)
    app = create_app(args.data_dir, posture=True, model_factory=None if args.offline else fixture.factory,
                     pulse=PulseSettings(enabled=False))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
