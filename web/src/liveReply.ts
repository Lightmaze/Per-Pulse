import { useEffect, useState } from 'react';

/** Reply text the model is writing now on a page. */
export type LiveReply = { turnId: string; roundId: string; text: string };

/** One message from `/api/pages/{id}/reply/stream`. */
export type LiveReplyEvent = { turnId: string | null; roundId?: string; text?: string; append?: string };

/**
 * Apply one stream message. A full `text` starts a new model round (or a new
 * connection); `append` extends the same round. When nothing is being written
 * (`turnId: null`), the last text stays until the page shows the finished
 * reply, so the reply does not flicker away while it is being saved.
 */
export function applyLiveReplyEvent(current: LiveReply | null, event: LiveReplyEvent): LiveReply | null {
  if (!event.turnId) return current;
  if (typeof event.text === 'string') return { turnId: event.turnId, roundId: event.roundId ?? '', text: event.text };
  if (typeof event.append === 'string' && current
    && current.turnId === event.turnId && current.roundId === (event.roundId ?? '')) {
    return { ...current, text: current.text + event.append };
  }
  return current;
}

const RECONNECT_MS = 1500;

/** Follow the reply as the model writes it, while the page is working. */
export function useLiveReply(pageId: string, active: boolean): LiveReply | null {
  const [reply, setReply] = useState<LiveReply | null>(null);
  useEffect(() => {
    setReply(null);
    if (!active || typeof EventSource === 'undefined') return;
    let source: EventSource | null = null;
    let timer: number | undefined;
    let closed = false;
    const open = () => {
      source = new EventSource(`/api/pages/${encodeURIComponent(pageId)}/reply/stream`);
      source.onmessage = message => {
        let event: LiveReplyEvent;
        try { event = JSON.parse(message.data); } catch { return; }
        setReply(current => applyLiveReplyEvent(current, event));
      };
      // The server ends the stream when a reply is done or none has started.
      // Keep following while the page is still working.
      source.addEventListener('end', () => {
        source?.close();
        if (!closed) timer = window.setTimeout(open, RECONNECT_MS);
      });
    };
    open();
    return () => { closed = true; source?.close(); window.clearTimeout(timer); };
  }, [pageId, active]);
  return active ? reply : null;
}
