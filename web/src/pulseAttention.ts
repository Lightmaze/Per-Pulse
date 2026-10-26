/** Source-safe projection for one local Context Body, never a global feed of Work notes. */
import type { PulseActivation, PulseRetainedAttention, PulseState } from './types';

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

/** Navigate a live Pulse activation without inventing message ownership. */
export function activationMessageTarget(
  entry: PulseActivation,
  validPageIds: ReadonlySet<string>,
  side: 'receiver' | 'source',
): { pageId: string; messageId?: string } | null {
  const pageId = side === 'receiver' ? entry.pageId : entry.sourcePageId;
  if (!pageId || !validPageIds.has(pageId)) return null;
  const eventId = side === 'receiver' ? entry.eventId : entry.sourceEventId;
  const messageId = side === 'receiver' ? entry.messageId : entry.sourceMessageId;
  // The backend checks original World, center and engram lineage; the UI
  // additionally refuses an ID that does not match its claimed event.
  return messageId && messageId === eventId ? { pageId, messageId } : { pageId };
}
