import { useLayoutEffect, useRef, type RefObject } from 'react';
import type { Draft } from '../types';
import { Icon } from './Icon';

export function Composer({ draft, changeDraft, send, busy, onLink, inputRef }: {
  draft: Draft; changeDraft: (draft: Draft) => void; send: () => void; busy: boolean; onLink: () => void; inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const composing = useRef(false);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = 'auto'; input.style.height = `${Math.min(220, Math.max(38, input.scrollHeight))}px`; }
  }, [draft.text, inputRef]);
  return <div className="composer-wrap">
    <form className="composer" onSubmit={event => { event.preventDefault(); if (draft.text.trim() && !busy) send(); }}>
      <textarea ref={inputRef} value={draft.text} onChange={event => changeDraft({ ...draft, text: event.target.value })} placeholder="What would you like to work on?" aria-label="Message" rows={1} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing) { event.preventDefault(); if (draft.text.trim() && !busy) send(); }
      }} />
      <div className="composer-toolbar">
        <button type="button" className="text-button link-page-button" onClick={onLink}><Icon name="link" size={17} />Link a page{draft.references.length ? <span className="reference-count">{draft.references.length}</span> : null}</button>
        <button className="send-button" type="submit" aria-label="Send message" disabled={busy || !draft.text.trim()}><Icon name="send" size={21} /></button>
      </div>
    </form>
    <p className="composer-hint">{busy ? 'Saving your message…' : 'Enter to send · Shift + Enter for a new line'}</p>
  </div>;
}
