"""Return selected native cognitive work through the existing causal Home path.

The final checkpoint is the substantive content, not a queue-status notification
laundered into life. A prior successful Home action must have requested its return.
A trusted system notice addresses the retained work; actual model-produced text
is read using the normal cognition tool, not promoted into system instructions.
"""
from __future__ import annotations
import json
from pulse_harness.core import HarnessScope, TurnStatus
from pulse_harness.core.contracts import ActionStatus
from pulse_harness.core.errors import ScopeConflict, LeaseFenced
from pulse_system.agent.harness.stimulus_firewall import StimulusEnvelope, EvidenceClass
from pulse_system.core.types import CausalEventSource, EngramStatus, HarnessTurnState

PREFIX = 'native-cognition-return-v1:'


class NativeCognitionReturn:
    def __init__(self, runtime):
        self.r = runtime

    def _source(self, rec):
        r = self.r
        scope = HarnessScope.from_dict(json.loads(rec['origin']))
        if scope.context_id or scope.world_id != r.world_id:
            raise ScopeConflict('cognition return belongs to another context')
        r.harness._cognition_scope_check(scope)
        source = r.causal_ledger.get_turn_for_world(scope.turn_id, r.world_id)
        if source is None or source.engram_id != scope.engram_id:
            raise ScopeConflict('cognition return has no originating turn')
        if source.state is HarnessTurnState.RUNNING: return scope, None
        if source.state is not HarnessTurnState.SETTLED or not source.result_event_id:
            raise ScopeConflict('originating Home did not settle successfully')
        action = r.kernel_service.store.get_action(scope, rec['id'])
        turn = r.kernel_service.store.get_turn(scope)
        args = json.loads(rec['arguments'])
        if (turn.status is not TurnStatus.SUCCEEDED or action.status is not ActionStatus.SUCCEEDED
                or action.tool_name != 'cognition' or dict(action.arguments) != args
                or args.get('op') != 'run' or args.get('return_to_home') is not True
                or args.get('thread_id') != rec['thread_id']):
            raise ScopeConflict('cognition return does not match its successful native action')
        return scope, source

    @staticmethod
    def _content(rec):
        cp = json.loads(rec['checkpoint'])
        return json.dumps({'kind': 'returned_tentative_cognitive_work',
            'thread_id': rec['thread_id'], 'run_id': rec['id'], 'checkpoint': cp,
            'not_user_instruction': True, 'goal_complete': False},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def drain(self):
        """Existing Runtime tick; no polling model, timer, second scheduler or UI."""
        r = self.r
        c = r.harness.cognition
        if c is None: return
        # inject owns the Runtime close lock itself (it is not reentrant).
        # The journal lock serializes validation/admission with text corrections;
        # shutdown fences publication before draining this short critical section.
        with c.journal._lock:
            r._require_runtime_owner(); c._check()
            seen = set()
            for rec in c.returns.pending():
                try:
                    scope, source = self._source(rec)
                except (ScopeConflict, LeaseFenced):
                    c.returns.hold(rec['id'], 'source_or_owner_changed'); continue
                if source is None: continue
                key = PREFIX + rec['id']
                prior = r.causal_ledger.find_causal_event_by_idempotency(r.world_id, key)
                if prior is not None:
                    # A crash after enqueue cannot manufacture a second event.
                    if (prior.engram_id != scope.engram_id or prior.content != self._content(rec)
                            or prior.source is not CausalEventSource.SELF or prior.center_id is not None
                            or prior.metadata.get('stimulus_source_turn_id') != source.id):
                        c.returns.hold(rec['id'], 'admission_identity_conflict'); continue
                    c.returns.delivered(rec['id'], prior.id); continue
                if not c.returns.current(rec):
                    c.returns.hold(rec['id'], 'checkpoint_no_longer_current'); continue
                who = scope.engram_id
                if who in seen: continue
                engram = r.storage.get_engram(who)
                if engram is None or engram.status is not EngramStatus.ACTIVE:
                    c.returns.hold(rec['id'], 'subject_inactive'); continue
                if r.causal_ledger.get_running_turn_for_context(who, 'home') is not None:
                    continue
                # Do not preempt or join an outstanding ordinary Home input.
                queued = r.causal_ledger.list_events(world_id=r.world_id, engram_id=who,
                    status='queued', limit=500)
                if any(r._native_kernel_context_for_center(e.center_id) == '' for e in queued):
                    continue
                content = self._content(rec)
                envelope = StimulusEnvelope.subject_reflection(key,
                    lineage_id=r._ensure_subject_lineage(who), source_turn_id=source.id,
                    capability='subject_project', content=content,
                    evidence_class=EvidenceClass.LIVE, settled=True, explicit_adoption=True)
                def valid(_conn):
                    # inject validates the live subject and Runtime fence in this
                    # transaction; do not open another lease transaction here.
                    if not c.returns.current(rec): raise ScopeConflict('returned work changed before admission')
                event_id = r.inject(who, content, source='self', priority=0.5,
                    idempotency_key=key, stimulus_envelope=envelope, _admission_guard=valid)
                c.returns.delivered(rec['id'], event_id)
                seen.add(who)

    def input_for_turn(self, who, turn_id):
        """Only a real admitted return can select a non-user Kernel input."""
        r = self.r
        turn = r.causal_ledger.get_turn_for_world(turn_id, r.world_id)
        if turn is None or turn.engram_id != who: raise ScopeConflict('missing causal turn')
        event = r.causal_ledger.get_event(turn.event_id)
        key = event.idempotency_key if event else None
        if (not isinstance(key, str) or not key.startswith(PREFIX)
                or event.source is not CausalEventSource.SELF): return None
        c = r.harness.cognition
        if c is None: raise ScopeConflict('cognition return is not enabled')
        with c.journal._lock:
            c._check()
            rec = c.returns.get(key[len(PREFIX):])
            if rec is None: raise ScopeConflict('missing cognitive return provenance')
            scope, source = self._source(rec)
            if (source is None or scope.engram_id != who or event.source is not CausalEventSource.SELF
                    or event.center_id is not None or event.content != self._content(rec)
                    or not c.returns.current(rec)):
                raise ScopeConflict('cognitive return revoked, outdated or foreign')
            cp = json.loads(rec['checkpoint'])
            # IDs/versions only: generated work never gets system authority.
            return ('Internal cognitive-work return, not a user message. Your prior successful Home '
                'action requested this review. Use cognition inspect on the thread below to obtain '
                'the actual retained work as a tool result; check freshness and relevance before '
                'using it. Allocation completion does not establish goal completion, new user intent, '
                'or additional tool permission. Do not busy-poll or automatically duplicate this run. '
                'Decide what is useful to continue or report in the original conversation.\n' +
                json.dumps({'kind': 'cognition_return_notice', 'thread_id': rec['thread_id'],
                    'checkpoint_version': cp['version'], 'run_id': rec['id'],
                    'source_home_turn_id': source.id}, sort_keys=True))
