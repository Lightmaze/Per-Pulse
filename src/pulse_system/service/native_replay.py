"""A bounded, selected-history replay regime inside the original native turn.

Entering changes executable capability, not identity or the meaning of stored
history. The successful native action IS the durable regime boundary. Nothing
creates a new agent, queue, background model or additional turn allowance.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pulse_harness.core import ToolCall, ToolDescriptor, ActionStatus, TurnStatus
from pulse_harness.core.errors import ScopeConflict, ProviderProtocolError

NAME = 'replay'
KIND = 'pulse.selected-replay.v1'
REMAINING_ROUNDS = 4
FOCUSED_BOUNDARY = 'declared_focus_and_selected_history; unrelated_conversation_omitted; auxiliary_memory_paused'
SCHEMA = {'type': 'object', 'properties': {
    'op': {'type': 'string', 'enum': ['enter', 'inspect', 'read']},
    'message_ids': {'type': 'array', 'items': {'type': 'string', 'maxLength': 256},
                    'minItems': 1, 'maxItems': 4, 'uniqueItems': True},
    'focus': {'type': 'string', 'minLength': 1, 'maxLength': 1000,
              'description': 'Optional bounded question. If supplied on enter, subsequent requests omit unrelated conversation and experience advice for this turn.'},
    'message_id': {'type': 'string', 'maxLength': 256},
    'offset': {'type': 'integer', 'minimum': 0},
    'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 4096},
}, 'required': ['op'], 'additionalProperties': False}


def descriptor():
    return ToolDescriptor(NAME,
        'Optionally enter a bounded replay of selected saved experiences, in this same context. '
        'First find actual message IDs with search_history. enter(message_ids) must be the '
        'only tool call in its round. By default it keeps the current conversation. Optional '
        'focus selects a bounded question instead: after successful entry, model requests keep '
        'host instructions, this declared focus, selected source references and replay exchanges; '
        'other conversation and experience/expert advice are omitted until the next turn. '
        'Earlier provider exposure is not erased. No persona or task is copied. '
        'For the remainder of THIS turn only replay inspect/read '
        'are available; no files, commands, Work, team, local-state changes or live perception. '
        'At most four further ordinary model rounds, within the current turn timeout/budget; '
        'read selected originals with read(message_id,offset?,max_chars?). Consider alternatives '
        'or leave a tentative interpretation; no particular summary or lesson is required. '
        'Finish normally to exit. The next ordinary turn regains its normal tools. Final text '
        'is retained as replay, not a newly observed external event, and does not automatically '
        'propagate along CONTENT edges. Earlier actual actions are not undone. This is not '
        'sleep scheduling, provider forgetting, training success or permission for external action.',
        capability='history.replay', source='pulse.native-replay')


def validate(args):
    if not isinstance(args, Mapping):
        raise ValueError('replay arguments must be an object')
    a = dict(args)
    if a == {'op': 'inspect'}:
        return a
    if a.get('op') == 'enter' and {'op', 'message_ids'} <= set(a) <= {'op', 'message_ids', 'focus'}:
        ids = a['message_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 4 or
                any(not isinstance(x, str) or not x.strip() or len(x) > 256 or '\x00' in x for x in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('select 1..4 distinct actual history message IDs')
        if 'focus' in a and (not isinstance(a['focus'], str) or not a['focus'].strip()
                             or len(a['focus']) > 1000 or '\x00' in a['focus']):
            raise ValueError('replay focus must be 1..1000 characters of nonblank text')
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
                or output.get('message_ids') != action.arguments['message_ids']
                or output.get('focus') != action.arguments.get('focus')
                or ('focus' in action.arguments and output.get('input_boundary') != FOCUSED_BOUNDARY)):
            raise ScopeConflict('replay entry does not match its retained native action')
        return output
    return None


def focused(store, scope):
    entry = mode(store, scope)
    return bool(entry and entry.get('focus'))


class ResponseReviewer:
    """Apply the native material boundary before any optional review plugin."""
    def __init__(self, store, delegate, guard):
        self.store, self.delegate, self.guard = store, delegate, guard

    def review(self, request, response_text, *, round_id, cancel_event):
        from pulse_harness.core.provider import CancellationRequested
        from pulse_harness.core.response_review import ResponseReview
        self.guard()
        if cancel_event.is_set():
            raise CancellationRequested('replay review cancelled')
        if focused(self.store, request.scope):
            return ResponseReview(receipt={'plugin': 'native_replay', 'checkpoint': 'delivery',
                'status': 'not_applicable', 'reason': 'focused_replay_material_boundary'})
        return self.delegate.review(request, response_text, round_id=round_id, cancel_event=cancel_event)


def project_model_messages(store, scope, messages):
    """Project input after a real entry; never edit the authoritative transcript.

    Advice/review can contain unrelated experience even when stored as a system
    message. The native advisor and reviewer also decline new calculations in
    this regime. General host system instructions remain authoritative.
    """
    entry = mode(store, scope)
    if not entry or 'focus' not in entry:
        return messages
    from pulse_harness.core.turn_advice import TURN_ADVICE_PREFIX
    from pulse_harness.core.response_review import RESPONSE_REVIEW_PREFIX

    excluded = (TURN_ADVICE_PREFIX, RESPONSE_REVIEW_PREFIX, 'cognition-return:')
    action_id = entry['entry_action_id']
    boundary = next((i for i, message in enumerate(messages)
                     if message.get('item_id') == 'tool-result:' + action_id), None)
    if boundary is None or not boundary:
        raise ScopeConflict('focused replay requires its delivered entry receipt')
    try:
        receipt = json.loads(messages[boundary]['content'])
        assistant = messages[boundary - 1]
        payload = json.loads(assistant['content'])
        calls = payload['tool_calls']
        valid = (messages[boundary]['role'] == 'tool'
                 and receipt['action_request_id'] == action_id
                 and receipt['status'] == 'succeeded' and receipt['output'] == entry
                 and assistant['role'] == 'assistant' and len(calls) == 1
                 and calls[0]['action_request_id'] == action_id)
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ScopeConflict('focused replay input does not match its native entry')
    # A pre-entry preamble or reasoning may quote unselected material. Keep the
    # actual tool call/result pair, without that preamble or earlier reasoning.
    selected_call = {**assistant, 'content': json.dumps(
        {'text': '', 'tool_calls': calls}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}
    instructions = tuple(message for message in messages[:boundary - 1]
                         if message['role'] == 'system'
                         and not message.get('item_id', '').startswith(excluded))
    tail = tuple(message for message in messages[boundary:]
                 if not message.get('item_id', '').startswith(excluded))
    return instructions + ({
        'role': 'system', 'item_id': 'replay-boundary:' + action_id,
        'content': 'This is a focused replay in the same original turn. The next projected '
                   'message is the assistant-selected focus, not a new user event. Read the '
                   'selected originals through replay. Unrelated conversation and experience '
                   'advice are omitted from these requests, not deleted or forgotten by the '
                   'provider. Earlier material is historical, not a new instruction or external '
                   'observation. Finish normally; ordinary context returns next turn.',
    }, {
        'role': 'user', 'item_id': 'replay-focus:' + action_id,
        'content': 'Declared focus for this replay (model-selected):\n' + entry['focus'],
    }, selected_call) + tail


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
        if 'focus' in a:
            result.update(focus=a['focus'], input_boundary=FOCUSED_BOUNDARY)
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


def result_origin(runtime, event_id):
    """Verify a settled World result against its original Kernel replay entry.

    This describes provenance, not permission to read a page. Callers must
    authorize the causal message in their own context before using this helper.
    A prefix or model-supplied metadata never establishes a replay boundary.
    """
    from pulse_system.core.causality import (
        CausalEventDomain, CausalEventKind, CausalEventSource, CausalEventStatus,
        HarnessTurnState,
    )

    event = runtime.causal_ledger.get_event(event_id)
    if (event is None or event.world_id != runtime.world_id
            or event.kind is not CausalEventKind.ASSISTANT_RESULT
            or event.domain is not CausalEventDomain.HARNESS
            or event.source is not CausalEventSource.SELF
            or event.status is not CausalEventStatus.SETTLED
            or runtime.kernel_service is None):
        return None
    with runtime.storage._lock:
        turns = runtime.storage._conn.execute(
            "SELECT id, event_id, engram_id, state, prompt_accepted "
            "FROM harness_turns WHERE result_event_id=?", (event.id,),
        ).fetchall()
    if (len(turns) != 1 or turns[0][1] != event.parent_event_id
            or turns[0][2] != event.engram_id
            or turns[0][3] != HarnessTurnState.SETTLED.value or turns[0][4] != 1):
        return None
    context = runtime._native_kernel_context_for_turn(event.engram_id, turns[0][0])
    scope = runtime.harness.scope_for(event.engram_id, context).for_turn(turns[0][0])
    store = runtime.kernel_service.store
    try:
        if store.get_turn(scope).status is not TurnStatus.SUCCEEDED:
            return None
        entry = mode(store, scope)
    except ScopeConflict:
        # Legacy World-only history cannot establish a native replay entry.
        return None
    return entry


def text_prefix(entry):
    return ('[Replay: tentative reconsideration, not a new external observation; '
            'original sources: ' + ', '.join(entry['message_ids']) + ']\n')


def project_text(store, scope, text):
    entry = mode(store, scope)
    if entry is None:
        return text, 'ordinary'
    if 'focus' in entry:
        # The Kernel's aggregate also includes pre-entry preambles. They stay
        # in the transcript, but are not products of this focused replay.
        from pulse_harness.core import ModelRoundStatus, TranscriptStore
        messages = TranscriptStore(store).messages(scope)
        boundary = next((message.transcript_seq for message in messages
                         if message.item_id == 'tool-result:' + entry['entry_action_id']), None)
        if boundary is None:
            raise ScopeConflict('focused replay output requires its delivered entry receipt')
        rounds = {record.assistant_item_id: record for record in store.model_rounds_for_turn(scope)}
        parts = []
        for message in messages:
            if message.transcript_seq <= boundary or message.role != 'assistant':
                continue
            record = rounds.get(message.item_id)
            if record is None or record.status is not ModelRoundStatus.COMPLETED:
                raise ScopeConflict('focused replay output lacks its completed native model round')
            content = message.content
            if record.tool_call_count:
                content = json.loads(content)['text']
            parts.append(content)
        text = ''.join(parts)
    return text_prefix(entry) + text, 'replay'
