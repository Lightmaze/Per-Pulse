import { useId, useLayoutEffect, useRef, type RefObject } from 'react';
import type { Draft, ProviderState } from '../types';
import { Icon } from './Icon';
import { useLanguage } from '../i18n';

export function Composer({ draft, changeDraft, send, busy, onLink, inputRef, pageTitle, pageKind = 'home', provider, onSettings }: {
  draft: Draft; changeDraft: (draft: Draft) => void; send: () => void; busy: boolean; onLink: () => void; inputRef: RefObject<HTMLTextAreaElement | null>;
  pageTitle?: string; pageKind?: 'home' | 'work'; provider?: ProviderState; onSettings?: () => void;
}) {
  const { t, pageTitle: localizedPageTitle } = useLanguage();
  const composing = useRef(false);
  const connectionId = useId();
  const destination = pageKind === 'home' ? localizedPageTitle({ id: 'home', title: 'Home' }) : pageTitle || t('composer.thisPage');
  const disconnected = provider && !provider.configured;
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = 'auto'; input.style.height = `${Math.min(220, Math.max(38, input.scrollHeight))}px`; }
  }, [draft.text, inputRef]);
  return <div className="composer-wrap">
    <form className="composer" onSubmit={event => { event.preventDefault(); if (draft.text.trim() && !busy) send(); }}>
      <textarea ref={inputRef} value={draft.text} onChange={event => changeDraft({ ...draft, text: event.target.value })} placeholder={pageKind === 'work' ? t('composer.continueIn', { destination }) : t('composer.placeholder')} aria-label={t('composer.messageTo', { destination })} aria-describedby={disconnected ? connectionId : undefined} rows={1} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); if (draft.text.trim() && !busy) send(); }
      }} />
      <div className="composer-toolbar">
        <button type="button" className="text-button link-page-button" onClick={onLink}><Icon name="link" size={17} />{t('composer.linkPage')}{draft.references.length ? <span className="reference-count">{draft.references.length}</span> : null}</button>
        <button className="send-button" type="submit" aria-label={t('composer.send')} disabled={busy || !draft.text.trim()}><Icon name="send" size={21} /></button>
      </div>
    </form>
    {disconnected ? <p id={connectionId} className="composer-connection">{t('composer.disconnected')}{onSettings ? <button type="button" className="text-button composer-connection-action" onClick={onSettings}>{t('composer.connectionSettings')}</button> : null}</p> : null}
    <p className="composer-hint">{busy ? t('composer.saving') : t('composer.hint')}</p>
  </div>;
}
