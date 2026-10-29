"""Selected collaborator evidence travels with a Work synthesis, not instead of it.

Quotes remain exact snapshots of source-verified completed contributions. This
module does not decide agreement, certify conclusions or persist another report.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from pulse_harness.core.errors import ScopeConflict

MAX_CITATIONS = 8
MAX_EXCERPT = 1024
CITATION_SCHEMA = {"type": "array", "minItems": 1, "maxItems": MAX_CITATIONS,
    "items": {"type": "object", "properties": {
        "record_id": {"type": "string", "minLength": 1, "maxLength": 200},
        "excerpt": {"type": "string", "minLength": 1, "maxLength": MAX_EXCERPT}},
        "required": ["record_id", "excerpt"], "additionalProperties": False}}


def validate_citations(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_CITATIONS:
        raise ValueError("contributions requires 1..8 selected source quotes")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"record_id", "excerpt"}:
            raise ValueError("each contribution requires record_id and exact excerpt")
        item = dict(item)
        for field, maximum in (("record_id", 200), ("excerpt", MAX_EXCERPT)):
            text = item[field]
            if not isinstance(text, str) or not text.strip() or len(text) > maximum or "\x00" in text:
                raise ValueError("invalid contribution " + field)
        if item["record_id"] in seen:
            raise ValueError("select each contribution once; combine the relevant excerpt")
        seen.add(item["record_id"])
        result.append(item)
    return result


def capture(runtime, subject: str, selections: Any) -> list[dict[str, Any]]:
    """Read one coherent selection with the existing re-entrant World lock."""
    with runtime.storage._lock:
        return _capture(runtime, subject, selections)


def _capture(runtime, subject: str, selections: Any) -> list[dict[str, Any]]:
    """Reuse the same ownership, settled-result and immutable-author source reader."""
    from .runtime import ServiceError
    quotes = []
    for item in validate_citations(selections):
        try:
            record, _, front = runtime._native_team._record(subject, item["record_id"])
            source = runtime._native_team.feedback._source(front.id, record["id"])
        except (ScopeConflict, ServiceError) as exc:
            raise ScopeConflict("report contribution source is unavailable") from exc
        result = record["result_summary"]
        start = result.find(item["excerpt"])
        if start < 0:
            raise ValueError("report excerpt must be exact text from the selected contribution")
        expert = runtime.storage.get_engram(record["target_id"])
        if expert is None:
            raise ScopeConflict("report contribution author is unavailable")
        quotes.append({"source": source, "expert_name": expert.name or expert.id,
            "work_title": front.title, "excerpt": item["excerpt"], "start": start,
            "end": start + len(item["excerpt"]),
            "result_digest": hashlib.sha256(result.encode()).hexdigest(),
            "evidence": "collaborator_output_not_verified_truth"})
    return quotes


def verify_for_delivery(runtime, subject: str, selections: Any, saved: Any) -> None:
    """Recheck before first publication; already delivered history stays unchanged.

Renaming an author or a Work does not change the retained label captured when the
report was composed. Altered original results or attribution cannot be delivered.
"""
    current = capture(runtime, subject, selections)
    if not isinstance(saved, list) or len(current) != len(saved):
        raise ScopeConflict("report contribution snapshot changed")
    for old, new in zip(saved, current):
        if any(old.get(k) != new[k] for k in ("source", "excerpt", "start", "end", "result_digest", "evidence")):
            raise ScopeConflict("report contribution snapshot changed before delivery")
