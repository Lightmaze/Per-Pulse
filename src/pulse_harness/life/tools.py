"""Per-context access to user-selected, short-lived viewing moments."""
from pulse_harness.core.contracts import ToolDescriptor
from pulse_harness.core.errors import ProviderProtocolError, ScopeConflict
from .stream import LifeError

LIFE_SCHEMAS = {
 'life_focus': {'type':'object','properties':{'moment_id':{'type':'string'},'at':{'type':'number'},'seconds':{'type':'number','minimum':.1,'maximum':2}},'required':['moment_id','at'],'additionalProperties':False},
 'life_recent': {'type':'object','properties':{'moment_id':{'type':'string'}},'required':['moment_id'],'additionalProperties':False},
 'life_frame': {'type':'object','properties':{'moment_id':{'type':'string'},'index':{'type':'integer','minimum':0,'maximum':7}},'required':['moment_id','index'],'additionalProperties':False},
}

def descriptors():
    return (ToolDescriptor('life_focus', 'Look more densely around a capture time inside an explicitly shared moment. Re-selects up to eight real frames from the recent evidence ring, not earlier summaries. Use when the sparse overview misses detail. Cannot see beyond the frozen interval; may expire.', capability='life.read', source='pulse.life'),
            ToolDescriptor('life_recent', 'Inspect a viewing moment the user shared: observed times, sampled frame references, change cues, audio coverage and subtitle samples. Subtitles are sampled text shared with the moment; a missing sample does not mean silence, and cue end times do not tell future dialogue. Use life_frame to see a frame. Moments can expire.', capability='life.read', source='pulse.life'),
            ToolDescriptor('life_frame', 'Observe one captured image from a viewing moment with explicit user image-sharing permission. On a vision-compatible configured model, pixels accompany the next model request. Never infer unseen motion, speech, emotions or intent from a change score.', capability='life.read', source='pulse.life'))


def execute(hub, scope, call, cancellation):
    cancellation.raise_if_cancelled()
    args = call.arguments
    if (call.name not in LIFE_SCHEMAS or not set(LIFE_SCHEMAS[call.name]['required']) <= set(args)
            or set(args)-set(LIFE_SCHEMAS[call.name]['properties'])):
        raise ProviderProtocolError('invalid life observation request')
    if not isinstance(args['moment_id'],str) or len(args['moment_id']) > 64:
        raise ProviderProtocolError('invalid moment identity')
    try:
        if call.name == 'life_focus': return hub.focus(scope,args['moment_id'],args['at'],args.get('seconds',1.))
        if call.name == 'life_recent': return hub.describe(args['moment_id'],scope)
        return {'life_image': hub.image_reference(scope,args['moment_id'],args['index'])}
    except LifeError as exc:
        raise ScopeConflict('Life observation unavailable: '+exc.code) from None
