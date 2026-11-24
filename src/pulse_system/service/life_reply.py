"""Read one original Home answer without creating a watching conversation.

An injected event ID is the selector; neither latest assistant text nor a model
provided message prefix is used to pair a reply. No media/source lifetime is
required to read an already admitted question after sharing has stopped.
"""
from pulse_harness.core.errors import HarnessError
from pulse_system.core.types.models import CausalEventKind, CausalEventSource
from pulse_system.service.runtime import ServiceError

MAX_TEXT = 32000
ACTIVE = {'open', 'model_streaming', 'waiting_approval', 'settling'}


def life_reply(runtime, event_id: str) -> dict:
    runtime._require_runtime_owner()
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 160:
        raise ServiceError('invalid_event', 'Invalid question reference', 'Open the original Home', status=400)
    ledger = runtime.causal_ledger
    event = ledger.get_event(event_id)
    if (event is None or event.world_id != runtime.world_id
            or event.engram_id != runtime.continuity_engram_id
            or event.center_id is not None or event.kind != CausalEventKind.STIMULUS
            or event.source != CausalEventSource.USER):
        raise ServiceError('home_question_unavailable', 'The question does not belong to the current Home',
                           'Open the original Home', status=404)
    result = {'event_id': event.id, 'engram_id': event.engram_id, 'status': event.status.value,
              'text': '', 'partial': False, 'truncated': False, 'control_scope': None,
              'error_code': None, 'native_status': None, 'response_in': 'Home'}
    with runtime.storage._lock:
        row = runtime.storage._conn.execute(
            'SELECT id FROM harness_turns WHERE event_id=? AND engram_id=? ORDER BY rowid DESC LIMIT 1',
            (event_id, event.engram_id),
        ).fetchone()
    if row is None:
        return result
    turn = ledger.get_turn_for_world(row[0], runtime.world_id)
    if turn is None or turn.event_id != event_id or turn.engram_id != event.engram_id:
        raise ServiceError('home_reply_unavailable', 'The original turn is unavailable',
                           'Open the original Home', status=409)
    result['error_code'] = turn.error_code
    if turn.result_event_id:
        answer = ledger.get_event(turn.result_event_id)
        if (answer is None or answer.parent_event_id != event_id
                or answer.world_id != event.world_id or answer.engram_id != event.engram_id
                or answer.kind != CausalEventKind.ASSISTANT_RESULT):
            raise ServiceError('home_reply_unavailable', 'The original answer is unavailable',
                               'Open the original Home', status=409)
        content = answer.content or ''
        result.update(status='succeeded', text=content[:MAX_TEXT], truncated=len(content) > MAX_TEXT)
        return result
    scope = runtime.harness.scope_for(event.engram_id).for_turn(turn.id)
    kernel = runtime.kernel_service
    try:
        native = kernel.store.get_turn(scope)
    except HarnessError:
        # Causal admission can precede native startup; no fake response/failure.
        return result
    # Keep native cancellation distinct from the causal result's uncertainty.
    # Cancelling a turn does not prove every earlier side effect was undone.
    result['native_status'] = native.status.value
    result['status'] = ('settling' if native.status.value == 'succeeded' else native.status.value)
    if turn.state.value in ('uncertain', 'failed'):
        result['status'] = turn.state.value
    if native.status.value in ACTIVE:
        result['control_scope'] = scope.as_dict()
    # Bounded observer evidence. Only public content deltas from the latest
    # response of this exact turn; hidden reasoning fields are never read.
    session = kernel.store.get_session(scope.session_scope())
    page = kernel.store.replay(scope.session_scope(), after_seq=max(0, session.cursor - 256), limit=256)
    deltas = [e for e in page.events if e.scope.turn_id == turn.id and e.event_type == 'turn.text_delta']
    if deltas:
        response_id = deltas[-1].payload.get('response_id')
        content = ''.join(str(e.payload.get('text', '')) for e in deltas if e.payload.get('response_id') == response_id)
        result.update(text=content[:MAX_TEXT], partial=True, truncated=len(content)>MAX_TEXT or page.gap or page.truncated)
    return result
