/** Explain native rolling admission limits without changing Pulse execution. */
import type { PulseState } from './types';

export type PulseAdmissionHold = 'hourly_count' | 'token_reservation' | null;

/**
 * This is only a reason that *new* local work cannot be enqueued now.
 * Existing queued/running activity and foreground turns have separate rights.
 * No hold reason is not a promise that a new activation will happen.
 */
export function pulseAdmissionHold(pulse: PulseState): PulseAdmissionHold {
  if (!pulse.enabled || pulse.paused || !pulse.providerConfigured) return null;
  if (pulse.budget.remaining <= 0) return 'hourly_count';
  const tokens = pulse.providerTokenBudget;
  if (tokens && tokens.remaining < tokens.reservationPerAdmission) {
    return 'token_reservation';
  }
  return null;
}

/**
 * Physical attachment exhaustion is distinct from model concurrency and
 * admission-token limits. Reveal it only for actual queued local work, with a
 * verified native parked population; this is not a scheduler diagnosis or a
 * claim that an attention-needing Work is necessarily an approval.
 */
export function pulseWorkerPressure(pulse: PulseState, localQueued: number): boolean {
  const capacity = pulse.workerCapacity;
  return Boolean(
    capacity && pulse.enabled && pulse.running && !pulse.paused
    && pulse.providerConfigured && localQueued > 0
    && capacity.approvalHeld > 0 && capacity.available === 0
    && capacity.attached >= capacity.computeLimit + capacity.approvalParkedLimit
  );
}
