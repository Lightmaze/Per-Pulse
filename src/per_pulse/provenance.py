"""Verified display origins for internal activity in the page projection.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
This module reads the existing World; it does not add conversation truth.
"""
from __future__ import annotations

from pulse_system.core.types import CausalEventKind, CausalEventSource


def project_origin(runtime, row: dict) -> dict | None:
    """Return display overrides, or None for an empty internal activation.

    Ordinary rows return an empty mapping, including human-selected returns
    and text that merely resembles an internal notice. Callers can retain
    their existing human-authored navigation metadata for those rows.
    """
    event = runtime.causal_ledger.get_event(row["event_id"])
    if (event is None or event.world_id != runtime.world_id
            or event.engram_id != row["engram_id"]
            or event.center_id != row["center_id"] or event.content != row["content"]):
        return {}
    attention = getattr(runtime.harness, "attention", None)
    attention_key = getattr(attention, "key", None)
    if (event.kind is CausalEventKind.SPONTANEOUS and event.source is CausalEventSource.SELF
            and not event.content and isinstance(attention_key, str)
            and event.metadata.get("context_body") == attention_key):
        return None
    work = getattr(runtime, "_native_work", None)
    if work is None or event.engram_id is None:
        return {}
    origin = work.report_origin(event.id, event.idempotency_key, event.engram_id,
                                event.content, require_settled_source=True)
    if origin is None:
        return {}
    source = runtime.causal_ledger.get_turn_for_world(origin["source_turn_id"], runtime.world_id)
    if source is None or not source.result_event_id:
        return {}
    return {"role": "system", "kind": "work_report", "content": origin["note"],
            "sourcePageId": origin["work_id"], "sourceMessageId": source.result_event_id}
