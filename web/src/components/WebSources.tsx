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
  view: PageView; navigate: Navigate; onReviewExperience: () => void;
  onContinueWork: (experience: { id: string; title: string; excerpt: string }) => void;
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
  const [savedExperience, setSavedExperience] = useState<{ id: string; title: string; excerpt: string } | null>(null);
  const [saveError, setSaveError] = useState('');
  const saveAttempt = useRef(0);
  const savedText = useRef<HTMLPreElement | null>(null);
  const excerptInput = useRef<HTMLTextAreaElement | null>(null);
  const matchRef = useRef<HTMLElement | null>(null);
  const [findQuery, setFindQuery] = useState('');
  const [matchStart, setMatchStart] = useState(-1);
  const [findSearched, setFindSearched] = useState(false);
  const foundMatch = observation !== null && matchStart >= 0 && findQuery.length > 0
    && observation.content.slice(matchStart, matchStart + findQuery.length) === findQuery;

  useEffect(() => {
    if (foundMatch) matchRef.current?.scrollIntoView({ block: 'center', inline: 'nearest' });
  }, [foundMatch, matchStart]);


  useEffect(() => {
    // A new World, Work or service cannot inherit the earlier receipt's text.
    reader.current?.abort();
    reader.current = null;
    currentReceipt.current = null;
    saveAttempt.current++;

    setOpened(null);
    setObservation(null);
    setReading(false);
    setError('');
    setExampleTitle('');
    setExcerpt('');
    setNote('');
    setSaving(false);
    setSaved(false);
    setSavedExperience(null);
    setSaveError('');
    setFindQuery('');
    setMatchStart(-1);
    setFindSearched(false);
    return () => reader.current?.abort();
  }, [view.workspaceId, view.serviceInstanceId, view.page.id]);

  const inspect = async (actionId: string) => {
    // Even reopening the same receipt is a new editor, not an old save completion.
    saveAttempt.current++;
    reader.current?.abort();
    reader.current = null;
    currentReceipt.current = opened === actionId ? null : actionId;
    setSaved(false);
    setSavedExperience(null);
    setSaving(false);
    setSaveError('');
    setExampleTitle('');
    setExcerpt('');
    setNote('');
    setFindQuery('');
    setMatchStart(-1);
    setFindSearched(false);
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

  const findNext = () => {
    if (!observation || !findQuery.trim()) return;
    const from = matchStart < 0 ? 0 : matchStart + findQuery.length;
    let next = observation.content.indexOf(findQuery, from);
    if (next < 0) next = observation.content.indexOf(findQuery);
    setMatchStart(next);
    setFindSearched(true);
  };

  const useFoundPassage = () => {
    if (!observation || !foundMatch || saving) return;
    // Keep exact adjacent source text, not an AI-authored paraphrase.
    const start = Math.max(0, matchStart - 180);
    const end = Math.min(observation.content.length, matchStart + findQuery.length + 180);
    setExcerpt(observation.content.slice(start, end));
    setSaveError('');
    setSaved(false);
    setSavedExperience(null);
    excerptInput.current?.focus();
  };

  const useSelectedPassage = () => {
    const selection = window.getSelection();
    const text = savedText.current;
    if (!observation || !text || !selection || selection.rangeCount !== 1 || selection.isCollapsed) {
      setSaveError(t('webSources.selectHelp'));
      return;
    }
    const range = selection.getRangeAt(0);
    // Only the original displayed observation can supply the exact passage.
    // A selection extending into another source or the editor is not evidence.
    if (!text.contains(range.startContainer) || !text.contains(range.endContainer)) {
      setSaveError(t('webSources.selectHelp'));
      return;
    }
    const passage = range.toString();
    if (!passage.trim() || !observation.content.includes(passage)
        || new TextEncoder().encode(passage).length > 8000) {
      setSaveError(t('webSources.exampleMismatch'));
      return;
    }
    setExcerpt(passage);
    setSaveError('');
    setSaved(false);
    setSavedExperience(null);
    excerptInput.current?.focus();
  };

  const saveExample = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!observation || saving) return;
    const receiptId = observation.actionId;
    const attempt = ++saveAttempt.current;
    if (!excerpt.trim() || !observation.content.includes(excerpt)
        || new TextEncoder().encode(excerpt).length > 8000) {
      setSaveError(t('webSources.exampleMismatch'));
      return;
    }
    setSaving(true);
    setSaveError('');
    try {
      const response = await api.saveWebExample(view.page.id, {
        receiptId, title: exampleTitle.trim(), excerpt, note,
      });
      if (response.experience.representation !== 'example') throw new Error('Not a saved passage');
      if (currentReceipt.current === receiptId && saveAttempt.current === attempt) {
        setSavedExperience({ id: response.experience.id, title: response.experience.title,
          excerpt: response.experience.excerpt });
        setSaved(true);
      }
    } catch {
      if (currentReceipt.current === receiptId && saveAttempt.current === attempt) setSaveError(t('webSources.exampleFailed'));
    } finally {
      if (currentReceipt.current === receiptId && saveAttempt.current === attempt) setSaving(false);
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
          <form className="web-observation-find" onSubmit={event => { event.preventDefault(); findNext(); }}>
            <label>{t('webSources.find')}
              <input type="search" maxLength={160} value={findQuery}
                onChange={event => { setFindQuery(event.target.value); setMatchStart(-1); setFindSearched(false); }} />
            </label>
            <button type="submit" className="secondary-button" disabled={!findQuery.trim()}>{t('webSources.findNext')}</button>
            {findSearched ? <span role="status">{foundMatch
              ? t('webSources.foundAt', { start: observation.textStart + [...observation.content.slice(0, matchStart)].length })
              : t('webSources.notFound')}</span> : null}
          </form>
          <pre ref={savedText} tabIndex={0} className="web-observation-text">{foundMatch
            ? <>{observation.content.slice(0, matchStart)}<mark ref={matchRef}>{observation.content.slice(matchStart, matchStart + findQuery.length)}</mark>{observation.content.slice(matchStart + findQuery.length)}</>
            : observation.content}</pre>
          {foundMatch ? <button type="button" className="text-button" disabled={saving}
            onClick={useFoundPassage}>{t('webSources.useFound')}</button> : null}
          {observation.readingNote ? <p>{observation.readingNote}</p> : null}
          <CopyButton text={observation.content} label={t('webSources.copy')} />
          <button type="button" className="text-button" disabled={saving}
            onClick={useSelectedPassage}>{t('webSources.useSelection')}</button>
          <form className="web-observation-example" onSubmit={event => void saveExample(event)}>
            <h4>{t('webSources.exampleHeading')}</h4>
            <p>{t('webSources.exampleHelp')}</p>
            <label>{t('webSources.exampleTitle')}
              <input type="text" required maxLength={240} disabled={saving} value={exampleTitle}
                onChange={event => { setExampleTitle(event.target.value); setSaved(false); setSavedExperience(null); }} />
            </label>
            <label>{t('webSources.exampleExcerpt')}
              <textarea ref={excerptInput} required rows={3} disabled={saving} value={excerpt}
                onChange={event => { setExcerpt(event.target.value); setSaveError(''); setSaved(false); setSavedExperience(null); }} />
            </label>
            <label>{t('webSources.exampleNote')}
              <textarea rows={2} disabled={saving} value={note}
                onChange={event => { setNote(event.target.value); setSaved(false); setSavedExperience(null); }} />
            </label>
            {saveError ? <p role="alert">{saveError}</p> : null}
            {saved && savedExperience ? <>
              <p role="status">{t('webSources.exampleSaved')}</p>
              <div className="web-observation-next">
                <button type="button" className="secondary-button" onClick={onReviewExperience}>
                  {t('webSources.reviewSaved')}
                </button>
                <button type="button" className="secondary-button" onClick={() => onContinueWork(savedExperience)}>
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
