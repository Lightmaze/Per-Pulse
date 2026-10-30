"""Consult existing, history-bearing participants through the actual TUNNEL.

An owned Work can explicitly establish a persistent participant or select an
existing one. Only successful source settlement admits its first assignment.
No fabricated experience, shadow model or second execution loop is created.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from copy import deepcopy
import unicodedata
from typing import Any, Mapping

from pulse_harness.core import ActionStatus, SessionStatus, TurnStatus
from pulse_harness.core.contracts import ToolDescriptor
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.scope import HarnessScope
from pulse_system.core.delegation import DURABLE_DELEGATION_MODE, DelegationTunnelError
from pulse_system.core.types import (
    CausalEventKind, CausalEventStatus, CausalEventSource, EngramStatus, HarnessTurnState,
)
from .prior_contribution import consultation_content
from .report_contributions import CITATION_SCHEMA, validate_citations

NAME = 'team'
PREFIX = 'native-team-v1:'
SCHEMA = {'type': 'object', 'properties': {
    'op': {'type': 'string', 'enum': ['list', 'search', 'inspect', 'consult', 'establish', 'feedback', 'propose_feedback']},
    'query': {'type': 'string', 'maxLength': 256},
    'before_record_id': {'type': 'string', 'maxLength': 200},
    'expert_id': {'type': 'string', 'maxLength': 160},
    'name': {'type': 'string', 'maxLength': 160},
    'record_id': {'type': 'string', 'maxLength': 200},
    'prior_record_id': {'type': 'string', 'maxLength': 200},
    'contributions': CITATION_SCHEMA,
    'instruction': {'type': 'string', 'maxLength': 8192},
    'content': {'type': 'string', 'maxLength': 2048},
    'scope': {'type': 'string', 'enum': ['work', 'collaborator']},
}, 'required': ['op'], 'additionalProperties': False}
_FIELDS = {'feedback': set(), 'list': set(), 'search': {'query'}, 'inspect': {'record_id'},
           'propose_feedback': {'record_id', 'content', 'scope'},
           'consult': {'expert_id', 'instruction'}, 'consult_adaptive': {'candidates', 'instruction'},
           'establish': {'name', 'instruction'}}


def schema(*, policy=False):
    result = deepcopy(SCHEMA)
    if policy:
        result['properties']['op']['enum'].append('consult_adaptive')
        result['properties']['candidates'] = {
            'type': 'object', 'minProperties': 2, 'maxProperties': 8,
            'additionalProperties': {'type': 'number', 'exclusiveMinimum': 0},
        }
    return result


def descriptor(*, policy=False) -> ToolDescriptor:
    return ToolDescriptor(NAME,
        'Use existing collaborators with their own saved experience, not temporary roles. '
        'list() shows active participants already known from your owned Works and their '
        'prior contribution references. Use search(query[,before_record_id]) when you remember '
        'the work, material or current correction rather than the collaborator name. It '
        'finds completed contributions using all whitespace-separated literal terms across '
        'names, work titles, tasks, result text and current applicable operator notes; it '
        'is not semantic expertise ranking. Results are newest first with exact sources, '
        'excerpts and scoped feedback. An empty page can have more_history=true: continue '
        'with the same query and next_before_record_id, not a new expert identity. '
        'Only notes applicable to this calling Work (or collaborator-wide in Home) are '
        'searched/quoted; outside_current_work is not a current instruction. Search reads '
        'only: it neither invites nor rewards anyone. inspect(record_id) reads one contribution returned '
        'to you. In a Work you own, consult(expert_id,instruction) selects one known '
        'collaborator and only the relevant material/question. '
        'When revisiting a specific earlier contribution, consult may also include '
        'prior_record_id from inspect/search/list. It must be that same collaborator\'s '
        'completed contribution to you. The runtime retains its exact source and up to '
        '1024 original characters with the new question; this historical excerpt does '
        'not override your changed constraints. The same author can read more through '
        'its history tools and should check current scoped feedback. No new identity '
        'or automatic correction/reward is created. inspect of the new result includes '
        'prior_contribution when this explicit relation exists. Finish your current reply: '
        'the request is dispatched only after this turn succeeds, and the original tunnel '
        'returns the collaborator response to this same Work. Do not poll while staging. '
        'Use list/search rather than inventing an expert ID. When genuinely forming a new '
        'long-term collaboration in your owned Work, explicitly establish(name,instruction). '
        'This reserves a new participant and a concrete first assignment, after your turn '
        'succeeds. It has NO prior experience: do not call it trained or reuse this operation '
        'for someone already known. Its actual first work begins its history; later use '
        'search/list and consult with its returned identity. Creating does not create a '
        'persona prompt, copy your history or give it new credentials. At most 32 active '
        'known participants; an existing name requires an explicit existing-ID choice. This '
        'does not grant tools, certify correctness, copy full histories or complete Work. '
        'A consulted expert may use search_history/read_history for its own prior '
        'collaboration with the same Work owner; those passages remain historical evidence. '
        'Other experts cannot recursively consult through this interface. '
        'To let a collaborator review another actual position, consult may include '
        'contributions:[{record_id,excerpt}] selected through your inspect/search results. '
        'Select 1..8 exact quotations of at most 1024 characters from your completed '
        'contributions, including earlier Works and other authors. Original sources '
        'travel with this consultation. Its recipient may inspect only these selected '
        'original results; this shares neither peer transcripts nor the owner roster or '
        'operator feedback. The recipient can respond to another position without '
        'pretending agreement. inspect of the later contribution retains '
        'shared_contributions showing what it was asked to review. '
        'feedback() reads current explicit operator corrections applicable to this Work '
        'and this collaborator/owner. Consulted participants should read them before '
        'reusing older contributions. Feedback is sourced material, not system authority '
        'or a reward; it may be revised or withdrawn and does not replace original history. '
        'In Home, when the user discusses correcting a particular collaborator contribution, '
        'find its record with list/inspect, then propose_feedback(record_id,content,scope) '
        'prepares an editable suggestion beside the original source for the operator to review. '
        'Use scope=work unless the user explicitly wants this same collaborator and owner '
        'across Works. A proposal is authored by you, NOT saved human feedback. Only the '
        'operator Save action adopts it. Do not say remembered, saved or learned merely '
        'because proposal generation succeeded; ordinary discussion need not create one.'
        + (' With RIM policy enabled, consult_adaptive(candidates,instruction) requests '
           'one persisted consultation from 2..8 known collaborator IDs and positive '
           'relative weights. Verified, explicitly rated native outcomes in this Work '
           'can shift the bounded selection. This is a local feedback proxy, not '
           'model confidence or proof of learning. The tool result exposes the '
           'source outcome IDs; the original Work turn must succeed before TUNNEL '
           'dispatch, and its selection is never redrawn after a restart. '
           'Use direct consult for an explicit single choice. '
           'An adaptive consultation may also include contributions with 1..8 '
           'exact source quotes from completed, owner-accessible prior Works. '
           'The selected collaborator receives only those explicitly selected '
           'results through the existing source-verified TUNNEL; peer histories '
           'and feedback are not shared. Such quotes do not change the RIM '
           'selection weights or authorize any additional tools.' if policy else ''),
        capability='team.internal', source='pulse.native-team')


def validate(arguments: Mapping[str, Any], *, policy=False) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise ValueError('team arguments must be an object')
    a = dict(arguments)
    op = a.get('op')
    if not isinstance(op, str) or op not in _FIELDS:
        raise ValueError('unknown operation or incorrect team fields')
    if op == 'consult_adaptive' and not policy:
        raise ValueError('RIM execution preference is disabled')
    optional = ({'before_record_id'} if op == 'search' else
                {'prior_record_id', 'contributions'} if op == 'consult' else
                {'contributions'} if op == 'consult_adaptive' else set())
    required = {'op'} | _FIELDS[op]
    if not required <= set(a) or set(a) - required - optional:
        raise ValueError('unknown operation or incorrect team fields')
    if 'contributions' in a:
        a['contributions'] = validate_citations(a['contributions'])
    if op == 'consult_adaptive':
        candidates = a['candidates']
        if not isinstance(candidates, dict) or not 2 <= len(candidates) <= 8:
            raise ValueError('adaptive consult needs 2..8 known candidates')
        for identity, weight in candidates.items():
            if (not isinstance(identity, str) or not identity.strip() or len(identity) > 160
                    or '\x00' in identity or type(weight) not in (int, float)
                    or not math.isfinite(weight) or weight <= 0):
                raise ValueError('invalid candidate identity or reference weight')
    for key, val in a.items():
        if key in {'contributions', 'candidates'}:
            continue
        limit = 8192 if key == 'instruction' else 2048 if key == 'content' else 256 if key == 'query' else 160 if key in {'expert_id', 'name'} else 200
        if not isinstance(val, str) or not val.strip() or len(val) > limit or '\x00' in val:
            raise ValueError(f'invalid {key}')
    if op == 'search':
        from .collaborator_search import query_terms
        query_terms(a['query'])
    if op == 'propose_feedback' and a['scope'] not in {'work', 'collaborator'}:
        raise ValueError('feedback scope must be work or collaborator')
    return a


def established_id(request_id: str) -> str:
    return 'col_' + hashlib.sha256(request_id.encode()).hexdigest()[:32]


def _target(record) -> str:
    operation = record['arguments']['op']
    if operation == 'establish':
        return established_id(record['request_id'])
    if operation == 'consult_adaptive':
        return record['preference']['selected_expert_id']
    return record['arguments']['expert_id']


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class NativeTeam:
    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = threading.RLock()
        from .collaborator_feedback import CollaboratorFeedback
        self.feedback = CollaboratorFeedback(runtime)

    def _front(self, scope: HarnessScope, *, writable=False):
        if not scope.context_id.startswith('work:'):
            raise ScopeConflict('consult requires an owned Work, not Home')
        r = self.runtime
        fronts = [f for f in r.world.list_task_fronts(focal_engram_id=scope.engram_id)
                  if 'work:' + f.center_id == scope.context_id]
        if len(fronts) != 1:
            raise ScopeConflict('caller is not the owner of this Work')
        return r._native_work._owned(scope.engram_id, fronts[0].id, writable=writable)

    def _known(self, subject: str) -> dict[str, list]:
        r = self.runtime
        known: dict[str, list] = {}
        for front in r.world.list_task_fronts(focal_engram_id=subject):
            for membership in r.storage.list_center_memberships(center_id=front.center_id):
                expert = r.storage.get_engram(membership.engram_id)
                if expert is None or expert.id == subject or expert.status is not EngramStatus.ACTIVE:
                    continue
                known.setdefault(expert.id, []).append(front)
        return known

    def _record(self, subject: str, record_id: str):
        r = self.runtime
        record = r.storage.get_delegation(record_id)
        if record is None or record['caller_id'] != subject or record['mode'] != DURABLE_DELEGATION_MODE:
            raise ScopeConflict('contribution is not owned by this caller')
        request = r.causal_ledger.find_delegation_request_by_record_id(r.world_id, record_id)
        if request is None:
            raise ScopeConflict('contribution has no matching durable origin')
        try:
            caller, original_id = r.delegation_tunnel._request_owners(request)
            admitted_target = r.delegation_tunnel._request_admitted_target_id(request)
            # The execution holder may be a committed successor; the retained
            # contribution still belongs to the immutable admitted identity.
            r.delegation_tunnel._assert_request_execution_lineage(request, admitted_target)
        except DelegationTunnelError as exc:
            raise ScopeConflict('contribution has no matching durable origin') from exc
        if (caller != subject or original_id != record_id
                or record['target_id'] != admitted_target
                or record['task'] != request.content):
            raise ScopeConflict('contribution does not match its original target and task')
        fronts = [f for f in r.world.list_task_fronts(focal_engram_id=subject)
                  if f.center_id == request.center_id]
        if len(fronts) != 1:
            raise ScopeConflict('contribution does not belong to an owned Work')
        return record, request, fronts[0]

    def _authorize(self, scope, action_id, call, *, allow_consulted=False):
        r = self.runtime
        h = r.harness
        h._check()
        if scope.session_scope() != h.scope_for(scope.engram_id, scope.context_id):
            raise ScopeConflict('team caller is not a native context')
        if h.kernel.store.get_session(scope.session_scope()).status not in {SessionStatus.CREATED, SessionStatus.ACTIVE}:
            raise ScopeConflict('team caller context is not active')
        with h._lock:
            if h._active.get((scope.engram_id, scope.context_id)) != scope or not action_id:
                raise ScopeConflict('team requires the exact active native turn')
        action = h.kernel.store.get_action(scope, action_id)
        if action.tool_name != NAME or action.terminal or dict(action.arguments) != dict(call.arguments):
            raise ScopeConflict('team action does not match the native call')
        if scope.context_id:
            if not (allow_consulted and self._consultation(scope) is not None):
                self._front(scope)

    def execute(self, scope, call, cancellation, *, action_request_id=None):
        r = self.runtime
        with self._lock:
            r._require_runtime_owner()
            a = validate(call.arguments, policy=r.harness._rim_policy)
            self._authorize(scope, action_request_id, call, allow_consulted=a['op'] in {'feedback', 'inspect'})
            cancellation.raise_if_cancelled()
            if a['op'] == 'feedback':
                consultation = self._consultation(scope)
                if consultation is not None:
                    return self.feedback.applicable(consultation['subject'], consultation['work_id'], scope.engram_id)
                front = self._front(scope) if scope.context_id else None
                return self.feedback.applicable(scope.engram_id, front.id if front else None)
            if a['op'] == 'search':
                from .collaborator_search import search
                front = self._front(scope) if scope.context_id else None
                return search(self, scope.engram_id, front.id if front else None,
                              a['query'], a.get('before_record_id'), cancellation)
            if a['op'] == 'propose_feedback':
                if scope.context_id:
                    raise ScopeConflict('feedback suggestions belong to the original owner Home')
                record, _, front = self._record(scope.engram_id, a['record_id'])
                saved = self.feedback.get(front.id, a['record_id'])
                expert = r.storage.get_engram(record['target_id'])
                if saved['source']['owner_id'] != scope.engram_id or expert is None:
                    raise ScopeConflict('feedback suggestion source is not owned by this Home')
                # A normal read-only tool result, durably retained by the Kernel.
                # It does not create an annotation, revise a persona, or dispatch
                # a worker. The operator uses the existing authenticated PUT.
                contribution = record['result_summary']
                return {'kind': 'collaborator_feedback_proposal.v1',
                    'source': saved['source'], 'base_revision': saved['revision'],
                    'content': a['content'], 'scope': a['scope'],
                    'expert_name': expert.name or expert.id,
                    'work_title': front.title,
                    'contribution_excerpt': contribution[:2048],
                    'excerpt_truncated': len(contribution) > 2048,
                    'origin': 'model_suggestion_not_operator_feedback',
                    'effect': 'not_saved_requires_operator_confirmation'}
            if a['op'] == 'inspect':
                consultation = self._consultation(scope)
                if consultation is not None:
                    from .shared_contributions import selected_for_inspection
                    # This result is explicitly shared by its actual owner. Do
                    # not expose its author's other sources or annotations.
                    with r.storage._lock:
                        selected = selected_for_inspection(r, consultation, a['record_id'])
                        record, request, front = self._record(consultation['subject'], a['record_id'])
                        result = record['result_summary']
                        return {'record_id': record['id'], 'expert_id': record['target_id'],
                            'expert_name': selected['expert_name'], 'work_id': front.id,
                            'work_title': selected['work_title'], 'source': selected['source'],
                            'request_event_id': request.id, 'request_state': request.status.value,
                            'result': result[:8192], 'truncated': len(result) > 8192,
                            'result_digest': selected['result_digest'],
                            'selected_excerpt': selected['excerpt'],
                            'access': 'explicitly_shared_result_only',
                            'evidence': 'collaborator_output_not_verified_truth'}
                record, request, front = self._record(scope.engram_id, a['record_id'])
                result = record.get('result_summary')
                from .prior_contribution import retained
                prior = retained(r, scope.engram_id, request)
                from .shared_contributions import retained as shared_retained
                shared = shared_retained(r, scope.engram_id, request)
                return {'record_id': record['id'], 'expert_id': record['target_id'],
                    'work_id': front.id, 'work_title': front.title,
                    'request_event_id': request.id, 'request_state': request.status.value,
                    'result': None if result is None else result[:8192],
                    'truncated': result is not None and len(result) > 8192,
                    'outcome': record.get('outcome'),
                    'evidence': 'collaborator_output_not_verified_truth',
                    **({'prior_contribution': prior} if prior is not None else {}),
                    **({'shared_contributions': shared} if shared is not None else {})}
            if a['op'] == 'list':
                known = self._known(scope.engram_id)
                records = r.storage.list_delegations(caller_id=scope.engram_id, limit=128)
                experts = []
                for expert_id in sorted(known)[:32]:
                    expert = r.storage.get_engram(expert_id)
                    past = []
                    for record in records:
                        if record['target_id'] != expert_id or record['mode'] != DURABLE_DELEGATION_MODE:
                            continue
                        try:
                            _, request, front = self._record(scope.engram_id, record['id'])
                        except ScopeConflict:
                            continue
                        past.append({'record_id': record['id'], 'work_id': front.id,
                            'task': record['task'][:256], 'state': request.status.value,
                            'has_result': record.get('result_summary') is not None})
                        if len(past) == 4: break
                    origin = r.storage.load_component_state('native-team-establishment-v1:' + expert_id)
                    if origin is not None and origin.get('owner_id') != scope.engram_id:
                        origin = None  # Shared membership does not expose another owner's founding Work.
                    experts.append({'expert_id': expert_id, 'name': expert.name,
                        'establishment': origin,
                        'known_from': [{'work_id': f.id, 'title': f.title} for f in known[expert_id][:8]],
                        'recent_contributions': past})
                return {'experts': experts, 'more_experts': len(known) > 32,
                    'scope': 'participants_of_your_owned_works', 'history_is_bounded': True,
                    'creates_experts': False}
            if not r.harness._allow_mutations:
                raise ScopeConflict('read-only profile cannot schedule a consultation')
            front = self._front(scope, writable=True)
            if a['op'] == 'consult' and a['expert_id'] not in self._known(scope.engram_id):
                raise ScopeConflict('expert is not an active known participant; no implicit new identity')
            if a['op'] == 'consult_adaptive' and any(
                    identity not in self._known(scope.engram_id) for identity in a['candidates']):
                raise ScopeConflict('adaptive consultation requires active known collaborators')
            source = r.causal_ledger.get_turn_for_world(scope.turn_id, r.world_id)
            if source is None or source.engram_id != scope.engram_id or source.state is not HarnessTurnState.RUNNING:
                raise ScopeConflict('consultation requires its live causal source turn')
            if r._native_kernel_context_for_turn(scope.engram_id, source.id) != scope.context_id:
                raise ScopeConflict('consultation source is not this Work')
            key = PREFIX + _digest({'scope': scope.as_dict(), 'action': action_request_id})
            rec = r.storage.load_component_state(key)
            if rec is not None:
                if rec['arguments'] != a:
                    raise ScopeConflict('consultation replay differs')
            else:
                if a['op'] == 'establish':
                    known = self._known(scope.engram_id)
                    if len(known) >= 32:
                        raise ScopeConflict('active collaborator capacity reached; reuse a known participant')
                    name = unicodedata.normalize('NFKC', a['name'].strip()).casefold()
                    if any(e is not None and e.name and
                           unicodedata.normalize('NFKC', e.name).casefold() == name
                           for e in (r.storage.get_engram(eid) for eid in known)):
                        raise ScopeConflict('name already belongs to a known collaborator; choose its ID')
                if len(r.storage.native_team_requests(pending=True, limit=65)) >= 64:
                    raise ScopeConflict('pending consultation capacity reached')
                rec = {'version': 1, 'request_id': key, 'world_id': r.world_id,
                    'subject': scope.engram_id, 'source_scope': scope.as_dict(),
                    'source_turn_id': scope.turn_id, 'source_action_id': action_request_id,
                    'delegation_event_id': r.delegation_tunnel.identity_for(
                        r.world_id, scope.engram_id, front.center_id, key).request_event_id,
                    'arguments': a, 'work_id': front.id, 'center_id': front.center_id,
                    'status': 'prepared', 'record_id': None, 'error_code': None}
                if a['op'] == 'consult_adaptive':
                    from .execution_preference import choose
                    rec['preference'] = choose(self, scope, front, a)
                if 'prior_record_id' in a:
                    from .prior_contribution import capture
                    rec['prior_contribution'] = capture(r, scope.engram_id,
                        a['expert_id'], a['prior_record_id'])
                if 'contributions' in a:
                    from .report_contributions import capture as capture_quotes
                    rec['shared_contributions'] = capture_quotes(r, scope.engram_id, a['contributions'])
                r.storage.save_component_state(key, rec)
            return {k: rec[k] for k in ('request_id', 'work_id', 'status', 'record_id', 'error_code')} | {
                'expert_id': _target(rec), 'delivery': 'after_successful_work_turn',
                'return_to': 'this_work_not_home',
                'effect': ('establish_persistent_participant' if a['op'] == 'establish'
                           else 'consult_existing_participant'),
                'prior_experience': 'none' if a['op'] == 'establish' else 'read_original_sources',
                **({'preference': rec['preference']} if a['op'] == 'consult_adaptive' else {}),
                **({'prior_record_id': a['prior_record_id']} if 'prior_record_id' in a else {}),
                **({'shared_record_ids': [c['record_id'] for c in a['contributions']]}
                    if 'contributions' in a else {})}

    def _finish(self, rec, status, *, record_id=None, error=None):
        self.runtime.storage.save_component_state(rec['request_id'], {
            **rec, 'status': status, 'record_id': record_id, 'error_code': error})

    def cancel_subject(self, subject: str) -> int:
        with self._lock:
            records = self.runtime.storage.native_team_requests(subject=subject, pending=True, limit=64)
            for rec in records: self._finish(rec, 'cancelled', error='subject_stopped')
            return len(records)

    def drain(self):
        from .runtime import ServiceError
        r = self.runtime
        with self._lock:
            r._require_runtime_owner()
            for rec in r.storage.native_team_requests(pending=True, limit=64):
                scope = HarnessScope.from_dict(rec['source_scope'])
                source = r.causal_ledger.get_turn_for_world(rec['source_turn_id'], r.world_id)
                if source is None or source.engram_id != rec['subject'] or rec['world_id'] != r.world_id:
                    self._finish(rec, 'blocked', error='source_unavailable'); continue
                if source.state is HarnessTurnState.RUNNING: continue
                if source.state is not HarnessTurnState.SETTLED or not source.result_event_id:
                    self._finish(rec, 'cancelled', error='source_unsuccessful'); continue
                try:
                    action = r.kernel_service.store.get_action(scope, rec['source_action_id'])
                    turn = r.kernel_service.store.get_turn(scope)
                    if (turn.status is not TurnStatus.SUCCEEDED or action.status is not ActionStatus.SUCCEEDED
                            or action.tool_name != NAME or dict(action.arguments) != rec['arguments']):
                        self._finish(rec, 'cancelled', error='native_action_unsuccessful'); continue
                    target_id = _target(rec)
                    # Existing tunnel identity repairs enqueue-before-outbox-receipt crashes.
                    identity = r.delegation_tunnel.identity_for(r.world_id, rec['subject'], rec['center_id'], rec['request_id'])
                    prior = r.causal_ledger.get_event(identity.request_event_id)
                    if prior is not None:
                        if (prior.metadata.get('caller_id') != rec['subject']
                                or prior.metadata.get('admitted_target_id') != target_id
                                or prior.content != consultation_content(rec)):
                            raise ScopeConflict('existing consultation differs')
                        self._finish(rec, 'delivered', record_id=identity.record_id); continue
                    front = self._front(scope, writable=True)
                    if front.center_id != rec['center_id'] or front.id != rec['work_id']:
                        raise ScopeConflict('source Work changed')
                    if rec['arguments']['op'] == 'establish':
                        origin = {'version': 1, 'expert_id': target_id, 'owner_id': rec['subject'],
                            'work_id': front.id, 'center_id': front.center_id,
                            'request_id': rec['request_id'], 'source_turn_id': rec['source_turn_id'],
                            'source_action_id': rec['source_action_id'],
                            'name': rec['arguments']['name'].strip(),
                            'origin': 'model_selected_new_participant_not_prior_expertise'}
                        r.storage.ensure_native_collaborator(origin)
                    elif target_id not in self._known(rec['subject']):
                        raise ScopeConflict('known expert no longer available')
                    if 'prior_record_id' in rec['arguments']:
                        from .prior_contribution import verify_for_delivery
                        verify_for_delivery(r, rec)
                    if 'contributions' in rec['arguments']:
                        from .shared_contributions import verify_for_delivery as verify_shared
                        verify_shared(r, rec)
                    # Existing invitations extend membership; establishment has already
                    # atomically retained it with the identity and its original source.
                    if not any(m.engram_id == target_id for m in
                               r.storage.list_center_memberships(center_id=front.center_id)):
                        r.storage.add_center_membership(front.center_id, target_id)
                    record_id = r.delegate(consultation_content(rec),
                        to=target_id, caller_id=rec['subject'],
                        center_id=front.center_id, idempotency_key=rec['request_id'])
                    self._finish(rec, 'delivered', record_id=record_id)
                except (ScopeConflict, ValueError, ServiceError) as exc:
                    self._finish(rec, 'blocked', error=getattr(exc, 'code', type(exc).__name__))

    def _consultation(self, scope: HarnessScope) -> dict | None:
        """Resolve the exact native-team request executing in this context."""
        r = self.runtime
        source = r.causal_ledger.get_turn_for_world(scope.turn_id, r.world_id)
        event = r.causal_ledger.get_event(source.event_id) if source is not None else None
        if event is None or event.kind is not CausalEventKind.DELEGATION_REQUEST or event.engram_id != scope.engram_id:
            return None
        caller = event.metadata.get('caller_id')
        if not isinstance(caller, str) or not caller:
            return None
        current = r.storage.native_team_requests(subject=caller, delegation_event_id=event.id, limit=1)
        if not current or _target(current[0]) != scope.engram_id:
            return None
        rec = current[0]
        identity = r.delegation_tunnel.identity_for(r.world_id, caller, rec['center_id'], rec['request_id'])
        if identity.request_event_id != event.id or event.content != consultation_content(rec):
            return None
        if scope.context_id != "work:" + rec["center_id"]:
            return None
        return rec

    def history_scopes(self, scope: HarnessScope) -> tuple[HarnessScope, ...]:
        """Retained own collaboration passages, without widening to other owners."""
        r = self.runtime
        rec = self._consultation(scope)
        if rec is None: return ()
        caller = rec["subject"]
        event = r.causal_ledger.get_event(rec["delegation_event_id"])
        extra = []
        # An explicitly selected old source may be outside the recent discovery
        # window. Keep that one original Work readable to its own author first;
        # the bounded ambient-history scan below must not silently displace it.
        if 'prior_record_id' in rec['arguments']:
            from .prior_contribution import verify_for_delivery
            try:
                verify_for_delivery(r, rec)
                _, _, selected_front = self._record(caller, rec['arguments']['prior_record_id'])
            except (ScopeConflict, ValueError):
                pass
            else:
                candidate = r.harness.scope_for(scope.engram_id, 'work:' + selected_front.center_id)
                if candidate != scope.session_scope():
                    extra.append(candidate)
        for record in r.storage.list_delegations(caller_id=caller, limit=128):
            if record['target_id'] != scope.engram_id or record.get('result_summary') is None:
                continue
            try:
                _, previous, front = self._record(caller, record['id'])
            except ScopeConflict:
                continue
            if previous.status is not CausalEventStatus.SETTLED or previous.id == event.id:
                continue
            candidate = r.harness.scope_for(scope.engram_id, 'work:' + front.center_id)
            if candidate != scope.session_scope() and candidate not in extra:
                extra.append(candidate)
            if len(extra) == 32: break
        return tuple(extra)


    def contribution_origin(self, event_id: str) -> dict[str, Any] | None:
        """Read-only presentation from the exact retained tunnel/result, not text tags."""
        from pulse_system.core.delegation import DelegationConflictError
        r = self.runtime
        event = r.causal_ledger.get_event(event_id)
        if (event is None or event.world_id != r.world_id
                or event.kind is not CausalEventKind.DELEGATION_RESULT
                or event.source is not CausalEventSource.DELEGATION):
            return None
        record_id = event.metadata.get('record_id')
        record = r.storage.get_delegation(record_id) if isinstance(record_id, str) else None
        if record is None or record['mode'] != DURABLE_DELEGATION_MODE:
            return None
        request = r.causal_ledger.find_delegation_request_by_record_id(r.world_id, record_id)
        if request is None or request.status is not CausalEventStatus.SETTLED:
            return None
        result = r.causal_ledger.get_event(event.parent_event_id) if event.parent_event_id else None
        if (result is None or result.kind is not CausalEventKind.ASSISTANT_RESULT
                or result.status is not CausalEventStatus.SETTLED
                or result.parent_event_id != request.id
                or record.get('result_summary') != result.content):
            return None
        try:
            r.delegation_tunnel._assert_result_delivery(event, request, result,
                record['caller_id'], request.metadata['admitted_target_id'])
        except (DelegationConflictError, KeyError, ValueError):
            return None
        expert = r.storage.get_engram(request.engram_id)
        return {'expert_id': request.engram_id, 'name': expert.name if expert else None,
            'record_id': record_id, 'request_event_id': request.id,
            'result_event_id': result.id, 'evidence': 'collaborator_output_not_verified_truth'}
