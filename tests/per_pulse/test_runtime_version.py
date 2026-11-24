"""The running API must identify the release that was actually packaged.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from pathlib import Path
import tomllib

from per_pulse import __version__


def test_runtime_version_matches_distribution_project_version():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == project["project"]["version"]
