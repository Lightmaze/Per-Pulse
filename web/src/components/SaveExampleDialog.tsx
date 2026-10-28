// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { Message } from '../types';
import { Dialog } from './Dialog';

export function SaveExampleDialog({ pageId, message, initial, messages, close, saved }: {
  pageId: string; message: Message; initial: string; messages: Message[];
  close: () => void; saved: () => void;
}) {
  const { t, errorText } = useLanguage();
  const [title, setTitle] = useState('');
  const [excerpt, setExcerpt] = useState(initial);
  const [note, setNote] = useState('');
  const [feedbackMessageId, setFeedbackMessageId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const mounted = useRef(false);
  const writing = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const sourceIndex = messages.findIndex(item => item.id === message.id);
  const followups = sourceIndex < 0 ? [] : messages.slice(sourceIndex + 1).filter(item => item.role === 'user');
  const exact = Boolean(excerpt.trim()) && message.content.includes(excerpt);
  const excerptBytes = new TextEncoder().encode(excerpt).length;
  const noteBytes = new TextEncoder().encode(note.trim()).length;
  const titleBytes = new TextEncoder().encode(title.trim()).length;
  const valid = Boolean(title.trim()) && titleBytes <= 240 && exact && excerptBytes <= 8000 && noteBytes <= 4000;
  const save = async () => {
    if (!valid || writing.current) return;
    writing.current = true; setBusy(true); setError(null);
    try {
      await api.saveExample(pageId, {
        sourceMessageId: message.id, title: title.trim(), excerpt, note: note.trim(),
        ...(feedbackMessageId ? { feedbackMessageId } : {}),
      });
      if (mounted.current) saved();
    } catch (problem) {
      if (mounted.current) setError(problem);
    } finally {
      writing.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return <Dialog title={t('learning.saveExample')} onClose={() => { if (!writing.current) close(); }} className="save-example-dialog">
    <p className="dialog-description">{t('learning.saveExampleHelp')}</p>
    {message.replay ? <p className="lesson-note">{t('replay.saved')}</p> : null}
    <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <label className="field-label" htmlFor="example-title">{t('learning.exampleTitle')}</label>
      <input id="example-title" value={title} onChange={event => setTitle(event.target.value)} maxLength={160} disabled={busy} required />
      {titleBytes > 240 ? <p className="form-error">{t('learning.titleLong')}</p> : null}
      <label className="field-label" htmlFor="example-excerpt">{t('learning.excerpt')}</label>
      <textarea id="example-excerpt" rows={8} value={excerpt} onChange={event => setExcerpt(event.target.value)} aria-describedby="example-excerpt-help" disabled={busy} required />
      <p className="lesson-note" id="example-excerpt-help">{t('learning.excerptHelp')}</p>
      {!exact && excerpt ? <p className="form-error">{t('learning.excerptMismatch')}</p> : null}
      {excerptBytes > 8000 ? <p className="form-error">{t('learning.excerptLong')}</p> : null}
      <label className="field-label" htmlFor="example-note">{t('learning.exampleNote')}</label>
      <textarea id="example-note" rows={3} value={note} onChange={event => setNote(event.target.value)} maxLength={4000} disabled={busy} />
      {noteBytes > 4000 ? <p className="form-error">{t('learning.noteLong')}</p> : null}
      <label className="field-label" htmlFor="example-followup">{t('learning.followup')}</label>
      <select id="example-followup" value={feedbackMessageId} onChange={event => setFeedbackMessageId(event.target.value)} disabled={busy}>
        <option value="">{t('learning.noFollowup')}</option>
        {followups.map((item, index) => <option key={item.id} value={item.id}>{index + 1}. {item.content.replace(/\s+/g, ' ').slice(0, 120)}</option>)}
      </select>
      <p className="lesson-note">{t('learning.followupHelp')}</p>
      {error ? <p className="form-error" role="alert">{errorText(error)}</p> : null}
      <div className="dialog-actions">
        <button className="secondary-button" type="button" disabled={busy} onClick={close}>{t('dialogs.cancel')}</button>
        <button className="primary-button" type="submit" disabled={busy || !valid}>{t(busy ? 'dialogs.saving' : 'learning.saveExample')}</button>
      </div>
    </form>
  </Dialog>;
}
