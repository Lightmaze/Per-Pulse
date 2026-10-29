import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { MessageHistory } from '../types';
import { Dialog } from './Dialog';
import type { Navigate } from './SessionLink';
import { ReplayOrigin } from './ReplayOrigin';
import { messageBody } from '../messageBody';

export function HistoryDialog({ pageId, beforeMessageId, close, navigate }: {
  pageId: string; beforeMessageId: string; close: () => void; navigate: Navigate;
}) {
  const { t, locale, errorText } = useLanguage();
  // Only cursors are retained as the reader moves. The native ledger remains
  // authoritative; returning to a range rereads its current message statuses.
  const [cursors, setCursors] = useState([beforeMessageId]);
  const [history, setHistory] = useState<MessageHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  const content = useRef<HTMLDivElement>(null);
  const cursor = cursors[cursors.length - 1];

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setHistory(null);
    api.history(pageId, cursor, controller.signal).then(result => {
      if (!controller.signal.aborted) setHistory(result);
    }).catch(problem => {
      if (!controller.signal.aborted) setError(problem);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [pageId, cursor, retry]);

  useEffect(() => {
    if (history && content.current) content.current.scrollTop = 0;
  }, [history]);

  const date = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
  return <Dialog title={t('history.title')} onClose={close} className="history-dialog">
    <p className="dialog-description">{t('history.description')}</p>
    <div className="history-messages" ref={content} aria-busy={loading}>
      {loading ? <p role="status">{t('history.loading')}</p> : null}
      {error ? <div className="error-notice" role="alert"><span>{errorText(error)}</span><button onClick={() => setRetry(value => value + 1)}>{t('app.retry')}</button></div> : null}
      {history?.messages.map(message => <article className="history-message" key={message.id} data-history-message-id={message.id}>
        <div className="history-message-heading"><strong>{message.role === 'assistant' ? 'Per Pulse' : t(message.role === 'user' ? 'conversation.you' : 'conversation.workspace')}</strong><time dateTime={message.createdAt}>{date.format(new Date(message.createdAt))}</time></div>
        <ReplayOrigin message={message} pageId={pageId} navigate={navigate} />
        <p className="history-message-content">{messageBody(message)}</p>
        {message.materials?.length ? <p className="lesson-note">{t('materials.attached')}: {message.materials.map(item => item.name).join(', ')}</p> : null}
        <button className="text-button" onClick={() => navigate(pageId, undefined, message.id)}>{t('history.open')}</button>
      </article>)}
      {history && !history.hasMore ? <p className="conversation-note">{t('history.beginning')}</p> : null}
    </div>
    <div className="history-pagination">
      <button className="secondary-button" disabled={loading || !history?.beforeMessageId} onClick={() => {
        if (history?.beforeMessageId) setCursors(previous => [...previous, history.beforeMessageId!]);
      }}>{t('history.older')}</button>
      <p role="status">{history ? t('history.count', { count: history.messages.length }) : ''}</p>
      <button className="secondary-button" disabled={loading} onClick={() => cursors.length > 1 ? setCursors(previous => previous.slice(0, -1)) : close()}>{t(cursors.length > 1 ? 'history.newer' : 'history.recent')}</button>
    </div>
  </Dialog>;
}
