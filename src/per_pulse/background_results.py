"""Resolve a completed background answer from its exact native turn.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulse_system.service.runtime import RuntimeService


def background_result_message(runtime: RuntimeService, event_id: str,
                              center_id: str | None) -> str | None:
    """Return an original answer candidate; the caller still checks page lineage.

    Never select the latest message in a Work. An empty spontaneous input and
    its eventual answer have different causal identities.
    """
    ledger = runtime.causal_ledger
    with runtime.storage._lock:
        event = ledger.get_event(event_id)
        if (event is None or event.world_id != runtime.world_id
                or event.center_id != center_id or event.status.value != 'settled'
                or event.kind.value not in {'spontaneous', 'propagation'}):
            return None
        row = runtime.storage._conn.execute(
            'SELECT id FROM harness_turns WHERE event_id=? AND engram_id=? '
            'ORDER BY rowid DESC LIMIT 1', (event.id, event.engram_id),
        ).fetchone()
        if row is None:
            return None
        turn = ledger.get_turn_for_world(row[0], runtime.world_id)
        contexts = {f'work:{center_id}'} if center_id is not None else {'', 'home'}
        if (turn is None or turn.event_id != event.id or turn.engram_id != event.engram_id
                or turn.state.value != 'settled' or turn.prompt_accepted is not True
                or turn.execution_context not in contexts or not turn.result_event_id
                or turn.error_code is not None):
            return None
        answer = ledger.get_event(turn.result_event_id)
        if (answer is None or answer.parent_event_id != event.id
                or answer.world_id != event.world_id or answer.engram_id != event.engram_id
                or answer.center_id != event.center_id or answer.causal_id != event.causal_id
                or answer.kind.value != 'assistant_result' or answer.source.value != 'self'
                or answer.status.value != 'settled' or not answer.content
                or answer.metadata.get('execution_regime') == 'replay'):
            return None
        return answer.id
