"""Actual local Docker effects and OS death, opt-in via PER_PULSE_DOCKER_IMAGE.

Model selection is an explicit local TEST_DOUBLE. Docker, the container,
native approvals, SQLite, lease expiry and file effects are real. The delayed
CLI case adds a named creation barrier, not a replacement Docker daemon.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

from pulse_harness.adapters.command import DockerCommandExecutor
from pulse_harness.core.errors import ProviderUnavailable
from pulse_harness.core.provider import CancellationToken


IMAGE = os.environ.get("PER_PULSE_DOCKER_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set PER_PULSE_DOCKER_IMAGE to an already installed real Docker image")


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError("Expected physical lifecycle observation did not arrive")


def docker(*args):
    return subprocess.run([shutil.which("docker"), "--host", "unix:///var/run/docker.sock", *args],
                          capture_output=True, text=True, timeout=12)


def container_id(name):
    result = docker("ps", "-aq", "--no-trunc", "--filter", "name=^/" + name + "$")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def stop(process):
    if process is not None and process.poll() is None:
        process.kill()
        process.wait(timeout=5)


def delayed_create_cli(root):
    """A test-only CLI barrier forwards the actual create after parent death."""
    bindir = root / "cli"
    bindir.mkdir()
    script = bindir / "docker"
    script.write_text("#!" + sys.executable + "\n" + f'''
import json,os,subprocess,sys,time
from pathlib import Path
root=Path({str(root)!r})
args=sys.argv[1:]
if "create" in args:
    (root/"create-waiting.json").write_text(json.dumps({{"pid":os.getpid()}}))
    deadline=time.monotonic()+30
    while not (root/"release-create").exists():
        if time.monotonic()>deadline: sys.exit(90)
        time.sleep(.025)
result=subprocess.run([{shutil.which("docker")!r},*args],capture_output=True,timeout=12)
if "create" in args:
    (root/"create-finished.json").write_text(json.dumps({{"code":result.returncode,"id":result.stdout.decode().strip()}}))
try:
    sys.stdout.buffer.write(result.stdout);sys.stdout.buffer.flush()
    sys.stderr.buffer.write(result.stderr);sys.stderr.buffer.flush()
except BrokenPipeError:
    pass
os._exit(result.returncode)
''')
    script.chmod(0o700)
    return bindir


@pytest.mark.parametrize("code,deadline", [(0, False), (7, False), (124, True)])
def test_actual_container_exit_receipt_and_cleanup(tmp_path, code, deadline):
    executor = DockerCommandExecutor(tmp_path, IMAGE)
    name = "p-pulse-command-" + os.urandom(16).hex()
    script = ("import time; print('before deadline',flush=True); time.sleep(5)" if deadline else
              "from pathlib import Path; import sys; Path('result.txt').write_text('actual command result'); print('receipt'); sys.exit(" + str(code) + ")")
    try:
        result = executor.execute({"argv": ["python3", "-c", script], "timeout_seconds": 1 if deadline else 10},
            CancellationToken(), guard=lambda: None, start_guard=nullcontext, invocation_name=name)
        assert result["exit_code"] == code
        assert result["execution_status"] == "exited"
        assert not container_id(name)
        if deadline:
            assert "before deadline" in result["stdout"] and result["exit_124"]
        else:
            assert (tmp_path / "result.txt").read_text() == "actual command result"
            assert result["stdout"] == "receipt\n"
    finally:
        executor.close()


def test_name_collision_does_not_remove_an_unrelated_container(tmp_path):
    name = "p-pulse-command-" + os.urandom(16).hex()
    created = docker("create", "--name", name, "--network", "none", IMAGE, "python3", "-c", "pass")
    assert created.returncode == 0, created.stderr
    foreign_id = created.stdout.strip()
    executor = DockerCommandExecutor(tmp_path, IMAGE)
    try:
        with pytest.raises(ProviderUnavailable):
            executor.execute({"argv": ["python3", "-c", "pass"]}, CancellationToken(),
                guard=lambda: None, start_guard=nullcontext, invocation_name=name)
        assert container_id(name) == foreign_id
        assert not executor._write_resource.unresolved_commands
    finally:
        executor.close()
        assert docker("rm", "--force", foreign_id).returncode == 0


def test_missing_durable_operation_never_starts_the_created_container(tmp_path):
    name = "p-pulse-command-" + os.urandom(16).hex()
    executor = DockerCommandExecutor(tmp_path, IMAGE)

    def cannot_record(_container_id):
        raise RuntimeError("explicit native operation persistence failure")
    try:
        with pytest.raises(RuntimeError, match="operation persistence failure"):
            executor.execute({"argv": ["python3", "-c", "from pathlib import Path; Path('unexpected').touch()"]},
                CancellationToken(), guard=lambda: None, start_guard=nullcontext,
                invocation_name=name, on_created=cannot_record)
        assert not (tmp_path / "unexpected").exists()
        assert not container_id(name)
    finally:
        executor.close()


@pytest.mark.parametrize("scenario", ["running-container", "renamed-container", "late-inert-create"])
def test_native_reentry_stops_old_writer_before_original_work_continues(tmp_path, scenario):
    delayed_create = scenario == "late-inert-create"
    assert sys.platform == "linux" and shutil.which("docker"), "configured real-Docker test requires Linux and Docker"
    inspected = docker("image", "inspect", IMAGE)
    assert inspected.returncode == 0, "preinstall the selected image before this test"
    world = tmp_path / "world"
    workspace = world / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "shared.txt").write_text("original\n")
    source = Path(__file__).resolve().parents[2] / "src"
    fixture = Path(__file__).with_name("command_recovery_fixture.py")
    env = {**os.environ, "PYTHONPATH": str(source)}
    owner_env = dict(env)
    if delayed_create:
        owner_env["PATH"] = str(delayed_create_cli(tmp_path)) + os.pathsep + os.environ.get("PATH", "")
    owner = successor = None
    name = None
    original_container = None
    logs = []

    def spawn(mode, child_env):
        log = (tmp_path / (mode + ".log")).open("w")
        logs.append(log)
        return subprocess.Popen([sys.executable, str(fixture), str(tmp_path), mode], env=child_env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

    try:
        owner = spawn("owner", owner_env)
        marker = tmp_path / "create-waiting.json" if delayed_create else workspace / "container-started"
        wait_for(marker.exists, 25)
        original = json.loads((tmp_path / "owner-ready.json").read_text())
        with sqlite3.connect(f"file:{world / 'kernel.db'}?mode=ro", uri=True) as db:
            authority = json.loads(db.execute("SELECT authority_json FROM action_authorities WHERE action_request_id=?", (original["action"],)).fetchone()[0])
            assert db.execute("SELECT status FROM actions WHERE action_request_id=?", (original["action"],)).fetchone() == ("running",)
        name = authority["identity"]["container_name"]
        if delayed_create:
            assert not container_id(name)
        else:
            original_container = container_id(name)
            assert original_container
            with sqlite3.connect(f"file:{world / 'kernel.db'}?mode=ro", uri=True) as db:
                assert db.execute("SELECT operation_id FROM actions WHERE action_request_id=?", (original["action"],)).fetchone() == ("docker-container-" + original_container,)
        stop(owner)
        assert owner.returncode != 0
        if scenario == "renamed-container":
            renamed = docker("rename", original_container, "p-pulse-recovery-renamed-" + os.urandom(12).hex())
            assert renamed.returncode == 0, renamed.stderr
            assert not container_id(name)
        with sqlite3.connect(f"file:{world / 'world.db'}?mode=ro", uri=True) as db:
            expiry, state = db.execute("SELECT expires_at,state FROM runtime_leases WHERE scope='pulse_world'").fetchone()
        assert state == "active"
        remaining = (datetime.fromisoformat(expiry) - datetime.now(timezone.utc)).total_seconds() + .2
        assert remaining < 7
        time.sleep(max(0, remaining))
        successor = spawn("successor", env)
        assert successor.wait(timeout=20) == 0, (tmp_path / "successor.log").read_text()
        result = json.loads((tmp_path / "successor-result.json").read_text())
        assert result["page"] == original["page"]
        assert result["command_recovery"] == {"confirmed": 1, "unresolved": 0}
        assert result["shared"] == "successor approved write\n"
        assert not container_id(name)
        if original_container:
            listed = docker("ps", "-aq", "--filter", "id=" + original_container)
            assert listed.returncode == 0 and not listed.stdout.strip()
        with sqlite3.connect(f"file:{world / 'kernel.db'}?mode=ro", uri=True) as db:
            assert db.execute("SELECT a.status,r.confirmed_exit FROM actions a JOIN recoveries r USING(action_request_id) WHERE action_request_id=?", (original["action"],)).fetchone() == ("uncertain", 1)
            assert db.execute("SELECT COUNT(*) FROM action_terminals WHERE action_request_id=?", (original["action"],)).fetchone() == (1,)
        if delayed_create:
            (tmp_path / "release-create").touch()
            wait_for((tmp_path / "create-finished.json").exists)
            late = json.loads((tmp_path / "create-finished.json").read_text())
            assert late["code"] == 0, late
            assert container_id(name) == late["id"]
            state = docker("inspect", "--format", "{{.State.Status}}", late["id"])
            assert state.returncode == 0 and state.stdout.strip() == "created"
            assert not (workspace / "container-started").exists()
        else:
            # Observe beyond the killed command's scheduled late write, rather
            # than asserting only that a cleanup call returned successfully.
            deadline = float((workspace / "container-started").read_text()) + 22
            while time.time() < deadline:
                assert not (workspace / "late-write").exists()
                time.sleep(.1)
        assert (workspace / "shared.txt").read_text() == "successor approved write\n"
        assert not (workspace / "late-write").exists()
    finally:
        stop(owner)
        stop(successor)
        if delayed_create:
            (tmp_path / "release-create").touch()
            if (tmp_path / "create-waiting.json").exists():
                wait_for((tmp_path / "create-finished.json").exists)
        if name:
            owned = {value for value in (container_id(name), original_container) if value}
            for exact in owned:
                present = docker("ps", "-aq", "--filter", "id=" + exact)
                assert present.returncode == 0
                if not present.stdout.strip():
                    continue
                removed = docker("rm", "--force", exact)
                assert removed.returncode == 0, removed.stderr
            assert not container_id(name)
        for log in logs:
            log.close()
