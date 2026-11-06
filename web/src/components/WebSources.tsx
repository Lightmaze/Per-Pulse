/** Read-only links from verified native web observations. */
import { useLanguage } from '../i18n';
import type { PageView } from '../types';
import { MessageContent } from './MessageContent';
import type { Navigate } from './SessionLink';

export function WebSources({ view, navigate }: { view: PageView; navigate: Navigate }) {
  const { t } = useLanguage();
  const sources = view.execution.webSources;
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
    </li>)}</ul>
    {sources.truncated ? <p>{t('webSources.recent')}</p> : null}
  </details>;
}
