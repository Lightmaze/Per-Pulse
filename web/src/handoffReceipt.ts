/** Recover uncertain cross-Work sends without copying source text into browser storage.
 *
 * The native CausalLedger remains the only admission/idempotency authority.
 * Browser storage holds only a SHA-256 request fingerprint and a random request
 * ID while an HTTP response may have been lost. Each distinct payload needs
 * its own identity; a successful response releases only its matching pending
 * identity so a later deliberate send can create a new native event.
 */
type HandoffKind = 'return' | 'transfer';
export type PendingHandoff = { requestId: string; fingerprint: string | null };

type Saved = { fingerprint: string; requestId: string };
const PREFIX = 'per-pulse.ui.v2.';
const LIMIT = 24;
const key = (worldId: string) =>
  `${PREFIX}${encodeURIComponent(worldId)}.pending-handoffs`;
const isSaved = (value: unknown): value is Saved => {
  if (!value || typeof value !== 'object') return false;
  const row = value as Record<string, unknown>;
  return typeof row.fingerprint === 'string' && /^[a-f0-9]{64}$/.test(row.fingerprint)
    && typeof row.requestId === 'string' && /^[0-9a-f-]{36}$/.test(row.requestId);
};

function read(worldId: string): Saved[] {
  try {
    const rows: unknown = JSON.parse(localStorage.getItem(key(worldId)) ?? 'null');
    return Array.isArray(rows) ? rows.filter(isSaved).slice(-LIMIT) : [];
  } catch {
    return [];
  }
}

/** The fingerprint includes World-scoped source and destination identities,
 * the selected source message, and the exact new user request. The serialized
 * text is used only in memory for hashing, never persisted.
 */
export async function reserveHandoff(
  worldId: string, kind: HandoffKind, payload: object, fallbackId: string,
): Promise<PendingHandoff> {
  try {
    const bytes = new TextEncoder().encode(JSON.stringify({ kind, payload }));
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    const fingerprint = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
    const rows = read(worldId);
    const existing = rows.find(row => row.fingerprint === fingerprint);
    if (existing) return { requestId: existing.requestId, fingerprint };

    const requestId = crypto.randomUUID();
    // A new, different instruction never inherits the UUID of an uncertain
    // prior action, even when the person edits the still-open dialog.
    localStorage.setItem(key(worldId), JSON.stringify(
      [...rows, { fingerprint, requestId }].slice(-LIMIT),
    ));
    return { requestId, fingerprint };
  } catch {
    // Restricted storage or crypto must not block ordinary interaction. Within
    // one surviving dialog the fallback still makes a retry idempotent.
    return { requestId: fallbackId, fingerprint: null };
  }
}

export function confirmHandoff(worldId: string, pending: PendingHandoff): void {
  if (!pending.fingerprint) return;
  try {
    const next = read(worldId).filter(row =>
      row.fingerprint !== pending.fingerprint || row.requestId !== pending.requestId);
    if (next.length) localStorage.setItem(key(worldId), JSON.stringify(next));
    else localStorage.removeItem(key(worldId));
  } catch {
    // Native admission succeeded; an unavailable browser preference store
    // cannot undo the admitted Work or its source provenance.
  }
}
