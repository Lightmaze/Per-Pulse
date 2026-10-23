import type { PageSummary } from './types';

export type ActivityTone = 'neutral' | 'active' | 'attention' | 'error';
export type Activity = { label: string; detail: string; tone: ActivityTone };

export function getPageActivity(status: PageSummary['status'], configured: boolean, stale = false): Activity {
  if (status === 'error') return { label: 'Turn interrupted', detail: 'Your message is saved. Review the conversation before continuing.', tone: 'error' };
  if (status === 'needs_attention') return { label: 'Needs attention', detail: 'This turn is paused and needs attention before it can continue.', tone: 'attention' };
  if (stale) return { label: 'Updates paused', detail: 'The latest status could not be checked. Refresh to try again.', tone: 'neutral' };
  if (status === 'queued' || status === 'working') {
    if (!configured) return { label: 'Saved · AI disconnected', detail: 'Your message is saved. Connect a model to continue this turn.', tone: 'neutral' };
    return status === 'working'
      ? { label: 'Working', detail: 'Per Pulse is working in this conversation.', tone: 'active' }
      : { label: 'Queued', detail: 'Your message is saved and waiting for its turn.', tone: 'neutral' };
  }
  return { label: 'Ready', detail: 'No turn is active. Continue whenever you are ready.', tone: 'neutral' };
}

export function getMessageActivity(status: string, configured: boolean, stale = false): Activity | null {
  if (status === 'uncertain') return { label: 'Needs review', detail: 'The outcome of this turn could not be confirmed. Review the conversation before continuing.', tone: 'attention' };
  if (status === 'failed' || status === 'error') return getPageActivity('error', configured);
  if (status === 'blocked' || status === 'awaiting_approval' || status === 'waiting_approval') {
    return { label: 'Needs attention', detail: status === 'blocked' ? 'This turn is paused and needs attention before it can continue.' : 'Approval is needed before this turn can continue.', tone: 'attention' };
  }
  if (status === 'queued') return getPageActivity('queued', configured, stale);
  if (['running', 'working', 'admitted', 'processing'].includes(status)) return getPageActivity('working', configured, stale);
  if (status === 'cancelled') return { label: 'Stopped', detail: 'This turn stopped. Your saved message is still here.', tone: 'neutral' };
  return null;
}
