// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import type { Message } from '../types';
import { useLanguage } from '../i18n';
import { SessionLink, type Navigate } from './SessionLink';

export function ReplayOrigin({ message, pageId, navigate }: {
  message: Message; pageId: string; navigate: Navigate;
}) {
  const { t } = useLanguage();
  if (!message.replay) return null;
  return <aside className="replay-origin" aria-label={t('replay.origin')}>
    <span>{t(message.replay.selected ? 'replay.selected' : 'replay.result')}</span>
    <details>
      <summary>{t('replay.sources')}</summary>
      <p>{t('replay.meaning')}</p>
      <ul>{message.replay.sources.map((source, index) => <li key={source.messageId}>
        <SessionLink pageId={source.pageId} label={t(source.role === 'assistant' ? 'replay.originalReply' : 'replay.originalInput', { count: index + 1 })}
          targetMessageId={source.messageId} origin={{ pageId, messageId: message.id }} navigate={navigate} />
      </li>)}</ul>
      {message.replay.unavailableSources > 0 ? <p>{t('replay.unavailable')}</p> : null}
    </details>
  </aside>;
}
