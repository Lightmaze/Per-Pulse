/** Project the durable native queue by local Work, not a sampled event list. */
import type { PulseState } from './types';

export type PulseLocalQueue = NonNullable<PulseState['queueByContext']>[number];

export function pulseQueueForPage(pulse: PulseState | null, pageId: string): PulseLocalQueue | null {
  return pulse?.queueByContext?.find(entry => entry.pageId === pageId) ?? null;
}

export function otherPendingPulseContexts(pulse: PulseState | null, pageId: string): PulseLocalQueue[] {
  return (pulse?.queueByContext ?? []).filter(entry =>
    entry.pageId !== pageId && (entry.queued > 0 || entry.running > 0));
}
