// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { Bootstrap, PageView } from './types';

/** A summary fetched before a local result must not undo that result. */
export function mergeWorkspaceSnapshot(
  current: Bootstrap | null, observed: Bootstrap,
  before: Readonly<Record<string, number>>, after: Readonly<Record<string, number>>,
): Bootstrap {
  if (!current || current.workspaceId !== observed.workspaceId || current.serviceInstanceId !== observed.serviceInstanceId) return observed;
  const changed = (id: string) => (before[id] ?? 0) !== (after[id] ?? 0);
  const local = new Map(current.pages.map(page => [page.id, page]));
  const seen = new Set(observed.pages.map(page => page.id));
  return { ...observed, pages: [
    ...observed.pages.map(page => changed(page.id) ? local.get(page.id) ?? page : page),
    ...current.pages.filter(page => !seen.has(page.id) && changed(page.id)),
  ] };
}

/** A page projection may be rendered only in its exact World and service incarnation. */
export function pageBelongsToWorkspace(
  view: Pick<PageView, 'workspaceId' | 'serviceInstanceId'> | null | undefined,
  bootstrap: Pick<Bootstrap, 'workspaceId' | 'serviceInstanceId'> | null | undefined,
): boolean {
  return Boolean(view && bootstrap && view.workspaceId && view.serviceInstanceId
    && view.workspaceId === bootstrap.workspaceId
    && view.serviceInstanceId === bootstrap.serviceInstanceId);
}
