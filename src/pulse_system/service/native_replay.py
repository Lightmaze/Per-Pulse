"""A bounded, selected-history replay regime inside the original native turn.

Entering changes executable capability, not identity or the meaning of stored
history. The successful native action IS the durable regime boundary. Nothing
creates a new agent, queue, background model or additional turn allowance.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pulse_harness.core import ToolCall, ToolDescriptor, ActionStatus
from pulse_harness.core.errors import ScopeConflict, ProviderProtocolError

NAME = 'replay'
KIND = 'pulse.selected-replay.v1'
REMAINING_ROUNDS = 4
SCHEMA = {'type': 'object', 'properties': {
    'op': {'type': 'string', 'enum': ['enter', 'inspect', 'read']},
    'message_ids': {'type': 'array', 'items': {'type': 'string', 'maxLength': 256},
                    'minItems': 1, 'maxItems': 4, 'uniqueItems': True},
    'message_id': {'type': 'string', 'maxLength': 256},
    'offset': {'type': 'integer', 'minimum': 0},
    'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 4096},
}, 'required': ['op'], 'additionalProperties': False}


def descriptor():
    return ToolDescriptor(NAME,
        'Optionally enter a bounded replay of selected saved experiences, in this same context. '
        'First find actual message IDs with search_history. enter(message_ids) must be the '
        'only tool call in its round. It keeps the current conversation, rather than copying '
        'a persona or creating a task. For the remainder of THIS turn only replay inspect/read '
        'are available; no files, commands, Work, team, local-state changes or live perception. '
        'At most four further ordinary model rounds, within the current turn timeout/budget; '
        'read selected originals with read(message_id,offset?,max_chars?). Consider alternatives '
        'or leave a tentative interpretation; no particular summary or lesson is required. '
        'Finish normally to exit. The next ordinary turn regains its normal tools. Final text '
        'is retained as replay, not a newly observed external event, and does not automatically '
        'propagate along CONTENT edges. Earlier actual actions are not undone. This is not '
        'sleep scheduling, memory isolation, training success or permission for external action.',
        capability='history.replay', source='pulse.native-replay')


def validate(args):
    if not isinstance(args, Mapping):
        raise ValueError('replay arguments must be an object')
    a = dict(args)
    if a == {'op': 'inspect'}:
        return a
    if a.get('op') == 'enter' and set(a) == {'op', 'message_ids'}:
        ids = a['message_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 4 or
                any(not isinstance(x, str) or not x.strip() or len(x) > 256 or '\x00' in x for x in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('select 1..4 distinct actual history message IDs')
        return a
    if a.get('op') == 'read' and {'op', 'message_id'} <= set(a) <= {'op', 'message_id', 'offset', 'max_chars'}:
        mid = a['message_id']
        if not isinstance(mid, str) or not mid.strip() or len(mid) > 256 or '\x00' in mid:
            raise ValueError('invalid replay source ID')
        for key, low, high in [('offset', 0, 2**31-1), ('max_chars', 1, 4096)]:
            if key in a and (type(a[key]) is not int or not low <= a[key] <= high):
                raise ValueError('invalid replay range')
        return a
    raise ValueError('unknown replay operation or fields')


def mode(store, scope):
    """Read canonical successful entry, never a model's text saying 'replay'."""
    for action in store.actions_for_turn(scope):
        if (action.tool_name != NAME or action.arguments.get('op') != 'enter'
                or action.status is not ActionStatus.SUCCEEDED):
            continue
        output = (action.result or {}).get('output', {})
        if (output.get('kind') != KIND or output.get('entry_action_id') != action.action_request_id
                or output.get('source_scope') != scope.as_dict()
                or output.get('message_ids') != action.arguments['message_ids']):
            raise ScopeConflict('replay entry does not match its retained native action')
        return output
    return None


def check_tool(store, scope, name):
    if name != NAME and mode(store, scope) is not None:
        raise ScopeConflict('selected replay admits only its original-source reader until this turn ends')


def execute(provider, scope, call, cancellation, *, action_request_id):
    a = validate(call.arguments)
    provider._replay_authorize(scope, action_request_id, call)
    cancellation.raise_if_cancelled()
    current = mode(provider.store, scope)
    if a['op'] == 'inspect':
        return {'kind': KIND, 'regime': 'replay' if current else 'ordinary', 'entry': current,
                'ends': 'at_original_turn_terminal; no_new_activity_scheduled'}
    if a['op'] == 'enter':
        if current is not None:
            raise ScopeConflict('this turn already entered replay; it cannot renew its allowance')
        latest = provider.store.model_rounds_for_turn(scope, limit=1)[0]
        if latest.tool_call_count != 1:
            raise ScopeConflict('replay entry must be alone, not combined with other actions')
        sources = []
        for mid in a['message_ids']:
            found = provider._history.execute_for_scope(scope, ToolCall(call.tool_call_id,
                'read_history', {'message_id': mid, 'max_chars': 1}), cancellation)
            sources.append(found['source'])
        result = {'kind': KIND, 'regime': 'replay', 'entry_action_id': action_request_id,
                  'source_scope': scope.as_dict(), 'message_ids': a['message_ids'], 'sources': sources,
                  'model_round_ceiling': latest.round_index + 1 + REMAINING_ROUNDS,
                  'remaining_rounds_cap': REMAINING_ROUNDS,
                  'meaning': 'selected_history_reconsideration_not_new_external_observation',
                  'input_boundary': 'original_conversation_retained; only_selected_history_tools',
                  'output_boundary': 'labelled_local_history; no_automatic_CONTENT_propagation',
                  'exit': 'finish_this_turn; no_inline_reenable_of_external_tools'}
        if len(json.dumps(result, ensure_ascii=True).encode()) > provider.store.max_payload_bytes // 2:
            raise ProviderProtocolError('replay entry exceeds configured payload; select fewer sources',
                                        code='replay_sources_too_large')
        return result
    if current is None or a['message_id'] not in current['message_ids']:
        raise ScopeConflict('read requires a selected source in this turn\'s replay')
    result = provider._history.execute_for_scope(scope, ToolCall(call.tool_call_id,
        'read_history', {k: v for k, v in a.items() if k != 'op'}), cancellation)
    expected = current['sources'][current['message_ids'].index(a['message_id'])]
    if result['source'] != expected:
        raise ScopeConflict('selected original source changed; replay does not rebind it')
    result = annotate_history(provider, scope, result)
    result['replay'] = {'entry_action_id': current['entry_action_id'],
                        'meaning': 'historical_material_not_reexecuted'}
    if len(json.dumps(result, ensure_ascii=True).encode()) > provider.store.max_payload_bytes // 2:
        raise ProviderProtocolError('replay passage with origin exceeds payload; request less',
                                    code='history_result_too_large')
    return result


def annotate_history(provider, scope, result):
    """Keep the raw Kernel transcript; label its origin when reading it later."""
    items = result.get('matches', [result])
    scopes = {s.session_id: s for s in provider._history.resolve_scopes(scope)}
    for item in items:
        source = item.get('source', {})
        original = scopes.get(source.get('session_id'))
        if original is None or not source.get('turn_id') or source.get('role') != 'assistant':
            continue
        entry = mode(provider.store, original.for_turn(source['turn_id']))
        if entry is not None:
            item['replay_origin'] = {'kind': KIND, 'entry_action_id': entry['entry_action_id'],
                'selected_sources': entry['sources'],
                'meaning': 'replay_interpretation_not_new_external_observation'}
    # Preserve continuation keys rather than allowing the normal payload bound
    # to cut them off after provenance annotation.
    if len(json.dumps(result, ensure_ascii=True).encode()) > provider.store.max_payload_bytes // 2:
        raise ProviderProtocolError('history with replay origin exceeds payload; request less',
                                    code='history_result_too_large')
    return result


def project_text(store, scope, text):
    entry = mode(store, scope)
    if entry is None:
        return text, 'ordinary'
    return ('[Replay: tentative reconsideration, not a new external observation; '
            'original sources: ' + ', '.join(entry['message_ids']) + ']\n' + text), 'replay'
