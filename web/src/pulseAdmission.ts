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
