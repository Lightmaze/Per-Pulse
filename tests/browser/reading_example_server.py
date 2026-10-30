"""Native reading-to-example browser fixture, with explicit synthetic choices."""
import argparse
import os
from pathlib import Path
import sys

import uvicorn
from per_pulse.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "per_pulse"))
from reading_example_fixture import ReadingExampleFixture

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    fixture = ReadingExampleFixture(os.environ.__setitem__)
    app = create_app(args.data_dir, allow_workspace_actions=True, posture=True,
                     model_factory=None if args.offline else fixture.factory)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
