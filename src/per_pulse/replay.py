"""Readable provenance over the existing native selected-history regime.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No replay is started by observation, and no second history is maintained here.
"""
from __future__ import annotations

from pulse_harness.adapters.history import source_identity
from pulse_harness.core.errors import ScopeConflict
from pulse_system.service import native_replay


RETURN_PREFIX = "[Selected from a replay interpretation; not a new external observation.]\n"


class PageReplay:
    def __init__(self, pages):
        self.pages = pages

    def _sources(self, page_id, entry):
        pages = self.pages
        runtime = pages.runtime
        scopes = {scope.session_id: scope for scope in pages.execution._history_scopes(
            pages.execution._scope(page_id),
        )}
        sources = []
        for selected in entry["sources"]:
            scope = scopes.get(selected.get("session_id"))
            if scope is None:
                continue
            try:
                row = runtime.kernel_service.store.read_transcript_message(scope, selected["message_id"])
            except ScopeConflict:
                continue
            if row is None or source_identity(row) != selected:
                continue
            turn = runtime.causal_ledger.get_turn_for_world(row["scope"].turn_id, runtime.world_id)
            if turn is None or turn.engram_id != scope.engram_id:
                continue
            message_id = turn.result_event_id if row["role"] == "assistant" else turn.event_id
            if not message_id:
                continue
            # The source must also be a real visible message in this page's
            # committed lineage. Broader team recall is not page authority.
            messages = pages._rows(page_id, limit=1, event_id=message_id)
            if not messages or messages[0]["role"] != row["role"]:
                continue
            source = {"pageId": page_id, "messageId": message_id, "role": row["role"]}
            if source not in sources:
                sources.append(source)
        return sources

    def message(self, page_id, row, origin):
        source_page, source_message = page_id, row["event_id"]
        selected = False
        if row["role"] == "assistant" and row["metadata"].get("execution_regime") == "replay":
            entry = native_replay.result_origin(self.pages.runtime, source_message)
            prefix = native_replay.text_prefix(entry) if entry else ""
        elif (origin.get("pageId") and origin.get("messageId")
                and row["content"].startswith(RETURN_PREFIX)):
            source_page, source_message = origin["pageId"], origin["messageId"]
            if not self.pages._rows(source_page, limit=1, event_id=source_message):
                return None
            entry = native_replay.result_origin(self.pages.runtime, source_message)
            prefix, selected = RETURN_PREFIX, True
        else:
            return None
        if entry is None:
            return None
        sources = self._sources(source_page, entry)
        return {"sources": sources, "unavailableSources": len(entry["sources"]) - len(sources),
                "selected": selected,
                "contentOffset": len(prefix) if row["content"].startswith(prefix) else 0}
