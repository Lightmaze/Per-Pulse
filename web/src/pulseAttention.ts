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
  // Preserve the existing source-checked activation-message destination.
  if (messageId && messageId === eventId) return { pageId, messageId };
  // An empty spontaneous input has no message of its own. The server may
  // separately verify the actual answer through that input's native turn.
  if (side === 'receiver' && entry.status === 'settled'
      && ['spontaneous', 'propagation'].includes(entry.kind)
      && typeof entry.resultMessageId === 'string' && entry.resultMessageId) {
    return { pageId, messageId: entry.resultMessageId };
  }
  return { pageId };
}
