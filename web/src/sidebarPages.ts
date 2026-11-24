/** Keep long-lived Work navigation usable without mounting every row.
 * Source summaries and their order stay the native World projection.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
export const SIDEBAR_PAGE_BATCH = 48;

type WorkSummary = { id: string; title: string; description: string };

export function sidebarPageWindow<T extends WorkSummary>(
  pages: readonly T[], query: string, activePageId: string, requested: number,
): { visible: T[]; total: number; shown: number; hasMore: boolean } {
  const needle = query.trim().toLocaleLowerCase();
  const matches = needle
    ? pages.filter(page => `${page.title} ${page.description}`.toLocaleLowerCase().includes(needle))
    : [...pages];
  const shown = Math.min(matches.length, Math.max(SIDEBAR_PAGE_BATCH, requested));
  const visible = matches.slice(0, shown);
  // A deep link or search may open Work 500 without scrolling 499 DOM rows
  // into the sidebar. Keep that Work visible and source-bound.
  const active = matches.find(page => page.id === activePageId);
  if (active && !visible.some(page => page.id === active.id)) visible.unshift(active);
  return { visible, total: matches.length, shown, hasMore: shown < matches.length };
}
