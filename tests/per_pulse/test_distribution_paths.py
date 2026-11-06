"""Release archives admit only the exact public test TLS fixture in source.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import importlib.util
import io
from pathlib import Path
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("distribution_verifier", ROOT / "scripts/verify_distribution.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
PREFIX = "per_pulse-0.1.0a3/"
FIXTURES = {PREFIX + "tests/fixtures/web_tls/" + name:
            (ROOT / "tests/fixtures/web_tls" / name).read_bytes() for name in ("cert.pem", "key.pem")}


def test_source_archive_admits_exact_public_synthetic_tls_bytes():
    verifier.check_source_paths(FIXTURES.__getitem__, set(FIXTURES) | {PREFIX + "src/per_pulse/app.py"}, PREFIX)


@pytest.mark.parametrize("missing", [("cert.pem",), ("key.pem",), ("cert.pem", "key.pem")])
def test_source_archive_requires_both_public_tls_fixtures(missing):
    remaining = {name: data for name, data in FIXTURES.items()
                 if name.rsplit("/", 1)[-1] not in missing}
    with pytest.raises(AssertionError, match="Missing public TLS fixture"):
        verifier.check_source_paths(remaining.__getitem__, set(remaining), PREFIX)


@pytest.mark.parametrize("name", ["cert.pem", "key.pem", "private.key", "private.p12"])
def test_default_archive_policy_still_rejects_every_certificate_or_key(name):
    with pytest.raises(AssertionError):
        verifier.check_paths([PREFIX + "tests/fixtures/web_tls/" + name])


@pytest.mark.parametrize("name", ["cert.pem", "key.pem"])
def test_source_archive_rejects_one_changed_byte_at_an_allowed_path(name):
    altered = dict(FIXTURES)
    key = PREFIX + "tests/fixtures/web_tls/" + name
    altered[key] += b"\n"
    with pytest.raises(AssertionError, match="Changed public TLS fixture"):
        verifier.check_source_paths(altered.__getitem__, set(altered), PREFIX)


@pytest.mark.parametrize("name", ["other/cert.pem", "tests/fixtures/web_tls/private.key",
                                "tests/fixtures/other/key.pem", "tests/fixtures/web_tls/../web_tls/key.pem"])
def test_source_archive_rejects_identical_public_bytes_at_other_paths(name):
    altered = dict(FIXTURES)
    altered[PREFIX + name] = next(iter(FIXTURES.values()))
    with pytest.raises(AssertionError):
        verifier.check_source_paths(altered.__getitem__, set(altered), PREFIX)


@pytest.mark.parametrize("prefix", ["/absolute/", "../escape/"])
def test_source_fixture_allowance_cannot_bypass_archive_root_validation(prefix):
    name = prefix + "tests/fixtures/web_tls/cert.pem"
    with pytest.raises(AssertionError):
        verifier.check_source_paths(lambda _: next(iter(FIXTURES.values())), {name}, prefix)


def test_actual_wheel_rejects_even_the_exact_public_test_certificate(tmp_path):
    with zipfile.ZipFile(tmp_path / "per_pulse-0.1.0a3-py3-none-any.whl", "w") as wheel:
        wheel.writestr("tests/fixtures/web_tls/cert.pem", FIXTURES[PREFIX + "tests/fixtures/web_tls/cert.pem"])
    with tarfile.open(tmp_path / "per_pulse-0.1.0a3.tar.gz", "w:gz"):
        pass
    with pytest.raises(AssertionError, match="cert.pem"):
        verifier.verify(tmp_path, "0.1.0a3")


def test_duplicate_source_member_cannot_hide_changed_fixture_bytes(tmp_path, monkeypatch):
    # Client assets are unrelated to this archive-member identity check.
    monkeypatch.setattr(verifier, "check_client", lambda *_: None)
    with zipfile.ZipFile(tmp_path / "per_pulse-0.1.0a3-py3-none-any.whl", "w") as wheel:
        for package in ("per_pulse", "pulse_system", "pulse_harness"):
            wheel.writestr(package + "/__init__.py", "")
        wheel.writestr("pulse_harness/core/secrets.py", "")
        wheel.writestr("per_pulse-0.1.0a3.dist-info/METADATA", "Name: per-pulse\nVersion: 0.1.0a3\n")
        for name in ("LICENSE", "NOTICE"):
            wheel.writestr("per_pulse-0.1.0a3.dist-info/licenses/" + name, "")
    members = [(PREFIX + name, b"") for name in ("README.md", "LICENSE", "NOTICE", "SECURITY.md",
        "CHANGELOG.md", "CONTRIBUTING.md", "scripts/verify_distribution.py", "docs/releases/v0.1.0a3.md")]
    members.append((PREFIX + "pyproject.toml", b'[project]\nversion="0.1.0a3"\n'))
    name = PREFIX + "tests/fixtures/web_tls/cert.pem"
    members.extend([(name, FIXTURES[name] + b"\n"), (name, FIXTURES[name])])
    with tarfile.open(tmp_path / "per_pulse-0.1.0a3.tar.gz", "w:gz") as source:
        for name, content in members:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            source.addfile(member, io.BytesIO(content))
    with pytest.raises(AssertionError, match="Duplicate source archive paths"):
        verifier.verify(tmp_path, "0.1.0a3")
