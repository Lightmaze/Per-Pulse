import { useEffect, useId, useState } from 'react';
import { api } from '../api';
import type { Bootstrap, Message, PageSummary, ResponseLanguage } from '../types';
import { Dialog } from './Dialog';
import { Icon } from './Icon';

export type Modal =
  | { kind: 'create'; message?: Message }
  | { kind: 'picker'; mode: 'search' | 'compose' | 'reference'; message?: Message }
  | { kind: 'return'; message: Message; content: string }
  | { kind: 'settings' }
  | { kind: 'learning' }
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
  const [activeIndex, setActiveIndex] = useState(0);
  const pickerId = useId();
  const matches = pages.filter(page => `${page.title} ${page.description}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selectedIndex = Math.min(activeIndex, Math.max(0, matches.length - 1));
  const activeId = matches[selectedIndex] ? `${pickerId}-${matches[selectedIndex].id}` : undefined;
  useEffect(() => { if (activeId) document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' }); }, [activeId]);
  return <Dialog title={mode === 'search' ? 'Find your place.' : 'Link a page'} onClose={close} className="picker-dialog">
    <div className="search-field"><Icon name="search" size={19} /><input aria-label="Search pages" role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls={`${pickerId}-results`} aria-activedescendant={activeId} aria-describedby={`${pickerId}-hint`} placeholder="Search your pages…" value={query} onChange={event => { setQuery(event.target.value); setActiveIndex(0); }} onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (matches.length) setActiveIndex(event.key === 'ArrowDown' ? Math.min(selectedIndex + 1, matches.length - 1) : Math.max(selectedIndex - 1, 0));
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (!busy && matches[selectedIndex]) select(matches[selectedIndex]);
      }
    }} autoFocus /></div>
    <p className="picker-results-meta" role="status">{matches.length} {matches.length === 1 ? 'page' : 'pages'} {query.trim() ? 'found' : 'available'}</p>
    <div className="picker-results" id={`${pickerId}-results`} role="listbox" aria-label="Pages">
      {matches.map((page, index) => <button key={page.id} id={`${pickerId}-${page.id}`} type="button" role="option" aria-selected={index === selectedIndex} tabIndex={-1} className="picker-result" disabled={busy} onMouseEnter={() => setActiveIndex(index)} onClick={() => select(page)}><Icon name={page.kind === 'home' ? 'home' : 'page'} /><span><strong>{page.title}</strong>{page.description ? <small>{page.description}</small> : null}</span><Icon name="chevron" size={16} /></button>)}
    </div>
    {!matches.length ? <p className="picker-empty">{pages.length ? 'No matching pages. Try another name.' : 'No pages yet. Create one to begin.'}</p> : null}
    <p id={`${pickerId}-hint`} className="picker-keyboard-hint">↑ ↓ to choose · Enter to {mode === 'search' ? 'open' : 'link'} · Esc to close</p>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    <button className="create-from-picker" disabled={busy} onClick={create}><Icon name="plus" size={18} />Create a new page</button>
  </Dialog>;
}

export function ReturnDialog({ initial, busy, error, close, send, sourceTitle }: { initial: string; busy: boolean; error: string; close: () => void; send: (content: string) => void; sourceTitle?: string }) {
  const [content, setContent] = useState(initial);
  return <Dialog title="Bring this back to Home." onClose={close}>
    <p className="dialog-description">Choose what belongs in the main conversation. A link will lead back to this source.</p>
    <div className="return-preview"><span className="return-preview-source">From <strong>{sourceTitle || 'this page'}</strong></span><span aria-hidden="true">→</span><span className="return-preview-destination">Home</span></div>
    <form onSubmit={event => { event.preventDefault(); if (content.trim() && !busy) send(content.trim()); }}>
      <label className="field-label" htmlFor="return-content">What would you like to bring back?</label>
      <textarea id="return-content" autoFocus value={content} onChange={event => setContent(event.target.value)} rows={7} required />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>Cancel</button><button className="primary-button" type="submit" disabled={busy || !content.trim()}>{busy ? 'Saving…' : 'Bring to Home'}</button></div>
    </form>
  </Dialog>;
}

export function SettingsDialog({ bootstrap, close, refresh, learning }: { bootstrap: Bootstrap | null; close: () => void; refresh: () => void; learning: () => void }) {
  const [language, setLanguage] = useState<ResponseLanguage>(bootstrap?.settings?.responseLanguage ?? 'auto');
  const [saving, setSaving] = useState(false);
  const [languageError, setLanguageError] = useState('');
  const changeLanguage = async (next: ResponseLanguage) => {
    if (saving || next === language) return;
    setSaving(true);
    setLanguageError('');
    try {
      const saved = await api.updateSettings(next);
      setLanguage(saved.responseLanguage);
      refresh();
    } catch (problem) {
      setLanguageError(problem instanceof Error ? problem.message : 'The language setting could not be saved.');
    } finally {
      setSaving(false);
    }
  };
  return <Dialog title="Your workspace" onClose={close}>
    <div className="settings-section"><h3>AI connection</h3><p className="connection-label"><span className={`connection-dot ${bootstrap?.provider.configured ? 'connected' : ''}`} />{bootstrap?.provider.label ?? 'Checking connection…'}</p><p>{bootstrap?.provider.configured ? `Connected to ${bootstrap.provider.model ?? 'your configured model'}.` : 'Your conversations and page links are saved. AI replies will be available when a provider is connected.'}</p>{bootstrap?.provider.detail ? <p className="settings-detail">{bootstrap.provider.detail}</p> : null}<button className="secondary-button" onClick={refresh}>Check connection</button></div>
    <div className="settings-section">
      <h3>Reply language</h3>
      <label className="field-label" htmlFor="response-language">Language for future replies</label>
      <select id="response-language" value={language} disabled={saving} onChange={event => void changeLanguage(event.target.value as ResponseLanguage)}>
        <option value="auto">Follow the conversation</option>
        <option value="en">English</option>
        <option value="zh-CN">简体中文</option>
      </select>
      <p className="settings-detail">A preference for new model turns. Explicit language instructions take precedence. This does not translate previous messages or change an ongoing turn.</p>
      {saving ? <p role="status">Saving preference…</p> : null}
      {languageError ? <p className="form-error" role="alert">{languageError}</p> : null}
    </div>
    <div className="settings-section"><h3>Learning from experience</h3><p>{bootstrap?.postureEnabled ? 'On. Relevant lessons can guide the next action and the decision to deliver. Keep, pause, or share a lesson across your pages.' : 'Off. Your conversations are saved; they are not automatically treated as lessons.'}</p><button className="secondary-button" onClick={learning}>View this page’s lessons</button></div>
    <div className="settings-section"><h3>{bootstrap?.example ? 'Example workspace' : 'Your conversations'}</h3><p>{bootstrap?.example ? 'These are authored example conversations in a separate workspace. They show how pages connect; they are not live AI replies.' : 'Conversations are saved in this workspace. Each page keeps its own conversation, and links always return to the same page.'}</p><p>Your unsent drafts and reading position stay in this browser.</p></div>
  </Dialog>;
}

export function RenameDialog({ initial, busy, error, close, rename }: { initial: string; busy: boolean; error: string; close: () => void; rename: (title: string) => void }) {
  const [title, setTitle] = useState(initial);
  return <Dialog title="Rename this page" onClose={close}><form onSubmit={event => { event.preventDefault(); if (title.trim() && !busy) rename(title.trim()); }}><label className="field-label" htmlFor="rename-title">Page name</label><input id="rename-title" autoFocus value={title} onChange={event => setTitle(event.target.value)} maxLength={160} required />{error ? <p className="form-error" role="alert">{error}</p> : null}<div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>Cancel</button><button className="primary-button" type="submit" disabled={busy || !title.trim()}>{busy ? 'Saving…' : 'Save name'}</button></div></form></Dialog>;
}
