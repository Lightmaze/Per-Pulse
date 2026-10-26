/** Source-safe projection for one local Context Body, never a global feed of Work notes. */
import type { PulseRetainedAttention, PulseState } from './types';

export function localRetainedAttention(
  pulse: PulseState | null,
  pageId: string,
): PulseRetainedAttention[] {
  // An unresolved historical Work must never be mistaken for Home.
  if (!pulse || !pageId) return [];
  return (pulse.retainedAttention ?? []).filter(
    entry => entry.pageId !== null && entry.pageId === pageId,
  );
}

export function attentionSourceTarget(
  entry: PulseRetainedAttention,
  validPageIds: ReadonlySet<string>,
): { pageId: string; messageId?: string } | null {
  if (!entry.sourcePageId || !validPageIds.has(entry.sourcePageId)) return null;
  // Only the original, backend-validated message may become a deep link.
  // Never manufacture a link from a Kernel turn/action identity.
  if (entry.sourceMessageId && entry.sourceMessageId === entry.sourceEventId) {
    return { pageId: entry.sourcePageId, messageId: entry.sourceMessageId };
  }
  return { pageId: entry.sourcePageId };
}
