"""Explicit authored long-conversation fixture on the native offline service.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No model transport or generated assistant replies are used by this fixture.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PageService


def seed(directory: Path) -> dict:
    manifest = directory / "history-fixture.json"
    if manifest.exists():
        return json.loads(manifest.read_text())
    pages = PageService(directory, model_factory=None)
    try:
        work = pages.create_page("Field notebook", "The original field note. [Authored history fixture]")
        sibling = pages.create_page("Sibling notes", "SIBLING_ONLY_PRIVATE_HISTORY")
        work_id, sibling_id = work["page"]["id"], sibling["page"]["id"]
        ids = [work["initialMessageId"]]
        for index in range(1120):
            ids.append(pages.runtime.send_task_front_message(
                work_id, f"Field note {index:04d}: preserve this original observation. [Authored history fixture]",
            ))
        linked = ids[12]
        pages.add_reference(work_id, sibling_id, linked, "Sibling notes")
        for index in range(510):
            pages.runtime.inject(pages.runtime.continuity_engram_id,
                                 f"HOME_ONLY_HISTORY {index}", source="user")
        result = {"workId": work_id, "siblingId": sibling_id, "messageIds": ids, "linkedMessageId": linked}
        manifest.write_text(json.dumps(result))
        return result
    finally:
        pages.runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    manifest = seed(args.data_dir)
    app = create_app(args.data_dir, model_factory=None)

    @app.get("/__history/fixture")
    def fixture():
        return manifest

    # Keep this test-only inspection route ahead of the frontend catch-all.
    app.router.routes.insert(0, app.router.routes.pop())

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
