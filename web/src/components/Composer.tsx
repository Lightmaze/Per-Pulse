import { useLayoutEffect, useRef, type RefObject } from 'react';
import type { Draft } from '../types';
import { useLanguage } from '../i18n';
import { Icon } from './Icon';

export function Composer({ draft, changeDraft, send, busy, onLink, inputRef }: {
  draft: Draft; changeDraft: (draft: Draft) => void; send: () => void; busy: boolean; onLink: () => void; inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const { t } = useLanguage();
  const composing = useRef(false);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = 'auto'; input.style.height = `${Math.min(220, Math.max(38, input.scrollHeight))}px`; }
  }, [draft.text, inputRef]);
  return <div className="composer-wrap">
    <form className="composer" onSubmit={event => { event.preventDefault(); if (draft.text.trim() && !busy) send(); }}>
      <textarea ref={inputRef} value={draft.text} onChange={event => changeDraft({ ...draft, text: event.target.value })} placeholder={t('composer.placeholder')} aria-label={t('composer.message')} rows={1} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); if (draft.text.trim() && !busy) send(); }
      }} />
      <div className="composer-toolbar">
        <button type="button" className="text-button link-page-button" onClick={onLink}><Icon name="link" size={17} />{t('composer.linkPage')}{draft.references.length ? <span className="reference-count">{draft.references.length}</span> : null}</button>
        <button className="send-button" type="submit" aria-label={t('composer.send')} disabled={busy || !draft.text.trim()}><Icon name="send" size={21} /></button>
      </div>
    </form>
    <p className="composer-hint">{t(busy ? 'composer.saving' : 'composer.hint')}</p>
  </div>;
}
