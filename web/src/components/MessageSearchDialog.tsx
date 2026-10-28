import { useEffect, useId, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { MessageHistory } from '../types';
import { Dialog } from './Dialog';
import { ReplayOrigin } from './ReplayOrigin';
import type { Navigate } from './SessionLink';

export function MessageSearchDialog({ pageId, close, navigate }: {
  pageId: string; close: () => void; navigate: Navigate;
}) {
  const { t, locale, errorText } = useLanguage();
  const inputId = useId();
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [result, setResult] = useState<MessageHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const content = useRef<HTMLDivElement>(null);
  const cursor = cursors[cursors.length - 1];

  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setResult(null);
    api.searchMessages(pageId, query, cursor, controller.signal).then(next => {
      if (!controller.signal.aborted) setResult(next);
    }).catch(problem => {
      if (!controller.signal.aborted) setError(problem);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [pageId, query, cursor, retry]);

  useEffect(() => {
    if (result && content.current) content.current.scrollTop = 0;
  }, [result]);

  const date = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
  return <Dialog title={t('find.title')} onClose={close} className="history-dialog">
    <p className="dialog-description">{t('find.description')}</p>
    <form className="message-search-form" onSubmit={event => {
      event.preventDefault();
      if (!draft.trim()) return;
      setResult(null);
      setError(null);
      setLoading(true);
      setQuery(draft.trim());
      setCursors([null]);
      setRetry(value => value + 1);
    }}>
      <label htmlFor={inputId}>{t('find.label')}</label>
      <div><input id={inputId} type="search" maxLength={200} value={draft} onChange={event => setDraft(event.target.value)} /><button className="primary-button" disabled={!draft.trim()}>{t('find.submit')}</button></div>
    </form>
    <div className="history-messages" ref={content} aria-busy={loading}>
      {!query ? <p className="conversation-note">{t('find.hint')}</p> : null}
      {loading ? <p role="status">{t('find.loading')}</p> : null}
      {error ? <div className="error-notice" role="alert"><span>{errorText(error)}</span><button onClick={() => setRetry(value => value + 1)}>{t('app.retry')}</button></div> : null}
      {result && !result.messages.length ? <p role="status">{t('find.empty', { query })}</p> : null}
      {result?.messages.map(message => <article className="history-message" key={message.id} data-search-message-id={message.id}>
        <div className="history-message-heading"><strong>{message.role === 'assistant' ? 'Per Pulse' : t(message.role === 'user' ? 'conversation.you' : 'conversation.workspace')}</strong><time dateTime={message.createdAt}>{date.format(new Date(message.createdAt))}</time></div>
        <ReplayOrigin message={message} pageId={pageId} navigate={navigate} />
        <p className="history-message-content">{message.content.slice(message.replay?.contentOffset ?? 0)}</p>
        <button className="text-button" onClick={() => navigate(pageId, undefined, message.id)}>{t('history.open')}</button>
      </article>)}
    </div>
    {query ? <div className="history-pagination">
      <button className="secondary-button" disabled={loading || !result?.beforeMessageId} onClick={() => {
        if (result?.beforeMessageId) setCursors(previous => [...previous, result.beforeMessageId!]);
      }}>{t('find.older')}</button>
      <p role="status">{result ? t('find.count', { count: result.messages.length, query }) : ''}</p>
      <button className="secondary-button" disabled={loading || cursors.length < 2} onClick={() => setCursors(previous => previous.slice(0, -1))}>{t('find.newer')}</button>
    </div> : null}
  </Dialog>;
}
