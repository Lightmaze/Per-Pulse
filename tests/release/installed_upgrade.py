"""Continue an actual offline World across two isolated wheel installations.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys

from installed_wheel_smoke import run_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "resume"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory.resolve() / "upgrade-world"
    receipt = args.directory.resolve() / "upgrade-receipt.json"
    installed_version = version("per-pulse")
    assert sys.prefix != sys.base_prefix, "Use the isolated installed wheel"
    if args.phase == "seed":
        assert installed_version == "0.1.0a39", installed_version
        assert not root.exists() and not receipt.exists()
        expected = run_workspace(root, example=False, fake_provider=False, exercise=True)
        assert (root / "world.db").is_file()
        run_workspace(root, example=False, fake_provider=False, expected=expected)
        receipt.write_text(json.dumps(expected), encoding="utf-8")
        print("Published a39: created and restarted sourced Home/Work history.")
    else:
        assert installed_version == "0.1.0a40", installed_version
        assert (root / "world.db").is_file()
        expected = json.loads(receipt.read_text(encoding="utf-8"))
        run_workspace(root, example=False, fake_provider=False, expected=expected)
        continued = run_workspace(
            root, example=False, fake_provider=False, expected=expected,
            continue_after_restore=True,
        )
        assert len(continued["homeMessageIds"]) == len(expected["homeMessageIds"]) + 1
        run_workspace(root, example=False, fake_provider=False, expected=continued)
        print(f"Installed {installed_version}: original World and Work continued, sourced return persisted after restart.")


if __name__ == "__main__":
    main()
