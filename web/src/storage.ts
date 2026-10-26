import type { Draft, NavigationOrigin } from './types';

const PREFIX = 'per-pulse.ui.v2.';
export const EMPTY_DRAFT: Draft = { text: '', references: [] };
const storageKey = (workspaceId: string, kind: 'draft' | 'position', pageId: string) => `${PREFIX}${encodeURIComponent(workspaceId)}.${kind}.${encodeURIComponent(pageId)}`;

export function readDraft(workspaceId: string, pageId: string): Draft {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(workspaceId, 'draft', pageId)) ?? 'null');
    if (saved && typeof saved.text === 'string' && Array.isArray(saved.references)) {
      return { text: saved.text, references: saved.references.filter((r: unknown) => typeof r === 'object' && r !== null && 'targetPageId' in r && typeof r.targetPageId === 'string' && 'label' in r && typeof r.label === 'string') };
    }
  } catch { /* A restricted or full browser storage must not break the workspace. */ }
  return { ...EMPTY_DRAFT, references: [] };
}

export function saveDraft(workspaceId: string, pageId: string, draft: Draft) {
  try {
    if (!draft.text && !draft.references.length) localStorage.removeItem(storageKey(workspaceId, 'draft', pageId));
    else localStorage.setItem(storageKey(workspaceId, 'draft', pageId), JSON.stringify(draft));
  } catch { /* Draft editing remains available when browser storage is unavailable. */ }
}

export function savePosition(workspaceId: string, pageId: string, position: number) {
  try { localStorage.setItem(storageKey(workspaceId, 'position', pageId), String(position)); } catch { /* optional UI preference */ }
}

export function readPosition(workspaceId: string, pageId: string) {
  try {
    const position = Number(localStorage.getItem(storageKey(workspaceId, 'position', pageId)));
    return Number.isFinite(position) ? Math.max(0, position) : 0;
  } catch { return 0; }
}

export const pageHref = (id: string, targetMessageId?: string) => `${id === 'home' ? '/' : `/page/${encodeURIComponent(id)}`}${targetMessageId ? `#message-${encodeURIComponent(targetMessageId)}` : ''}`;

export function pageFromLocation(source: Pick<Location, 'pathname'> = location) {
  const match = source.pathname.match(/^\/page\/([^/]+)\/?$/);
  try { return match ? decodeURIComponent(match[1]) : 'home'; } catch { return 'home'; }
}

export function messageFromLocation(source: Pick<Location, 'hash'> = location) {
  const match = source.hash.match(/^#message-(.+)$/);
  try { return match ? decodeURIComponent(match[1]) : undefined; } catch { return undefined; }
}

export type NavigationState = {
  workspaceId: string;
  pageId: string;
  origin?: NavigationOrigin;
  targetMessageId?: string;
  restoreFocus?: NavigationOrigin;
  canGoBack: boolean;
};

function readOrigin(value: unknown): NavigationOrigin | undefined {
  if (!value || typeof value !== 'object' || !('pageId' in value) || typeof value.pageId !== 'string' || !value.pageId) return undefined;
  return {
    pageId: value.pageId,
    messageId: 'messageId' in value && typeof value.messageId === 'string' ? value.messageId : undefined,
    referenceId: 'referenceId' in value && typeof value.referenceId === 'string' ? value.referenceId : undefined,
  };
}

export function readNavigationState(value: unknown, workspaceId: string | undefined, pageId: string): NavigationState | undefined {
  if (!workspaceId || !value || typeof value !== 'object' || !('workspaceId' in value) || value.workspaceId !== workspaceId || !('pageId' in value) || value.pageId !== pageId) return undefined;
  const origin = readOrigin('origin' in value ? value.origin : undefined);
  const restoreFocus = readOrigin('restoreFocus' in value ? value.restoreFocus : undefined);
  return {
    workspaceId, pageId, origin,
    targetMessageId: 'targetMessageId' in value && typeof value.targetMessageId === 'string' ? value.targetMessageId : undefined,
    restoreFocus: restoreFocus?.pageId === pageId ? restoreFocus : undefined,
    canGoBack: Boolean(origin && origin.pageId !== pageId && 'canGoBack' in value && value.canGoBack === true),
  };
}
