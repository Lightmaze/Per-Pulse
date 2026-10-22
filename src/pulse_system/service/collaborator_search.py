"""Read-only discovery of real prior contributions, with scoped current notes.

This is literal, Unicode-normalized all-term retrieval, not an expertise score or
another routing learner. It pages the existing durable records and reuses their
source checks. A page can be empty and still have more history to inspect.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from pulse_harness.core.errors import ScopeConflict
from pulse_system.core.delegation import DURABLE_DELEGATION_MODE

SCAN_LIMIT = 64
RESULT_LIMIT = 8
QUERY_LIMIT = 256
TERM_LIMIT = 8


def folded(value: str) -> str:
    return unicodedata.normalize('NFKC', value).casefold()


def query_terms(query: str) -> tuple[str, ...]:
    if not isinstance(query, str) or not query.strip() or len(query) > QUERY_LIMIT or '\x00' in query:
        raise ValueError('search query must contain 1..256 characters')
    terms = tuple(dict.fromkeys(folded(query).split()))
    if not terms or len(terms) > TERM_LIMIT:
        raise ValueError('search query must contain 1..8 distinct whitespace-separated terms')
    return terms


def matching_fields(fields: dict[str, str], terms: tuple[str, ...]) -> list[str]:
    values = {name: folded(value) for name, value in fields.items()}
    if not all(any(term in value for value in values.values()) for term in terms):
        return []
    return [name for name, value in values.items() if any(term in value for term in terms)]


def excerpt(value: str, terms: tuple[str, ...], limit: int = 640) -> dict[str, Any]:
    """Preserve original text, including when normalization changes its length.

    Find a literal original-text anchor when possible; no normalized character
    position is misrepresented as an original source offset. Fullwidth or other
    normalization-only matches may be outside this preview; inspect reads source.
    """
    positions = [value.find(term) for term in terms if value.find(term) >= 0]
    start = max(0, min(positions) - 100) if positions else 0
    end = min(len(value), start + limit)
    return {'text': value[start:end], 'start': start, 'end': end,
            'truncated': start != 0 or end != len(value)}


def _page(team, owner_id: str, before_record_id: str | None):
    storage = team.runtime.storage
    params: list[Any] = [owner_id, DURABLE_DELEGATION_MODE]
    after = ''
    with storage._lock:
        if before_record_id is not None:
            cursor = storage._conn.execute(
                'SELECT created_at,id FROM delegations WHERE id=? AND caller_id=? AND mode=?',
                (before_record_id, owner_id, DURABLE_DELEGATION_MODE)).fetchone()
            if cursor is None:
                raise ScopeConflict('search cursor is unavailable for this owner')
            after = ' AND (created_at < ? OR (created_at = ? AND id < ?))'
            params.extend((cursor[0], cursor[0], cursor[1]))
        # One extra row tells the caller whether the bounded scan can continue.
        params.append(SCAN_LIMIT + 1)
        rows = storage._conn.execute(
            'SELECT * FROM delegations WHERE caller_id=? AND mode=?' + after +
            ' ORDER BY created_at DESC,id DESC LIMIT ?', params).fetchall()
        return [storage._row_to_delegation(row) for row in rows]


def search(team, owner_id: str, work_id: str | None, query: str,
           before_record_id: str | None, cancellation) -> dict[str, Any]:
    terms = query_terms(query)
    cancellation.raise_if_cancelled()
    known = team._known(owner_id)
    records = _page(team, owner_id, before_record_id)
    matches = []
    scanned = 0
    last_record = None
    for candidate in records[:SCAN_LIMIT]:
        cancellation.raise_if_cancelled()
        scanned += 1
        last_record = candidate['id']
        if candidate['target_id'] not in known or candidate.get('result_summary') is None:
            continue
        try:
            record, _, front = team._record(owner_id, candidate['id'])
            # Also checks the settled result against its actual causal source;
            # an arbitrary legacy row or changed result is not a contribution.
            saved = team.feedback.get(front.id, record['id'])
        except ScopeConflict:
            continue
        except Exception as exc:
            from .runtime import ServiceError
            if isinstance(exc, ServiceError) and exc.error == 'feedback_source_unavailable':
                continue
            raise
        expert = team.runtime.storage.get_engram(record['target_id'])
        if expert is None:
            continue
        current = saved['current']
        applicable = bool(current and not current['withdrawn'] and
                          (current['scope'] == 'collaborator' or front.id == work_id))
        fields = {'expert_name': expert.name or expert.id, 'work_title': front.title,
                  'task': record['task'], 'contribution': record['result_summary']}
        if applicable:
            fields['operator_feedback'] = current['content']
        hit_fields = matching_fields(fields, terms)
        if not hit_fields:
            continue
        note = None
        if applicable:
            note = {'revision': saved['revision'], 'scope': current['scope'],
                    'recorded_by': 'operator', 'excerpt': excerpt(current['content'], terms)}
        status = ('none' if current is None else 'withdrawn' if current['withdrawn'] else
                  'applicable' if applicable else 'outside_current_work')
        matches.append({
            'record_id': record['id'], 'expert_id': expert.id,
            'expert_name': (expert.name or expert.id)[:160],
            'work_id': front.id, 'work_title': front.title[:256],
            'created_at': record['created_at'], 'completed_at': record['completed_at'],
            'source': saved['source'], 'matched_fields': hit_fields,
            'task_excerpt': excerpt(record['task'], terms, 256),
            'contribution_excerpt': excerpt(record['result_summary'], terms),
            'feedback': note,
            'source_feedback_state': {'revision': saved['revision'], 'status': status},
        })
        if len(matches) == RESULT_LIMIT:
            break
    more = len(records) > scanned
    return {'kind': 'collaborator_history_search.v1', 'query': query, 'terms': list(terms),
            'owner_id': owner_id, 'work_id': work_id, 'matches': matches,
            'scanned_records': scanned, 'scan_limit': SCAN_LIMIT, 'result_limit': RESULT_LIMIT,
            'more_history': more, 'next_before_record_id': last_record if more else None,
            'order': 'newest_first', 'matching': 'nfkc_casefold_all_literal_terms',
            'evidence': 'historical_contributions_and_applicable_operator_annotations_not_verified_truth',
            'snapshot': 'current_at_read; inspect and feedback reread before reuse',
            'effect': 'read_only_no_consultation_no_reward_no_memory_write'}
