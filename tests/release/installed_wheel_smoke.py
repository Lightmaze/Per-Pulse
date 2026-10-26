"""Installed-wheel first-run acceptance using only the Python standard library.

This script must run with the Python executable from an isolated wheel
installation, from outside the repository root. No test transport or provider
request is used. It checks the actual CLI, packaged static assets and native
World persistence rather than importing the application from the checkout.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def request(port: int, path: str) -> tuple[bytes, str]:
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        assert response.status == 200, f"{path}: HTTP {response.status}"
        return response.read(), response.headers.get("Content-Type", "")


def json_response(port: int, path: str) -> dict:
    body, _ = request(port, path)
    return json.loads(body)


def post_json(port: int, path: str, payload: dict, *, status: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = Request(f"http://127.0.0.1:{port}{path}", data=data,
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=10) as response:
        assert response.status == status, f"{path}: HTTP {response.status}"
        return json.load(response)


def run_workspace(root: Path, *, example: bool, fake_provider: bool,
                  exercise: bool = False, expected: dict | None = None) -> dict:
    port = free_port()
    env = os.environ.copy()
    for key in ("P_PULSE_CHAT_ENDPOINT", "P_PULSE_CHAT_MODEL", "P_PULSE_API_KEY",
                "P_PULSE_CREDENTIAL_ENV"):
        env.pop(key, None)
    if fake_provider:
        # A configured-looking environment must NOT reach a provider in example
        # mode, nor report the example as connected.
        env.update({
            "P_PULSE_CHAT_ENDPOINT": "http://127.0.0.1:1/v1/chat/completions",
            "P_PULSE_CHAT_MODEL": "must-not-be-called",
            "P_PULSE_API_KEY": "synthetic-example-test-only",
        })
    command = [
        sys.executable, "-m", "per_pulse.cli",
        "--host", "127.0.0.1", "--port", str(port),
        "--data-dir", str(root),
    ]
    if example:
        command.append("--example")
    output_path = root / ("example.log" if example else "ordinary.log")
    root.mkdir(parents=True, exist_ok=True)
    with output_path.open("w+b") as output:
        process = subprocess.Popen(
            command, env=env, cwd=root, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 25
            while True:
                if process.poll() is not None:
                    raise AssertionError(f"Server exited during startup ({process.returncode})")
                try:
                    health = json_response(port, "/api/health")
                    break
                except (URLError, TimeoutError, ConnectionError):
                    if time.monotonic() >= deadline:
                        raise AssertionError("Installed CLI did not serve /api/health")
                    time.sleep(0.2)
            assert health == {"status": "ok", "providerConfigured": False}, health
            bootstrap = json_response(port, "/api/bootstrap")
            assert bootstrap["example"] is example, bootstrap
            assert bootstrap["homeId"] == "home"
            assert isinstance(bootstrap["workspaceId"], str) and bootstrap["workspaceId"]
            home = json_response(port, "/api/pages/home")
            assert isinstance(home["messages"], list)
            if example:
                assert len(bootstrap["pages"]) >= 3, "Packaged example pages were not seeded"
                assert home["messages"], "Packaged example has no native Home history"
            else:
                if exercise:
                    assert len(bootstrap["pages"]) == 1, "Example pages leaked into ordinary World"
                    assert home["messages"] == [], "Example messages leaked into ordinary Home"
                if expected is not None:
                    assert bootstrap["workspaceId"] == expected["workspaceId"]
                    assert sorted(page["id"] for page in bootstrap["pages"]) == expected["pageIds"]
                    assert [message["id"] for message in home["messages"]] == expected["homeMessageIds"]
            html, content_type = request(port, "/")
            assert "text/html" in content_type
            assert b'<div id="root"></div>' in html, "Wheel omitted built client entry"
            scripts = re.findall(rb'<script[^>]+src="([^"]+)"', html)
            assets = [path.decode("utf-8") for path in scripts if path.startswith(b"/assets/")]
            assert assets, "Wheel served source HTML instead of compiled JS"
            asset_body, _ = request(port, assets[0])
            assert len(asset_body) > 100, "Compiled JS asset is empty"
            if exercise:
                # A user can perform a complete offline Home -> Work -> sourced
                # Home round trip through the *installed* service. No script
                # pretends the queued input came from a connected model.
                prompt = post_json(port, "/api/pages/home/messages",
                                   {"content": "Keep the original source with the result."}, status=202)
                original_id = prompt["messageId"]
                created = post_json(port, "/api/pages", {
                    "title": "Installed first Work",
                    "instruction": "Develop one sourced result.",
                    "sourcePageId": "home", "sourceMessageId": original_id,
                }, status=201)
                work_id = created["page"]["id"]
                assert created["reference"]["sourceMessageId"] == original_id
                followup = post_json(port, f"/api/pages/{work_id}/messages",
                                     {"content": "Record the decision and its reason."}, status=202)
                returned = post_json(port, f"/api/pages/{work_id}/return", {
                    "content": "A chosen result, with its source.",
                    "messageId": followup["messageId"],
                }, status=201)
                assert returned["reference"]["sourcePageId"] == work_id
                assert returned["reference"]["sourceMessageId"] == followup["messageId"]
                assert returned["view"]["page"]["id"] == "home"
                work = json_response(port, f"/api/pages/{work_id}")
                assert any(row["id"] == followup["messageId"] for row in work["messages"])
                home = json_response(port, "/api/pages/home")
                assert any(row["id"] == returned["messageId"] and
                           row["sourcePageId"] == work_id and
                           row["sourceMessageId"] == followup["messageId"]
                           for row in home["messages"]), "Sourced return missing from installed Home"
                bootstrap = json_response(port, "/api/bootstrap")
            result = {
                "workspaceId": bootstrap["workspaceId"],
                "pageIds": sorted(page["id"] for page in bootstrap["pages"]),
                "homeMessageIds": [message["id"] for message in home["messages"]],
                "example": example,
            }
            if expected is not None:
                assert result == expected, "Native sourced work changed across installed restart"
            return result
        except BaseException as error:
            output.flush()
            output.seek(0)
            raise AssertionError(
                f"{error}\nInstalled server output:\n"
                f"{output.read().decode('utf-8', 'replace')[-5000:]}"
            ) from error
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> None:
    # The CI runner also has an editable source install. Reject any accidental
    # reliance on it: all three shipped Python packages must resolve *inside*
    # the new isolated wheel environment, never from the checkout or system site.
    assert sys.prefix != sys.base_prefix, "Wheel smoke must use a virtual environment"
    prefix = Path(sys.prefix).resolve()
    for package in ("per_pulse", "pulse_system", "pulse_harness"):
        spec = importlib.util.find_spec(package)
        assert spec and spec.origin, f"Installed wheel is missing {package}"
        origin = Path(spec.origin).resolve()
        assert origin.is_relative_to(prefix), (
            f"{package} imported outside the isolated wheel environment: {origin}"
        )

    with tempfile.TemporaryDirectory(prefix="per-pulse-installed-wheel-") as temp:
        root = Path(temp)
        example_before = run_workspace(root, example=True, fake_provider=True)
        ordinary = run_workspace(root, example=False, fake_provider=False, exercise=True)
        ordinary_after = run_workspace(root, example=False, fake_provider=False, expected=ordinary)
        example_after = run_workspace(root, example=True, fake_provider=True)
        assert example_before == example_after, "Native example history changed across restart"
        assert ordinary["workspaceId"] != example_before["workspaceId"], "Examples share an ordinary World"
        assert not ordinary["homeMessageIds"]
        print(json.dumps({
            "passed": 8,
            "checks": [
                "all three packages resolved from isolated virtualenv, not editable source",
                "installed wheel CLI started from outside source checkout",
                "compiled web entry and JS assets served from wheel",
                "example mode refused provider access even with fake credentials",
                "example World retained original pages and messages across restart",
                "ordinary disconnected World stayed empty and isolated",
                "loopback health/bootstrap/pages APIs returned persistent native state",
                "installed ordinary Home -> Work -> sourced Home survives restart without a provider",
            ],
            "provider": "none; example authored fixture and real local persistence",
        }, indent=2))


if __name__ == "__main__":
    main()
