/** Read-only, page-scoped observations from verified native web actions. */
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { PageView, WebObservation } from '../types';
import { CopyButton } from './CopyButton';
import { MessageContent } from './MessageContent';
import type { Navigate } from './SessionLink';

export function WebSources({ view, navigate }: { view: PageView; navigate: Navigate }) {
  const { t } = useLanguage();
  const sources = view.execution.webSources;
  const [opened, setOpened] = useState<string | null>(null);
  const [observation, setObservation] = useState<WebObservation | null>(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState('');
  const reader = useRef<AbortController | null>(null);

  useEffect(() => {
    // A new World, Work or service cannot inherit the earlier receipt's text.
    reader.current?.abort();
    reader.current = null;
    setOpened(null);
    setObservation(null);
    setReading(false);
    setError('');
    return () => reader.current?.abort();
  }, [view.workspaceId, view.serviceInstanceId, view.page.id]);

  const inspect = async (actionId: string) => {
    reader.current?.abort();
    reader.current = null;
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
      }
    } catch {
      if (!request.signal.aborted) setError(t('webSources.failed'));
    } finally {
      if (reader.current === request) reader.current = null;
      if (!request.signal.aborted) setReading(false);
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
        </> : null}
      </div> : null}
    </li>)}</ul>
    {sources.truncated ? <p>{t('webSources.recent')}</p> : null}
  </details>;
}
