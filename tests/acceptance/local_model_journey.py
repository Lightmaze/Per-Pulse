"""Run the first product path against a running Per Pulse with a real model.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

Start Per Pulse yourself with a configured model, workspace actions and
experience enabled, then run:

    python tests/acceptance/local_model_journey.py --url http://127.0.0.1:8000 \\
        --output ./private-journey.json

The script only uses the public HTTP API. It records what happened in each
step (status, timing, replies, approvals, files, experience) and never
invents a reply. A step that times out or fails is recorded and the journey
continues where that makes sense. The report can contain your workspace
content: keep it private.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import httpx


class Journey:
    def __init__(self, url: str, turn_timeout: float):
        self.http = httpx.Client(base_url=url, timeout=60.0, headers={"Origin": url})
        self.turn_timeout = turn_timeout
        self.steps: list[dict] = []

    # ── HTTP helpers ──────────────────────────────────────────────
    def get(self, path: str) -> Any:
        response = self.http.get(path)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: dict) -> Any:
        response = self.http.post(path, json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"{path} -> {response.status_code}: {response.text[:400]}")
        return response.json()

    def view(self, page_id: str) -> dict:
        return self.get(f"/api/pages/{page_id}")

    @staticmethod
    def replies(view: dict) -> list[dict]:
        return [m for m in view["messages"] if m["role"] == "assistant"]

    def wait(self, page_id: str, done: Callable[[dict], bool]) -> tuple[dict, bool]:
        deadline = time.monotonic() + self.turn_timeout
        current = self.view(page_id)
        while time.monotonic() < deadline:
            current = self.view(page_id)
            if done(current):
                return current, True
            time.sleep(2)
        return current, False

    def settled(self, page_id: str, replies_before: int) -> tuple[dict, bool]:
        """Wait for a new reply and an idle page, or for a pending approval."""
        def done(view: dict) -> bool:
            if view["execution"]["pendingActions"]:
                return True
            return (len(self.replies(view)) > replies_before
                    and view["page"]["status"] in {"idle", "error"})
        return self.wait(page_id, done)

    def step(self, name: str, action: Callable[[], dict]) -> dict:
        started = time.monotonic()
        record: dict[str, Any] = {"step": name}
        try:
            record.update(action())
            record.setdefault("ok", True)
        except Exception as error:  # Record and continue: a journey report, not a test.
            record.update(ok=False, error=f"{type(error).__name__}: {error}")
        record["seconds"] = round(time.monotonic() - started, 1)
        self.steps.append(record)
        print(json.dumps({k: record[k] for k in ("step", "ok", "seconds")}), flush=True)
        return record

    # ── The journey ───────────────────────────────────────────────
    def send(self, page_id: str, content: str) -> dict:
        before = len(self.replies(self.view(page_id)))
        self.post(f"/api/pages/{page_id}/messages", {"content": content})
        view, finished = self.settled(page_id, before)
        new = self.replies(view)[before:]
        return {"ok": finished, "pageStatus": view["page"]["status"],
                "reply": new[-1]["content"] if new else None,
                "pendingActions": [a["toolName"] for a in view["execution"]["pendingActions"]]}

    def approve_all(self, page_id: str, limit: int = 4) -> dict:
        approved = []
        for _ in range(limit):
            pending = self.view(page_id)["execution"]["pendingActions"]
            if not pending:
                break
            action = pending[0]
            before = len(self.replies(self.view(page_id)))
            self.post(f"/api/pages/{page_id}/execution/actions/{action['actionId']}",
                      {"decision": "allow_once"})
            approved.append({"tool": action["toolName"], "path": action["arguments"].get("path")
                             if isinstance(action["arguments"], dict) else None})
            self.settled(page_id, before)
        view = self.view(page_id)
        return {"approved": approved, "pageStatus": view["page"]["status"],
                "files": [a["path"] for a in view["artifacts"]],
                "reply": self.replies(view)[-1]["content"] if self.replies(view) else None}

    def run(self) -> dict:
        boot = self.get("/api/bootstrap")
        report: dict[str, Any] = {
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": boot["version"], "workspaceId": boot["workspaceId"],
            "provider": boot["provider"], "postureEnabled": boot["postureEnabled"],
        }
        if not boot["provider"]["configured"]:
            report["outcome"] = "blocked_provider_unconfigured"
            return report

        self.step("home_conversation", lambda: self.send(
            "home", "I'm writing a short launch note for a small open-source tool. "
                    "Who should the first audience be? Answer in three sentences."))

        state: dict[str, str] = {}

        def create_work() -> dict:
            created = self.post("/api/pages", {
                "title": "Launch note",
                "instruction": ("Write a file named launch-note.md with a title line and three short "
                                "bullet points announcing the tool to its first audience. "
                                "Use the file tool to write it, then tell me what you wrote."),
                "sourcePageId": "home",
            })
            state["work"] = created["page"]["id"]
            view, finished = self.settled(state["work"], 0)
            return {"ok": finished, "workId": state["work"], "pageStatus": view["page"]["status"],
                    "pendingActions": [a["toolName"] for a in view["execution"]["pendingActions"]]}

        self.step("create_work_and_request_file", create_work)
        if "work" not in state:
            report.update(outcome="incomplete", steps=self.steps)
            return report
        work = state["work"]

        self.step("approve_file_and_read_back", lambda: self._approve_and_read(work))

        def bring_home() -> dict:
            last = self.replies(self.view(work))[-1]
            excerpt = last["content"][:300]
            returned = self.post(f"/api/pages/{work}/return", {"content": excerpt, "messageId": last["id"]})
            home = self.view("home")
            message = next(m for m in home["messages"] if m["id"] == returned["messageId"])
            return {"sourcePageId": message["sourcePageId"], "sourceMessageId": message["sourceMessageId"],
                    "linkedToSource": message["sourceMessageId"] == last["id"]}

        self.step("bring_result_home", bring_home)

        def tick() -> dict:
            before = len(self.replies(self.view(work)))
            self.post(f"/api/pages/{work}/tick", {"count": 2})
            view, finished = self.wait(work, lambda v: v["tick"] and v["tick"]["status"] != "running")
            return {"ok": finished and view["tick"]["status"] == "complete", "tick": view["tick"],
                    "newReplies": [m["content"][:400] for m in self.replies(view)[before:]]}

        self.step("tick_two_steps", tick)

        self.step("correction", lambda: self.send(
            work, "One correction for launch notes from now on: put the release date on the first "
                  "line, before the title. Please remember that for future notes."))

        def experience() -> dict:
            lessons = self.get(f"/api/pages/{work}/posture")["experiences"]
            return {"saved": [{"title": e.get("title"), "scope": e.get("scope"),
                               "representation": e.get("representation", "lesson")} for e in lessons],
                    "outcome": "native_lesson_admitted" if lessons else "capture_absent"}

        self.step("experience_saved", experience)

        def later_work() -> dict:
            created = self.post("/api/pages", {
                "title": "Release note",
                "instruction": ("Write a file named release-note.md announcing version 0.2 of the tool "
                                "in two short bullet points. Use the file tool to write it."),
                "sourcePageId": "home",
            })
            later = created["page"]["id"]
            state["later"] = later
            view, finished = self.settled(later, 0)
            result = {"ok": finished, "workId": later,
                      "pendingActions": [a["toolName"] for a in view["execution"]["pendingActions"]]}
            result.update(self._approve_and_read(later))
            return result

        self.step("later_work_uses_experience", later_work)
        report["steps"] = self.steps
        report["outcome"] = "completed" if all(s.get("ok") for s in self.steps) else "incomplete"
        report["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return report

    def _approve_and_read(self, page_id: str) -> dict:
        result = self.approve_all(page_id)
        files = {}
        for artifact in self.view(page_id)["artifacts"]:
            read = self.get(f"/api/pages/{page_id}/artifacts/{artifact['id']}")
            files[artifact["path"]] = read.get("content") or read.get("artifact", {}).get("preview")
        result["fileContents"] = files
        result["ok"] = bool(files)
        return result


def usage_summary(data_dir: Path | None) -> dict | None:
    """Read recorded provider usage from the Kernel store, read-only."""
    if data_dir is None:
        return None
    import sqlite3
    path = data_dir / "kernel.db"
    if not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        summary: dict[str, Any] = {"tables": sorted(t for t in tables if "usage" in t or "round" in t)}
        for table in summary["tables"]:
            summary[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return summary
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True, help="Private JSON report to create.")
    parser.add_argument("--turn-timeout", type=float, default=900.0, help="Seconds to wait for each reply.")
    parser.add_argument("--data-dir", type=Path, help="Workspace directory, to summarize recorded usage.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output file; reports are never overwritten.")
    journey = Journey(args.url, args.turn_timeout)
    report = journey.run()
    try:
        report["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                          text=True, check=True).stdout.strip()
    except Exception:
        report["commit"] = None
    report["usage"] = usage_summary(args.data_dir)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    args.output.chmod(0o600)
    print(json.dumps({"outcome": report.get("outcome"), "report": str(args.output)}))


if __name__ == "__main__":
    main()
