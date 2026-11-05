"""Check the actual release archives and write their SHA-256 checksums.

Run after building the client and distributions. Uses only the standard library.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
import zipfile


def check_paths(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts, name
        assert not any(
            part in {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
            or part.startswith(".env")
            for part in path.parts
        ), f"Local/private file in distribution: {name}"
        assert not name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
                                  ".jsonl", ".pyc", ".pem", ".key", ".p12")), name


def check_client(read, names: set[str], prefix: str) -> None:
    html = read(prefix + "index.html").decode("utf-8")
    assert '<div id="root"></div>' in html, "Missing client entry point"
    assets = re.findall(r'(?:src|href)="(/assets/[^"?#]+)"', html)
    assert any(asset.endswith(".js") for asset in assets), "Missing compiled JavaScript"
    assert any(asset.endswith(".css") for asset in assets), "Missing compiled CSS"
    for asset in assets:
        name = prefix + asset.lstrip("/")
        assert name in names and len(read(name)) > 100, f"Missing/empty client asset: {name}"
    notices = read(prefix + "THIRD_PARTY_NOTICES.txt")
    assert b"react-markdown " in notices and b"remark-gfm " in notices
    assert b"Permission is hereby granted" in notices, "Missing bundled licenses"


def verify(directory: Path, version: str) -> list[Path]:
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Expected one wheel and one source archive"
    wheel, source = wheels[0], sources[0]
    assert wheel.name == f"per_pulse-{version}-py3-none-any.whl", wheel.name
    assert source.name == f"per_pulse-{version}.tar.gz", source.name
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        check_paths(list(names))
        for package in ("per_pulse", "pulse_system", "pulse_harness"):
            assert f"{package}/__init__.py" in names, f"Missing package: {package}"
        assert "pulse_harness/core/secrets.py" in names, "Missing runtime module"
        assert not any(name.startswith(("research/", "tests/", "per_pulse_research/")) for name in names)
        metadata = BytesParser().parsebytes(archive.read(f"per_pulse-{version}.dist-info/METADATA"))
        assert metadata["Name"] == "per-pulse" and metadata["Version"] == version
        for license_name in ("LICENSE", "NOTICE"):
            assert any(name.endswith("/licenses/" + license_name) for name in names), license_name
        check_client(archive.read, names, "per_pulse/static/")
    with tarfile.open(source, "r:gz") as archive:
        names = set(archive.getnames())
        check_paths(list(names))
        assert all(member.isfile() or member.isdir() for member in archive.getmembers()), "Archive has links/devices"
        prefix = f"per_pulse-{version}/"
        assert all(name.startswith(prefix) for name in names), "Unexpected source archive root"
        def read(name: str) -> bytes:
            handle = archive.extractfile(name)
            assert handle is not None, name
            return handle.read()
        for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "SECURITY.md",
                     "CHANGELOG.md", "CONTRIBUTING.md", f"docs/releases/v{version}.md"):
            assert prefix + name in names, f"Missing source documentation: {name}"
        source_config = tomllib.loads(read(prefix + "pyproject.toml").decode("utf-8"))
        assert source_config["project"]["version"] == version
        check_client(read, names, prefix + "web/dist/")
    files = [wheel, source]
    checksums = []
    for path in files:
        with path.open("rb") as handle:
            checksums.append(f"{hashlib.file_digest(handle, 'sha256').hexdigest()}  {path.name}\n")
    (directory / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args()
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = config["project"]["version"]
    for path in verify(args.directory, version):
        print(f"Verified {path.name} ({path.stat().st_size:,} bytes)")
    print("Wrote SHA256SUMS")
