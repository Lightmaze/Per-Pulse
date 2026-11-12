/** Page-scoped native observations; selected exact passages can become experience. */
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { PageView, WebObservation } from '../types';
import { CopyButton } from './CopyButton';
import { MessageContent } from './MessageContent';
import type { Navigate } from './SessionLink';

export function WebSources({ view, navigate, onReviewExperience, onContinueWork }: {
  view: PageView; navigate: Navigate; onReviewExperience: () => void; onContinueWork: () => void;
}) {
  const { t } = useLanguage();
  const sources = view.execution.webSources;
  const [opened, setOpened] = useState<string | null>(null);
  const [observation, setObservation] = useState<WebObservation | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState('');
  const reader = useRef<AbortController | null>(null);
  const currentReceipt = useRef<string | null>(null);
  const [exampleTitle, setExampleTitle] = useState('');
  const [excerpt, setExcerpt] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    // A new World, Work or service cannot inherit the earlier receipt's text.
    reader.current?.abort();
    reader.current = null;
    currentReceipt.current = null;
    setOpened(null);
    setObservation(null);
    setReading(false);
    setError('');
    setExampleTitle('');
    setExcerpt('');
    setNote('');
    setSaving(false);
    setSaved(false);
    setSaveError('');
    return () => reader.current?.abort();
  }, [view.workspaceId, view.serviceInstanceId, view.page.id]);

  const inspect = async (actionId: string) => {
    reader.current?.abort();
    reader.current = null;
    currentReceipt.current = opened === actionId ? null : actionId;
    setSaved(false);
    setSaving(false);
    setSaveError('');
    setExampleTitle('');
    setExcerpt('');
    setNote('');
    if (opened === actionId) {
      setOpened(null);
      setObservation(null);
      setReading(false);
      setError('');
      return;
    }
    const request = new AbortController();
    reader.current = request;
    setOpened(actionId);
    setObservation(null);
    setReading(true);
    setError('');
    try {
      const saved = await api.webObservation(view.page.id, actionId, request.signal);
      if (!request.signal.aborted && saved.pageId === view.page.id && saved.actionId === actionId) {
        setObservation(saved);
        setExampleTitle(saved.source.title || t('webSources.exampleDefaultTitle'));
      }
    } catch {
      if (!request.signal.aborted) setError(t('webSources.failed'));
    } finally {
      if (reader.current === request) reader.current = null;
      if (!request.signal.aborted) setReading(false);
    }
  };

  const saveExample = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!observation || saving) return;
    const receiptId = observation.actionId;
    if (!excerpt.trim() || !observation.content.includes(excerpt)
        || new TextEncoder().encode(excerpt).length > 8000) {
      setSaveError(t('webSources.exampleMismatch'));
      return;
    }
    setSaving(true);
    setSaveError('');
    try {
      await api.saveWebExample(view.page.id, {
        receiptId, title: exampleTitle.trim(), excerpt, note,
      });
      if (currentReceipt.current === receiptId) setSaved(true);
    } catch {
      if (currentReceipt.current === receiptId) setSaveError(t('webSources.exampleFailed'));
    } finally {
      if (currentReceipt.current === receiptId) setSaving(false);
    }
  };

  if (!sources?.items.length) return null;
  return <details className="web-sources">
    <summary>{t('webSources.title')} <small>{sources.items.length}</small></summary>
    <p>{t('webSources.scope')}</p>
    <ul>{sources.items.map(source => <li key={source.actionId}>
      {source.title ? <span>{source.title}</span> : null}
      <MessageContent content={`<${source.url.replaceAll('<', '%3C').replaceAll('>', '%3E')}>`}
        referenceKey="[]" pageId={view.page.id} messageId={`web-${source.actionId}`} navigate={navigate} />
      <small>{t('webSources.observed', { time: new Date(source.fetchedAt).toLocaleString() })}</small>
      {source.truncated ? <p>{t('webSources.partial')}</p> : null}
      <button type="button" className="text-button" aria-expanded={opened === source.actionId}
        onClick={() => void inspect(source.actionId)}>
        {t(opened === source.actionId ? 'webSources.hide' : 'webSources.inspect')}
      </button>
      {opened === source.actionId ? <div className="web-observation">
        {reading ? <p role="status">{t('webSources.loading')}</p> : null}
        {error ? <p role="alert">{error}</p> : null}
        {observation?.actionId === source.actionId ? <>
          <p>{t('webSources.savedNote')}</p>
          <p className="web-observation-meta">{t('webSources.range', {
            start: observation.textStart, end: observation.textEnd,
          })} · {t('webSources.digest', { digest: observation.digest })}</p>
          <pre tabIndex={0} className="web-observation-text">{observation.content}</pre>
          {observation.readingNote ? <p>{observation.readingNote}</p> : null}
          <CopyButton text={observation.content} label={t('webSources.copy')} />
          <form className="web-observation-example" onSubmit={event => void saveExample(event)}>
            <h4>{t('webSources.exampleHeading')}</h4>
            <p>{t('webSources.exampleHelp')}</p>
            <label>{t('webSources.exampleTitle')}
              <input type="text" required maxLength={240} value={exampleTitle}
                onChange={event => { setExampleTitle(event.target.value); setSaved(false); }} />
            </label>
            <label>{t('webSources.exampleExcerpt')}
              <textarea required rows={3} value={excerpt}
                onChange={event => { setExcerpt(event.target.value); setSaved(false); }} />
            </label>
            <label>{t('webSources.exampleNote')}
              <textarea rows={2} value={note}
                onChange={event => { setNote(event.target.value); setSaved(false); }} />
            </label>
            {saveError ? <p role="alert">{saveError}</p> : null}
            {saved ? <>
              <p role="status">{t('webSources.exampleSaved')}</p>
              <div className="web-observation-next">
                <button type="button" className="secondary-button" onClick={onReviewExperience}>
                  {t('webSources.reviewSaved')}
                </button>
                <button type="button" className="secondary-button" onClick={onContinueWork}>
                  {t('webSources.continueSaved')}
                </button>
              </div>
            </> : null}
            <button type="submit" disabled={saving || !exampleTitle.trim() || !excerpt.trim()}>
              {t(saving ? 'webSources.exampleSaving' : 'webSources.exampleSave')}
            </button>
          </form>
        </> : null}
      </div> : null}
    </li>)}</ul>
    {sources.truncated ? <p>{t('webSources.recent')}</p> : null}
  </details>;
}
