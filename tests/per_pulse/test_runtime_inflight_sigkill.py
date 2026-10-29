"""Kill a real Per Pulse server while its native Kernel turn is in flight.

Both servers use the same durable World/Kernel/SQLite, a real OS process and
real loopback HTTP. The model transport is explicitly synthetic and blocked
after request admission; this is not a provider-quality or billing experiment.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from pulse_system.core.types import RuntimeLeaseState
from pulse_system.substrate.storage import Storage


# Deliberately run uvicorn, PageService, RuntimeService and the Kernel in a
# different OS process. A killed thread/TestClient is not this failure mode.
_SERVER = r"""
import json
import os
from pathlib import Path
import sys
import time

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

home, port, mode, admitted_path, journal_path = sys.argv[1:]
os.environ["PER_PULSE_INFLIGHT_KILL_QA_KEY"] = "synthetic-local-only"


def respond(request):
    payload = json.loads(request.content)
    current_user = next(
        message for message in reversed(payload["messages"])
        if message.get("role") == "user"
    )
    # A private out-of-World fixture journal records only the last *user
    # request*, not private provider headers or complete model context.
    with open(journal_path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(current_user, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if mode == "blocked":
        Path(admitted_path).write_text("native-provider-request-entered", encoding="ascii")
        # No response or graceful cancellation. The test physically kills
        # this process while the original Kernel worker still owns its turn.
        while True:
            time.sleep(60)
    chunks = [
        {"choices": [{"index": 0, "delta": {
            "content": "Synthetic response after physical restart."
        }, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 21, "completion_tokens": 8,
                   "total_tokens": 29}},
    ]
    wire = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content=wire + "data: [DONE]\n\n",
    )


def factory(_scope):
    return ChatHTTPModelAdapter(
        ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "inflight-kill-fixture",
            credential_env="PER_PULSE_INFLIGHT_KILL_QA_KEY",
        ),
        transport=httpx.MockTransport(respond),
    )


app = create_app(
    home, model_factory=factory,
    pulse=PulseSettings(
        enabled=True, spontaneous_rate=0, max_background_pulses_per_hour=0,
    ),
)
uvicorn.run(app, host="127.0.0.1", port=int(port), log_level="error")
"""


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _spawn(home: Path, port: int, mode: str, admitted: Path, journal: Path,
           workdir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-u", "-c", _SERVER, str(home), str(port),
         mode, str(admitted), str(journal)],
        cwd=workdir, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )


def _healthy(client: httpx.Client, process: subprocess.Popen) -> None:
    until = time.monotonic() + 15
    while time.monotonic() < until:
        if process.poll() is not None:
            errors = process.stderr.read() if process.stderr else ""
            raise AssertionError(f"Native server exited before health: {errors[-2500:]}")
        try:
            if client.get("/api/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise AssertionError("Native server failed to become healthy in 15 seconds")


def _until(predicate, label: str, *, seconds: float = 15) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out: {label}")


def _requests(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _last_user_contains(message: dict, phrase: str) -> bool:
    return phrase in json.dumps(message.get("content"), ensure_ascii=False)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    if process.stderr is not None:
        process.stderr.close()


def test_physical_kill_during_kernel_turn_keeps_original_work_and_does_not_replay(
    tmp_path,
):
    home = tmp_path / "home"
    journal = tmp_path / "outbound-fixture-requests.jsonl"
    admitted = tmp_path / "provider-entered"
    port = _port()
    first = _spawn(home, port, "blocked", admitted, journal, tmp_path)
    second = None
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}",
                          timeout=3.0, trust_env=False)
    try:
        _healthy(client, first)
        original_world = client.get("/api/bootstrap").json()["workspaceId"]
        created = client.post("/api/pages", json={
            "title": "Work interrupted mid-turn",
            "instruction": "OLD_ACTIVE_TURN_NOBILL_FIXTURE",
        })
        assert created.status_code == 201, created.text
        work_id = created.json()["page"]["id"]
        _until(admitted.is_file, "real Kernel dispatched blocked model request")
        assert first.poll() is None
        assert len(_requests(journal)) == 1
        assert _last_user_contains(_requests(journal)[0],
                                   "OLD_ACTIVE_TURN_NOBILL_FIXTURE")
        before = client.get(f"/api/pages/{work_id}").json()
        assert any(msg["role"] == "user" for msg in before["messages"])
        assert not any(msg["role"] == "assistant" for msg in before["messages"])

        # Kill the complete native HTTP/Kernel/Pulse process, not just a lease
        # keeper or a test worker. It cannot run finally/abort or publish a
        # response from this permanently blocked transport.
        _stop(first)
        old_store = Storage(home / "world.db")
        try:
            lease = old_store.get_runtime_lease()
            assert lease is not None
            assert lease.state is RuntimeLeaseState.ACTIVE
            deadline = (lease.expires_at + timedelta(milliseconds=250)
                        - datetime.now(timezone.utc)).total_seconds()
            assert deadline < 35, "Unexpected default native lease TTL after physical kill"
        finally:
            old_store.close()
        time.sleep(max(0, deadline))

        # A new service acquires a *new fenced epoch* of the same World.
        # Mere restart must not silently repeat the interrupted request.
        second = _spawn(home, port, "reply", admitted, journal, tmp_path)
        _healthy(client, second)
        assert client.get("/api/bootstrap").json()["workspaceId"] == original_world
        time.sleep(0.4)
        assert len(_requests(journal)) == 1, "Old in-flight model turn replayed on restart"
        reopened = client.get(f"/api/pages/{work_id}").json()
        assert any(msg["role"] == "user" for msg in reopened["messages"])
        assert not any(msg["role"] == "assistant" for msg in reopened["messages"])

        # A different local Work of the same subject can still perform new
        # foreground work after recovery; no old response is invented.
        next_work = client.post("/api/pages", json={
            "title": "Fresh work after crash",
            "instruction": "NEW_AFTER_KILL_EXPLICIT_USER_WORK",
        })
        assert next_work.status_code == 201, next_work.text
        next_id = next_work.json()["page"]["id"]
        _until(lambda: any(
            msg["role"] == "assistant"
            for msg in client.get(f"/api/pages/{next_id}").json()["messages"]
        ), "new foreground Work completes in recovered native runtime")
        attempts = _requests(journal)
        assert len(attempts) == 2, "An old model turn must not silently retry"
        assert _last_user_contains(attempts[1], "NEW_AFTER_KILL_EXPLICIT_USER_WORK")
        assert client.get("/api/bootstrap").json()["workspaceId"] == original_world
        reopened = client.get(f"/api/pages/{work_id}").json()
        assert not any(msg["role"] == "assistant" for msg in reopened["messages"])
    finally:
        client.close()
        if second is not None:
            _stop(second)
        _stop(first)
