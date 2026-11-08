// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { Message } from '../types';
import { useLanguage } from '../i18n';
import { MessageContent } from './MessageContent';
import { SessionLink, type Navigate } from './SessionLink';

/** Quote authorship is independent of the user event that requested the new page. */
export function PageSourceContent({ message, pageId, navigate, sourceTitle }: {
  message: Message; pageId: string; navigate: Navigate; sourceTitle?: string;
}) {
  const { t } = useLanguage();
  const source = message.pageSource;
  if (!source) return null;
  const author = source.role === 'assistant' ? 'Per Pulse'
    : t(source.role === 'user' ? 'conversation.you' : 'conversation.workspace');
  return <div className="transfer-presentation">
    <section className="transfer-presentation-source" aria-label={t('source.excerpt')}>
      <div className="transfer-presentation-label">{source.edited ? t('source.edited') : author}</div>
      {message.sourcePageId ? <div className="source-line">{t('conversation.from')} <SessionLink
        pageId={message.sourcePageId} targetMessageId={message.sourceMessageId ?? undefined}
        label={sourceTitle ?? t('conversation.sourcePage')} origin={{ pageId, messageId: message.id }} navigate={navigate} />
        {source.edited ? <span> / {author}</span> : null}</div> : null}
      <MessageContent content={source.excerpt} referenceKey="[]" pageId={pageId} messageId={`${message.id}-source`} navigate={navigate} />
    </section>
    <section className="transfer-presentation-request" aria-label={t('source.request')}>
      <div className="transfer-presentation-label">{t('source.request')}</div>
      <MessageContent content={source.instruction} referenceKey="[]" pageId={pageId} messageId={`${message.id}-request`} navigate={navigate} />
    </section>
  </div>;
}
