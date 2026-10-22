import { useState } from 'react';
import type { Bootstrap, Message, PageSummary } from '../types';
import { Dialog } from './Dialog';
import { Icon } from './Icon';

export type Modal =
  | { kind: 'create'; message?: Message }
  | { kind: 'picker'; mode: 'search' | 'compose' | 'reference'; message?: Message }
  | { kind: 'return'; message: Message; content: string }
  | { kind: 'settings' }
  | { kind: 'rename' };

export function CreatePageDialog({ initial, busy, error, close, create }: { initial?: Message; busy: boolean; error: string; close: () => void; create: (title: string, instruction: string) => void }) {
  const [title, setTitle] = useState('');
  const [instruction, setInstruction] = useState(initial?.content ?? '');
  return <Dialog title="Give this a little room." onClose={close}>
    <p className="dialog-description">Start a focused conversation. Its link will stay here, so you can come back to it.</p>
    <form onSubmit={event => { event.preventDefault(); if (title.trim() && instruction.trim() && !busy) create(title.trim(), instruction.trim()); }}>
      <label className="field-label" htmlFor="new-page-title">Page name</label>
      <input id="new-page-title" autoFocus value={title} onChange={event => setTitle(event.target.value)} placeholder="A name for this thought" maxLength={160} required />
      <label className="field-label" htmlFor="new-page-focus">Where should we begin?</label>
      <textarea id="new-page-focus" value={instruction} onChange={event => setInstruction(event.target.value)} placeholder="The question, idea, or work you want to continue…" rows={5} required />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>Cancel</button><button type="submit" className="primary-button" disabled={busy || !title.trim() || !instruction.trim()}>{busy ? 'Creating…' : 'Create page'}</button></div>
    </form>
  </Dialog>;
}

export function PagePickerDialog({ pages, mode, busy, error, close, select, create }: { pages: PageSummary[]; mode: 'search' | 'compose' | 'reference'; busy: boolean; error: string; close: () => void; select: (page: PageSummary) => void; create: () => void }) {
  const [query, setQuery] = useState('');
  const matches = pages.filter(page => `${page.title} ${page.description}`.toLowerCase().includes(query.toLowerCase()));
  return <Dialog title={mode === 'search' ? 'Find your place.' : 'Link a page'} onClose={close} className="picker-dialog">
    <div className="search-field"><Icon name="search" size={19} /><input aria-label="Search pages" placeholder="Search your pages…" value={query} onChange={event => setQuery(event.target.value)} autoFocus /></div>
    <div className="picker-results">
      {matches.map(page => <button key={page.id} className="picker-result" disabled={busy} onClick={() => select(page)}><Icon name={page.kind === 'home' ? 'home' : 'page'} /><span><strong>{page.title}</strong>{page.description ? <small>{page.description}</small> : null}</span><Icon name="chevron" size={16} /></button>)}
      {!matches.length ? <p className="picker-empty">No pages found. There’s room for a new one.</p> : null}
    </div>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    <button className="create-from-picker" onClick={create}><Icon name="plus" size={18} />Create a new page</button>
  </Dialog>;
}

export function ReturnDialog({ initial, busy, error, close, send }: { initial: string; busy: boolean; error: string; close: () => void; send: (content: string) => void }) {
  const [content, setContent] = useState(initial);
  return <Dialog title="Bring this back to Home." onClose={close}>
    <p className="dialog-description">Choose what belongs in the main conversation. A link will lead back to this source.</p>
    <form onSubmit={event => { event.preventDefault(); if (content.trim() && !busy) send(content.trim()); }}>
      <label className="field-label" htmlFor="return-content">What would you like to bring back?</label>
      <textarea id="return-content" autoFocus value={content} onChange={event => setContent(event.target.value)} rows={7} required />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>Cancel</button><button className="primary-button" type="submit" disabled={busy || !content.trim()}>{busy ? 'Saving…' : 'Bring to Home'}</button></div>
    </form>
  </Dialog>;
}

export function SettingsDialog({ bootstrap, close, refresh }: { bootstrap: Bootstrap | null; close: () => void; refresh: () => void }) {
  return <Dialog title="Your workspace" onClose={close}>
    <div className="settings-section"><h3>AI connection</h3><p className="connection-label"><span className={`connection-dot ${bootstrap?.provider.configured ? 'connected' : ''}`} />{bootstrap?.provider.label ?? 'Checking connection…'}</p><p>{bootstrap?.provider.configured ? `Connected to ${bootstrap.provider.model ?? 'your configured model'}.` : 'Your conversations and page links are saved. AI replies will be available when a provider is connected.'}</p>{bootstrap?.provider.detail ? <p className="settings-detail">{bootstrap.provider.detail}</p> : null}<button className="secondary-button" onClick={refresh}>Check connection</button></div>
    <div className="settings-section"><h3>{bootstrap?.example ? 'Example workspace' : 'Your conversations'}</h3><p>{bootstrap?.example ? 'These are authored example conversations in a separate workspace. They show how pages connect; they are not live AI replies.' : 'Conversations are saved in this workspace. Each page keeps its own conversation, and links always return to the same page.'}</p><p>Your unsent drafts and reading position stay in this browser.</p></div>
  </Dialog>;
}

export function RenameDialog({ initial, busy, error, close, rename }: { initial: string; busy: boolean; error: string; close: () => void; rename: (title: string) => void }) {
  const [title, setTitle] = useState(initial);
  return <Dialog title="Rename this page" onClose={close}><form onSubmit={event => { event.preventDefault(); if (title.trim() && !busy) rename(title.trim()); }}><label className="field-label" htmlFor="rename-title">Page name</label><input id="rename-title" autoFocus value={title} onChange={event => setTitle(event.target.value)} maxLength={160} required />{error ? <p className="form-error" role="alert">{error}</p> : null}<div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>Cancel</button><button className="primary-button" type="submit" disabled={busy || !title.trim()}>{busy ? 'Saving…' : 'Save name'}</button></div></form></Dialog>;
}
