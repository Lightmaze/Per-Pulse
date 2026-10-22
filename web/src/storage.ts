import type { Draft } from './types';

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

export const pageHref = (id: string) => id === 'home' ? '/' : `/page/${encodeURIComponent(id)}`;

export function pageFromLocation() {
  const match = location.pathname.match(/^\/page\/([^/]+)\/?$/);
  try { return match ? decodeURIComponent(match[1]) : 'home'; } catch { return 'home'; }
}
