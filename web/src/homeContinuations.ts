/** Verified native Pulse work worth reopening from Home.
 *
 * This projects existing settled local activations; it never invents a
 * summary, copies another Work's transcript or promotes a result to Home.
 * Original World message identity is required for an exact Work return.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import { activationMessageTarget } from './pulseAttention.ts';
import type { PageSummary, PulseActivation, PulseState } from './types';

export type HomeContinuation = {
  event: PulseActivation;
  page: PageSummary;
  receiver: { pageId: string; messageId: string };
  source: { pageId: string; messageId?: string } | null;
};

export function homeContinuations(
  pulse: PulseState | null,
  pages: readonly PageSummary[],
  maximum = 3,
): HomeContinuation[] {
  if (!pulse?.enabled || !Number.isInteger(maximum) || maximum <= 0) return [];
  const known = new Map(pages.filter(page => page.kind === 'work').map(page => [page.id, page]));
  const validIds = new Set(['home', ...pages.map(page => page.id)]);
  const displayed = new Set<string>();
  const rows: HomeContinuation[] = [];
  // The server returns its latest twenty durable activations in descending
  // order. Preserve that order; do not treat an event ID as a timestamp.
  for (const event of pulse.recentActivations ?? []) {
    if (event.status !== 'settled' || !['propagation', 'spontaneous'].includes(event.kind)) continue;
    const page = event.pageId && known.get(event.pageId);
    if (!page || displayed.has(page.id)) continue;
    const target = activationMessageTarget(event, validIds, 'receiver');
    if (!target?.messageId) continue; // Never fabricate a World message link.
    displayed.add(page.id);
    rows.push({
      page, event, receiver: { pageId: target.pageId, messageId: target.messageId },
      source: activationMessageTarget(event, validIds, 'source'),
    });
    if (rows.length >= maximum) break;
  }
  return rows;
}

/** Return live original Works where a person must inspect a blocked or failed turn.
 *
 * These are native page projections (not AI-suggested tasks). An approval is
 * never exposed or executed from Home; only the original Work can resolve it.
 * Keep errors visible too: a failed action must not silently disappear when
 * the user returns Home.
 */
export function homeWorkNeedsAttention(
  pages: readonly PageSummary[],
  maximum = 3,
): PageSummary[] {
  if (!Number.isInteger(maximum) || maximum <= 0) return [];
  const seen = new Set<string>();
  return pages
    .filter(page => page.kind === 'work'
      && (page.status === 'needs_attention' || page.status === 'error'))
    .sort((a, b) => {
      const aNeedsReview = a.status === 'needs_attention' ? 0 : 1;
      const bNeedsReview = b.status === 'needs_attention' ? 0 : 1;
      if (aNeedsReview !== bNeedsReview) return aNeedsReview - bNeedsReview;
      // ISO timestamps are server-authored; an invalid timestamp stays last.
      const aTime = Date.parse(a.updatedAt) || 0;
      const bTime = Date.parse(b.updatedAt) || 0;
      return bTime - aTime || a.id.localeCompare(b.id);
    })
    .filter(page => {
      if (seen.has(page.id)) return false;
      seen.add(page.id);
      return true;
    })
    .slice(0, maximum);
}
