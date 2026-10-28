"""A process dies after a real effect, before the Kernel records its outcome.

Synthetic model, real approval/file effect/SQLite/OS process. No provider bill.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time

import httpx

from pulse_system.core.types import RuntimeLeaseState
from pulse_system.substrate.storage import Storage
from test_runtime_inflight_sigkill import _healthy, _port, _stop, _until


def test_killed_effect_stays_uncertain_and_original_work_can_continue(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home, evidence = tmp_path / "home", tmp_path / "evidence"
    port = _port()
    env = {**os.environ, "PYTHONPATH": str(root / "src")}

    def spawn(mode):
        return subprocess.Popen(
            [sys.executable, "-u", str(root / "tests/browser/crash_recovery_server.py"),
             "--data-dir", str(home), "--evidence-dir", str(evidence),
             "--port", str(port), "--mode", mode],
            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )

    def events():
        path = evidence / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    first, second = spawn("effect"), None
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False)
    try:
        _healthy(client, first)
        world = client.get("/api/bootstrap").json()["workspaceId"]
        created = client.post("/api/pages", json={"title": "Interrupted file", "instruction": "WRITE_BEFORE_CRASH"})
        assert created.status_code == 201, created.text
        work = created.json()["page"]["id"]
        route = f"/api/pages/{work}"
        _until(lambda: any(a["canResolve"] for a in client.get(route + "/execution").json()["pendingActions"]),
               "native file approval")
        waiting = client.get(route + "/execution").json()
        action_id = waiting["pendingActions"][0]["actionId"]
        turn_id = waiting["turnId"]
        action_url = route + "/execution/actions/" + action_id
        assert not (home / "workspace/interrupted.txt").exists()

        def approve():
            try:
                return client.post(action_url, json={"decision": "allow_once"}, timeout=15)
            except httpx.HTTPError:
                return None  # The process dies without returning an HTTP receipt.

        with ThreadPoolExecutor(max_workers=1) as pool:
            receipt = pool.submit(approve)
            try:
                _until(lambda: any(e["kind"] == "effect" for e in events()), "real effect before receipt")
                path = home / "workspace/interrupted.txt"
                assert path.read_text() == "Written once before the process died.\n"
                original_stat = path.stat()
                with sqlite3.connect(f"file:{home / 'kernel.db'}?mode=ro", uri=True) as db:
                    native = db.execute("SELECT status FROM actions WHERE action_request_id = ?", (action_id,)).fetchone()
                assert native == ("running",), "The fixture must stop before the durable effect receipt"
                assert not receipt.done()
                _stop(first)
            finally:
                _stop(first)
            assert receipt.result(timeout=5) is None

        storage = Storage(home / "world.db")
        try:
            lease = storage.get_runtime_lease()
            assert lease.state is RuntimeLeaseState.ACTIVE
            delay = (lease.expires_at + timedelta(milliseconds=250) - datetime.now(timezone.utc)).total_seconds()
            assert delay < 35
        finally:
            storage.close()
        time.sleep(max(0, delay))
        second = spawn("reply")
        _healthy(client, second)
        assert client.get("/api/bootstrap").json()["workspaceId"] == world
        state = client.get(route + "/execution").json()
        assert state["recoveryRequired"] and state["canCancel"]
        assert state["historicalUncertainty"]
        assert state["uncertainActions"][0]["actionId"] == action_id
        assert state["uncertainActions"][0]["turnId"] == turn_id
        assert state["uncertainActions"][0]["status"] == "uncertain"
        assert state["uncertainActions"][0]["arguments"]["path"] == "interrupted.txt"
        assert client.get(route + "/artifacts").json()["artifacts"] == []
        assert client.get(f"/api/pages/home/execution").json()["uncertainActions"] == []

        # A lost Allow receipt is a lookup of the same uncertain action. It
        # cannot authorize a second effect or report the first as successful.
        retried = client.post(action_url, json={"decision": "allow_once"})
        assert retried.status_code == 200, retried.text
        assert retried.json()["repeated"] and not retried.json()["actionCompleted"]
        assert retried.json()["actionStatus"] == "uncertain"
        assert len(events()) == 2  # Exactly one request and one physical effect.

        stopped = client.post(route + "/execution/stop", json={"turnId": turn_id})
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["execution"]["uncertainActions"][0]["actionId"] == action_id
        assert stopped.json()["execution"]["uncertainActions"][0]["status"] == "uncertain"
        assert path.read_text() == "Written once before the process died.\n", "Stop is not undo"
        sent = client.post(route + "/messages", json={"content": "CONTINUE_AFTER_REVIEW"})
        assert sent.status_code == 202, sent.text
        _until(lambda: any("Continued the same Work" in m["content"]
                           for m in client.get(route).json()["messages"]), "same Work continues after explicit review")
        state = client.get(route + "/execution").json()
        assert state["turnId"] != turn_id and not state["recoveryRequired"]
        assert state["uncertainActions"][0]["actionId"] == action_id
        assert [e["kind"] for e in events()] == ["request", "effect", "request"]
        assert path.stat().st_mtime_ns == original_stat.st_mtime_ns, "No repeat file write"
    finally:
        client.close()
        if second is not None:
            _stop(second)
        _stop(first)
