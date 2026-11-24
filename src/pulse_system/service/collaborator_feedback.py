"""Explicit, revisable operator feedback attached to an actual contribution.

Stored in the existing World component store, not a personality prompt or a
learning reward. Only the authenticated product API writes. Native models read
current, applicable annotations through a real team tool result. Withdrawing a
note does not erase its audit history or texts already seen by an in-flight call.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from pulse_harness.core.errors import ScopeConflict
from pulse_system.core.types import CausalEventStatus

PREFIX = 'collaborator-feedback-v1:'
MAX_RECORDS = 128
MAX_REVISIONS = 32


def _fault(code: str, detail: str, status: int = 409):
    from .runtime import ServiceError
    return ServiceError(code, detail, 'read the current contribution feedback before saving again', status=status)


class CollaboratorFeedback:
    def __init__(self, runtime):
        self.runtime = runtime

    def _source(self, work_id: str, record_id: str) -> dict[str, Any]:
        r = self.runtime
        front = r.world.get_task_front(work_id)
        if front is None:
            raise _fault('feedback_source_unavailable', 'the original Work is unavailable', 404)
        try:
            record, request, original = r._native_team._record(front.focal_engram_id, record_id)
        except ScopeConflict as exc:
            raise _fault('feedback_source_unavailable', 'the contribution does not belong to this Work', 404) from exc
        if (original.id != front.id or request.status is not CausalEventStatus.SETTLED
                or record.get('result_summary') is None):
            raise _fault('feedback_source_unavailable', 'a completed contribution in this exact Work is required', 404)
        with r.storage._lock:
            rows = r.storage._conn.execute(
                "SELECT id,content FROM causal_events WHERE world_id=? AND parent_event_id=? "
                "AND kind='assistant_result' AND status='settled' LIMIT 2",
                (r.world_id, request.id)).fetchall()
        if len(rows) != 1 or rows[0][1] != record['result_summary']:
            raise _fault('feedback_source_unavailable', 'the retained result does not match this contribution', 404)
        return {'world_id': r.world_id, 'owner_id': record['caller_id'],
                'expert_id': record['target_id'], 'work_id': front.id,
                'record_id': record_id, 'request_event_id': request.id,
                'result_event_id': rows[0][0]}

    def _key(self, source: Mapping[str, Any]) -> str:
        return PREFIX + hashlib.sha256(json.dumps(dict(source), sort_keys=True).encode()).hexdigest()

    def get(self, work_id: str, record_id: str) -> dict[str, Any]:
        source = self._source(work_id, record_id)
        entry = self.runtime.storage.load_component_state(self._key(source))
        if entry is not None and entry.get('source') != source:
            raise _fault('feedback_source_changed', 'stored feedback no longer matches its retained source')
        return {'source': source, 'revision': 0 if entry is None else entry['revision'],
                'current': None if entry is None else entry['current'],
                'history': [] if entry is None else entry['history'],
                'max_revisions': MAX_REVISIONS}

    def put(self, work_id: str, record_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        r = self.runtime
        if not isinstance(body, Mapping) or set(body) != {'expected_revision', 'content', 'scope', 'withdrawn'}:
            raise _fault('feedback_invalid', 'expected_revision, content, scope and withdrawn are required', 400)
        expected, content, extent, withdrawn = (body[k] for k in ('expected_revision','content','scope','withdrawn'))
        if (type(expected) is not int or not 0 <= expected <= MAX_REVISIONS + 1
                or not isinstance(content, str) or len(content) > 2048 or '\x00' in content
                or not isinstance(extent, str) or extent not in {'work', 'collaborator'}
                or type(withdrawn) is not bool or (not withdrawn and not content.strip())):
            raise _fault('feedback_invalid', 'invalid feedback revision, text, scope or withdrawal', 400)
        if not r.harness._allow_mutations:
            raise _fault('feedback_readonly', 'this runtime is read-only', 403)
        fence = r._current_runtime_fence()
        # One existing World lock/transaction checks revision and owner together.
        # No second store, background runner or model call is opened here.
        with r.storage._lock, r.storage._immediate_transaction() as conn:
            r.storage._assert_runtime_publication()
            now = datetime.now(timezone.utc)
            r.storage._assert_runtime_lease_uncommitted(fence.owner_id, fence.epoch, now, conn)
            source = self._source(work_id, record_id)
            key = self._key(source)
            row = conn.execute('SELECT state FROM component_state WHERE component=?', (key,)).fetchone()
            entry = json.loads(row[0]) if row else None
            if entry is not None and entry.get('source') != source:
                raise _fault('feedback_source_changed', 'feedback source changed')
            revision = entry['revision'] if entry else 0
            desired = {'content': content, 'scope': extent, 'withdrawn': withdrawn}
            current = entry['current'] if entry else None
            # Retry of the same last write is safe after a lost HTTP receipt.
            if revision == expected + 1 and current and all(current[k] == v for k,v in desired.items()):
                return self.get(work_id, record_id)
            if revision != expected:
                raise _fault('feedback_revision_conflict', 'feedback changed elsewhere; your draft has not overwritten it')
            if revision >= MAX_REVISIONS and not withdrawn:
                raise _fault('feedback_revision_limit', 'this retained annotation has reached its revision bound')
            if withdrawn and (current is None or current['withdrawn']):
                raise _fault('feedback_not_active', 'there is no active feedback to withdraw')
            if withdrawn and (content != current['content'] or extent != current['scope']):
                raise _fault('feedback_invalid', 'withdraw the saved version without rewriting its content or range', 400)
            if entry is None:
                count = conn.execute("SELECT COUNT(*) FROM component_state WHERE component GLOB ? "
                    "AND json_extract(state,'$.source.owner_id')=?", (PREFIX+'*',source['owner_id'])).fetchone()[0]
                if count >= MAX_RECORDS:
                    raise _fault('feedback_capacity', 'retained collaboration feedback capacity reached')
            change = {**desired, 'revision': revision+1, 'recorded_at': now.isoformat(), 'recorded_by': 'operator'}
            result = {'version':1, 'source':source, 'revision':revision+1,
                      'current': change, 'history': [*(entry['history'] if entry else []), change]}
            conn.execute('INSERT INTO component_state(component,state,updated_at) VALUES(?,?,?) '
                'ON CONFLICT(component) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at',
                (key, json.dumps(result, ensure_ascii=False, allow_nan=False), now.isoformat()))
        return {'source':source, 'revision':result['revision'], 'current':change,
                'history':result['history'], 'max_revisions':MAX_REVISIONS}

    def applicable(self, owner_id: str, work_id: str | None, expert_id: str | None = None) -> dict[str, Any]:
        r = self.runtime
        params: list[Any] = [PREFIX+'*', owner_id]
        clauses = ["component GLOB ?", "json_extract(state,'$.source.owner_id')=?",
                   "json_extract(state,'$.current.withdrawn')=0"]
        if expert_id is not None:
            clauses.append("json_extract(state,'$.source.expert_id')=?"); params.append(expert_id)
        clauses.append("(json_extract(state,'$.current.scope')='collaborator' OR json_extract(state,'$.source.work_id')=?)")
        params.append(work_id)
        with r.storage._lock:
            rows = r.storage._conn.execute('SELECT state FROM component_state WHERE '+ ' AND '.join(clauses)
                +' ORDER BY updated_at DESC,component LIMIT 129', params).fetchall()
        notes = []
        for row in rows[:MAX_RECORDS]:
            entry = json.loads(row[0]); source = entry['source']
            # Never turn an orphan, reattributed or changed old result into current guidance.
            try:
                if self._source(source['work_id'], source['record_id']) != source: continue
            except Exception as exc:
                from .runtime import ServiceError
                if isinstance(exc, ServiceError) and exc.error == 'feedback_source_unavailable': continue
                raise
            notes.append({'source':source, **entry['current']})
        return {'feedback': notes, 'owner_id':owner_id, 'work_id':work_id,
                'expert_id':expert_id, 'more':len(rows)>MAX_RECORDS,
                'kind':'operator_annotations_not_verified_truth',
                'scope_rule':'work_only_or_same_collaborator_with_same_owner',
                'snapshot':'current_at_read; older model/tool transcripts are not erased',
                'affects_rewards':False}
