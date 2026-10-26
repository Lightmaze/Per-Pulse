/** Display native Pulse state without fabricating or altering event evidence. */
import type { MessageKey, Translate } from './i18n';

type PulseLabelField = 'kind' | 'status' | 'reason';
const known: Record<PulseLabelField, Record<string, MessageKey>> = {
  status: {
    queued: 'pulse.status.queued', running: 'pulse.status.running',
    settled: 'pulse.status.settled', failed: 'pulse.status.failed',
    uncertain: 'pulse.status.uncertain', cancelled: 'pulse.status.cancelled',
  },
  kind: {
    spontaneous: 'pulse.kind.spontaneous', propagation: 'pulse.kind.propagation',
    pulse: 'pulse.kind.pulse',
  },
  reason: {
    profile_paused: 'pulse.reason.profile_paused',
    operator_paused: 'pulse.reason.operator_paused',
    context_paused: 'pulse.reason.context_paused',
    context_inactive: 'pulse.reason.context_inactive',
    attention_held: 'pulse.reason.attention_held',
    refractory: 'pulse.reason.refractory',
    inhibited: 'pulse.reason.inhibited',
    depth_limit: 'pulse.reason.depth_limit',
    connection_revoked: 'pulse.reason.connection_revoked',
  },
};

export function pulseActivityLabel(t: Translate, field: PulseLabelField, value: string): string {
  // Native values not known to this UI remain visible verbatim, including
  // future runtime states. Raw values also remain on the activity elements.
  const key = Object.prototype.hasOwnProperty.call(known[field], value)
    ? known[field][value] : undefined;
  return key ? t(key) : value;
}
