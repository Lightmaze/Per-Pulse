"""Read the natural Home conversation without flattening Work into it.

This projection uses admitted causal facts and committed generation ancestry.
It never rewrites history, opens a model session, or enqueues recalled text.
The older all-Engram inspector remains available for unattributed imports.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pulse_system.service.runtime import ServiceError

if TYPE_CHECKING:
    from pulse_system.service.runtime import RuntimeService


def home_conversation(runtime: RuntimeService, engram_id: str, *,
                      before_seq: int | None = None, limit: int = 100) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ServiceError("home_page_invalid", "limit must be between 1 and 200",
                           "use a bounded page size", status=400)
    if before_seq is not None and (type(before_seq) is not int or not 1 <= before_seq < 2**63):
        raise ServiceError("home_page_invalid", "before_seq must be a positive integer",
                           "use next_before_seq from the previous page", status=400)
    if runtime.kernel_service is None:
        raise ServiceError("home_context_unavailable", "this runtime has no native Home context",
                           "use the existing Engram inspector for this adapter", status=409)
    head = runtime.storage.get_engram(engram_id)
    if head is None:
        raise ServiceError("unknown_engram", "the requested Engram does not exist",
                           "select a current conversation", status=404)
    ledger = runtime.causal_ledger
    lineage = ledger.committed_history_lineage(runtime.world_id, engram_id)
    has_home_input = (
        "EXISTS (SELECT 1 FROM harness_turns t "
        "WHERE t.event_id=e.id AND t.input_message_id IS NOT NULL)"
    )
    # A selected report is already admitted evidence after its source Work
    # succeeds. It can be read while Home awaits its normal execution slot.
    # Select candidates before LIMIT so pagination keeps the original event
    # sequence. report_origin below must still verify their exact provenance.
    # Do not bring a predecessor's unprocessed queue into the new Home.
    queued_report = (
        "e.engram_id = ? AND e.kind='stimulus' AND e.source='self' "
        "AND e.center_id IS NULL AND e.idempotency_key GLOB 'native-work-v1:*' "
        "AND EXISTS (SELECT 1 FROM component_state r "
        "WHERE r.component=e.idempotency_key "
        "AND json_extract(r.state,'$.arguments.op')='report' "
        "AND json_extract(r.state,'$.world_id')=e.world_id "
        "AND json_extract(r.state,'$.subject')=e.engram_id "
        "AND json_extract(r.state,'$.source_turn_id')="
        "json_extract(e.metadata,'$.stimulus_source_turn_id'))"
    )
    clauses = [
        "e.world_id = ?",
        "e.engram_id IN (" + ",".join("?" for _ in lineage) + ")",
        "e.content IS NOT NULL",
        "e.kind IN ('stimulus','spontaneous','propagation','assistant_result')",
        "(e.center_id IS NULL OR EXISTS (SELECT 1 FROM activity_centers c "
        "WHERE c.id=e.center_id AND c.kind <> 'task'))",
        "e.domain <> 'generation'",
        # Internal return material stays in the causal ledger and native tool
        # history, not as an invented human bubble or duplicate draft in Home.
        "NOT (e.source='self' AND COALESCE(e.idempotency_key,'') "
        "GLOB 'native-cognition-return-v1:*')",
        "NOT EXISTS (SELECT 1 FROM causal_events parent WHERE parent.id=e.parent_event_id "
        "AND parent.world_id=e.world_id AND parent.domain='generation')",
        f"(e.status <> 'queued' OR {has_home_input} OR ({queued_report}))",
    ]
    params: list[Any] = [runtime.world_id, *lineage, engram_id]
    if before_seq is not None:
        clauses.append("e.seq < ?")
        params.append(before_seq)
    params.append(limit + 1)
    with runtime.storage._lock:
        rows = runtime.storage._conn.execute(
            "SELECT e.seq,e.id,e.engram_id,e.center_id,e.kind,e.source,e.status,"
            f"e.content,e.metadata,e.created_at,e.idempotency_key,{has_home_input} "
            "FROM causal_events e WHERE "
            + " AND ".join(clauses) + " ORDER BY e.seq DESC LIMIT ?", params,
        ).fetchall()
    origins = {}
    # The lookahead row must also be verified before it can justify a cursor.
    for row in rows:
        awaiting_input = row[6] == "queued" and not row[11]
        report = runtime._native_work.report_origin(
            row[1], row[10], row[2], row[7], require_settled_source=awaiting_input,
        )
        if awaiting_input and report is None:
            # Never turn a failed report verification into a human bubble,
            # or silently drop a selected row and misrepresent this page.
            raise ServiceError("home_report_unavailable", "a queued Work report has invalid source evidence",
                               "inspect the original Work and its delivery record", status=409)
        origins[row[1]] = report
    more = len(rows) > limit
    selected = rows[:limit]
    messages = []
    for row in reversed(selected):
        metadata = json.loads(row[8])
        source = metadata.get("source_engram_id")
        source = source if isinstance(source, str) and source else None
        role = "assistant" if row[4] == "assistant_result" else (
            "injection" if row[5] == "propagation" else "user")
        report = origins[row[1]]
        report_fields = ({"source": "self", "metadata": {"work_report": {
            **report, "home_status": row[6],
        }}}
                         if report is not None else {})
        messages.append({
            "seq": row[0], "event_id": row[1], "engram_id": row[2],
            "role": role, "content": row[7], "timestamp": row[9],
            "source_engram_id": source, "status": row[6], **report_fields,
        })
    return {"engram": runtime._engram_view(head), "context": "home", "messages": messages,
            "next_before_seq": selected[-1][0] if more else None,
            "coverage": "admitted_home_causal_history", "internal_cognition_inputs_included": False,
            "queued_current_work_reports_included": True, "includes_committed_ancestors": True}
