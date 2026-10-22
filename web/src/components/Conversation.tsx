import type { ReactNode } from 'react';
import type { Message, PageReference, PageSummary, PageView } from '../types';
import { Icon } from './Icon';
import { SessionLink, type Navigate } from './SessionLink';

function linkedText(text: string, references: PageReference[], pageId: string, messageId: string | undefined, navigate: Navigate): ReactNode[] {
  const sorted = [...references].filter(reference => reference.label).sort((a, b) => b.label.length - a.label.length);
  const nodes: ReactNode[] = [];
  let position = 0;
  while (position < text.length) {
    let found: PageReference | undefined;
    let next = text.length;
    for (const reference of sorted) {
      const index = text.indexOf(reference.label, position);
      if (index !== -1 && index < next) { next = index; found = reference; }
    }
    if (!found) { nodes.push(text.slice(position)); break; }
    if (next > position) nodes.push(text.slice(position, next));
    nodes.push(<SessionLink key={`${found.id}-${next}`} pageId={found.targetPageId} label={found.label} referenceId={found.id} origin={{ pageId, messageId, referenceId: found.id }} navigate={navigate} />);
    position = next + found.label.length;
  }
  return nodes;
}

function MessageContent({ message, pageId, navigate }: { message: Message; pageId: string; navigate: Navigate }) {
  const used = new Set(message.references.filter(reference => message.content.includes(reference.label)).map(reference => reference.id));
  const remaining = message.references.filter(reference => !used.has(reference.id));
  return <div className="message-content">
    {message.content.split(/\n\s*\n/).map((paragraph, index) => <p key={index}>{linkedText(paragraph, message.references, pageId, message.id, navigate)}</p>)}
    {remaining.length ? <p className="message-references">{remaining.map(reference => <SessionLink key={reference.id} pageId={reference.targetPageId} label={reference.label} referenceId={reference.id} origin={{ pageId, messageId: message.id, referenceId: reference.id }} navigate={navigate} />)}</p> : null}
  </div>;
}

export function Conversation({ view, pages, navigate, onCreate, onReference, onReturn }: {
  view: PageView; pages: PageSummary[]; navigate: Navigate; onCreate: (message?: Message) => void; onReference: (message: Message) => void; onReturn: (message: Message, selection: string) => void;
}) {
  const pageMap = new Map(pages.map(page => [page.id, page]));
  const pageReferences = view.references.filter(reference => !reference.sourceMessageId);
  return <div className="conversation" aria-label="Conversation">
    {view.hasMore ? <p className="conversation-note">Showing the most recent messages.</p> : null}
    {view.messages.map(message => <article className={`message message-${message.role}`} id={`message-${message.id}`} data-message-id={message.id} key={message.id}>
      <div className="message-label">{message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Per Pulse' : 'Workspace'}</div>
      {message.sourcePageId && message.sourcePageId !== view.page.id ? <div className="source-line">From <SessionLink pageId={message.sourcePageId} label={pageMap.get(message.sourcePageId)?.title ?? 'Source page'} targetMessageId={message.sourceMessageId ?? undefined} navigate={navigate} /></div> : null}
      <MessageContent message={message} pageId={view.page.id} navigate={navigate} />
      <div className="message-footer">
        {['queued', 'running', 'working', 'failed', 'error'].includes(message.status) ? <span className={`message-status status-${message.status}`}>{message.status === 'queued' ? view.provider.configured ? 'Queued' : 'Saved · awaiting AI connection' : ['running', 'working'].includes(message.status) ? 'Working…' : 'Could not complete this turn'}</span> : null}
        <div className="message-actions">
          <button onClick={() => onCreate(message)} title="Continue this in a new page"><Icon name="plus" size={14} />New page</button>
          <button onClick={() => onReference(message)} title="Link an existing page here"><Icon name="link" size={14} />Link page</button>
          {view.page.kind === 'work' ? <button onClick={() => {
            const selected = window.getSelection();
            const node = document.getElementById(`message-${message.id}`);
            const text = selected && node?.contains(selected.anchorNode) && node.contains(selected.focusNode) ? selected.toString().trim() : '';
            onReturn(message, text || message.content);
          }} title="Bring selected text or this message to Home"><Icon name="return" size={14} />Bring to Home</button> : null}
        </div>
      </div>
    </article>)}
    {pageReferences.length ? <div className="page-references">{pageReferences.map(reference => <SessionLink key={reference.id} pageId={reference.targetPageId} label={reference.label} referenceId={reference.id} origin={{ pageId: view.page.id, referenceId: reference.id }} navigate={navigate} />)}</div> : null}
    {!view.messages.length && !pageReferences.length ? <p className="empty-conversation">{view.page.kind === 'home' ? 'Start a conversation, and give each idea a place of its own.' : 'This is a space to work things through. Pick up the conversation below.'}</p> : null}
  </div>;
}
