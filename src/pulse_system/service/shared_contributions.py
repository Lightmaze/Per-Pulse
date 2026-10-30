"""Explicitly share selected peer results through an existing consultation.

Sharing a completed contribution grants a read of that selected result, not the
author's transcript, another owner's material, feedback or execution authority.
"""
from __future__ import annotations

from typing import Any

from pulse_harness.core.errors import ScopeConflict

from .report_contributions import verify_for_delivery as verify_quotes


def verify_for_delivery(runtime, record: dict[str, Any]) -> None:
    verify_quotes(runtime, record['subject'], record['arguments']['contributions'],
        record.get('shared_contributions'))


def selected_for_inspection(runtime, record: dict[str, Any], record_id: str) -> dict[str, Any]:
    """Bind the live read to one explicitly admitted, unchanged source."""
    selections = record['arguments'].get('contributions', [])
    snapshots = record.get('shared_contributions', [])
    selected = [s for s in selections if s['record_id'] == record_id]
    saved = [s for s in snapshots if s.get('source', {}).get('record_id') == record_id]
    if len(selected) != 1 or len(saved) != 1:
        raise ScopeConflict('this contribution was not shared with the current consultation')
    verify_quotes(runtime, record['subject'], selected, saved)
    return saved[0]


def retained(runtime, owner_id: str, request) -> list[dict[str, Any]] | None:
    """Expose what this exact consultation was asked to review, as history."""
    from .prior_contribution import consultation_content
    records = runtime.storage.native_team_requests(subject=owner_id,
        delegation_event_id=request.id, limit=2)
    if len(records) != 1:
        return None
    record = records[0]
    arguments = record.get('arguments', {})
    saved = record.get('shared_contributions')
    selected_target = (arguments.get('expert_id') if arguments.get('op') == 'consult'
                       else (record.get('preference') or {}).get('selected_expert_id'))
    if (arguments.get('op') not in {'consult', 'consult_adaptive'}
            or 'contributions' not in arguments
            or not isinstance(saved, list) or len(saved) != len(arguments['contributions'])
            or record.get('world_id') != runtime.world_id
            or selected_target != request.metadata.get('admitted_target_id')
            or any(s.get('source', {}).get('record_id') != a['record_id']
                or s.get('source', {}).get('owner_id') != owner_id
                for a, s in zip(arguments['contributions'], saved))
            or consultation_content(record) != request.content):
        return None
    identity = runtime.delegation_tunnel.identity_for(
        runtime.world_id, owner_id, record['center_id'], record['request_id'])
    return saved if identity.request_event_id == request.id else None
