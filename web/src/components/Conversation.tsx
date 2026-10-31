import type { Message, PageSummary, PageView } from '../types';
import { Icon } from './Icon';
import { SessionLink, type Navigate } from './SessionLink';
import { getMessageActivity } from '../status';
import { useLanguage } from '../i18n';
import { MessageContent } from './MessageContent';
import { CopyButton } from './CopyButton';
import { ReplayOrigin } from './ReplayOrigin';
import { messageBody } from '../messageBody';

export function Conversation({ view, pages, navigate, onCreate, onReference, onReturn, onTransfer, onSaveExample, onHistory, onFind, onMaterial, stale = false }: {
  view: PageView; pages: PageSummary[]; navigate: Navigate; onCreate: (message?: Message) => void; onReference: (message: Message) => void; onReturn: (message: Message, selection: string) => void; onHistory: (beforeMessageId: string) => void; onFind: () => void; stale?: boolean;
  onSaveExample?: (message: Message, excerpt: string) => void;
  onMaterial?: (materialId: string, pageId: string) => void;
}) {
  const { t, pageTitle } = useLanguage();
  const pageMap = new Map(pages.map(page => [page.id, page]));
  const pageReferences = view.references.filter(reference => !reference.sourceMessageId);
  return <div className="conversation" aria-label={t("conversation.label")}>
    {view.hasMore ? <p className="conversation-note">{view.earlierSourceMessageId ? t('conversation.earlierSource') : t('conversation.recentMessages')}</p> : null}
    {view.messages.length ? <div className="conversation-history">
      {view.historyBeforeMessageId ? <button className="text-button" onClick={() => onHistory(view.historyBeforeMessageId!)}>{t('history.title')}</button> : null}
      <button className="text-button" onClick={onFind}><Icon name="search" size={14} />{t('find.title')}</button>
    </div> : null}
    {view.messages.map(message => { const activity = getMessageActivity(message.status, view.provider.configured, stale); return <article className={`message message-${message.role}`} id={`message-${message.id}`} data-message-id={message.id} key={message.id} tabIndex={-1} aria-label={t(message.role === 'user' ? 'conversation.yourMessage' : message.role === 'assistant' ? 'conversation.assistantMessage' : 'conversation.workspaceMessage')}>
      <div className="message-label">{message.role === 'user' ? t('conversation.you') : message.role === 'assistant' ? 'Per Pulse' : t('conversation.workspace')}</div>
      {message.sourcePageId && message.sourcePageId !== view.page.id ? <div className="source-line">{t("conversation.from")} <SessionLink pageId={message.sourcePageId} label={pageMap.get(message.sourcePageId) ? pageTitle(pageMap.get(message.sourcePageId)!) : t('conversation.sourcePage')} targetMessageId={message.sourceMessageId ?? undefined} origin={{ pageId: view.page.id, messageId: message.id }} navigate={navigate} /></div> : null}
      <ReplayOrigin message={message} pageId={view.page.id} navigate={navigate} />
      <MessageContent content={messageBody(message)} referenceKey={JSON.stringify(message.references.map(({ id, targetPageId, label }) => ({ id, targetPageId, label })))} pageId={view.page.id} messageId={message.id} navigate={navigate} />
      {message.materials?.length ? <ul className="message-materials" aria-label={t('materials.attached')}>{message.materials.map(material => <li key={material.id}><button className="material-chip" onClick={() => onMaterial?.(material.id, material.pageId)}><Icon name="page" size={16} /><span>{material.name}</span></button></li>)}</ul> : null}
      <div className="message-footer">
        {activity ? <span className={`message-status tone-${activity.tone}`} title={activity.detail}>{activity.label}</span> : null}
        <div className="message-actions">
          <CopyButton text={message.content} label={t('conversation.copyMessage')} />
          <button onClick={() => onCreate(message)} title={t("conversation.newPageTitle")}><Icon name="plus" size={14} />{t("conversation.newPage")}</button>
          <button onClick={() => onReference(message)} title={t("conversation.linkPageTitle")}><Icon name="link" size={14} />{t("conversation.linkPage")}</button>
          {onSaveExample && message.role === 'assistant' && message.status === 'settled' ? <button onClick={() => {
            const selected = window.getSelection();
            const node = document.getElementById(`message-${message.id}`);
            const text = selected && node?.contains(selected.anchorNode) && node.contains(selected.focusNode) ? selected.toString() : '';
            // Rendered Markdown may join text that was not contiguous in the
            // source. The editor always starts with an exact source passage.
            onSaveExample(message, text.trim() && message.content.includes(text) ? text : message.content);
          }}>{t('learning.saveExample')}</button> : null}
          {view.page.kind === 'work' ? <button onClick={() => {
            const selected = window.getSelection();
            const node = document.getElementById(`message-${message.id}`);
            const text = selected && node?.contains(selected.anchorNode) && node.contains(selected.focusNode) ? selected.toString().trim() : '';
            const canonical = messageBody(message);
            // Rendered Markdown may join text that is not contiguous in the
            // original. Only label an exact source substring as a selection.
            onReturn(message, text && canonical.includes(text) ? text : canonical);
          }} title={t("conversation.bringHomeTitle")}><Icon name="return" size={14} />{t("conversation.bringHome")}</button> : null}
        </div>
      </div>
    </article>; })}
    {pageReferences.length ? <div className="page-references">{pageReferences.map(reference => <SessionLink key={reference.id} pageId={reference.targetPageId} label={reference.label} referenceId={reference.id} origin={{ pageId: view.page.id, referenceId: reference.id }} navigate={navigate} />)}</div> : null}
    {!view.messages.length && !pageReferences.length ? <p className="empty-conversation">{view.page.kind === 'home' ? t('conversation.emptyHome') : t('conversation.emptyPage')}</p> : null}
  </div>;
}
