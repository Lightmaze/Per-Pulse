import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from './api';
import { useWorkspace } from './useWorkspace';
import { EMPTY_DRAFT, readDraft, readPosition, saveDraft, savePosition } from './storage';
import type { Draft, Message, NavigationOrigin, PageSummary } from './types';
import { Icon } from './components/Icon';
import { Sidebar } from './components/Sidebar';
import { SessionLink, type Navigate } from './components/SessionLink';
import { Conversation } from './components/Conversation';
import { Composer } from './components/Composer';
import { LearningDialog } from './components/LearningDialog';
import { CreatePageDialog, PagePickerDialog, RenameDialog, ReturnDialog, SettingsDialog, type Modal } from './components/WorkspaceDialogs';

const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';

export default function App() {
  const workspace = useWorkspace();
  const { bootstrap, route, view, loading, error, setError, applyView, refreshBootstrap, refreshPage } = workspace;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [modalError, setModalError] = useState('');
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState('');
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;
  const main = useRef<HTMLElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const restored = useRef('');
  const scrollTimer = useRef<number | undefined>(undefined);
  const composerSelection = useRef({ start: 0, end: 0 });
  const workspaceId = bootstrap?.workspaceId;
  const draftKey = `${workspaceId ?? ''}/${route.id}`;
  const draft = workspaceId ? drafts[draftKey] ?? readDraft(workspaceId, route.id) : EMPTY_DRAFT;

  const navigate: Navigate = useCallback((id: string, origin?: NavigationOrigin, targetMessageId?: string) => {
    window.clearTimeout(scrollTimer.current);
    if (workspaceId) savePosition(workspaceId, route.id, main.current?.scrollTop ?? 0);
    setSidebarOpen(false);
    setModal(null);
    workspace.navigate(id, origin, targetMessageId);
  }, [workspace.navigate, route.id, workspaceId]);

  const openModal = (next: Modal) => { setModalError(''); setModal(next); setSidebarOpen(false); };
  const closeModal = () => { if (!busy) { setModal(null); setModalError(''); } };
  const changeDraft = useCallback((next: Draft) => {
    if (!workspaceId) return;
    const updated = { ...draftsRef.current, [draftKey]: next };
    draftsRef.current = updated;
    setDrafts(updated);
    saveDraft(workspaceId, route.id, next);
  }, [route.id, workspaceId, draftKey]);

  useEffect(() => {
    document.title = view?.page.kind === 'work' ? `${view.page.title} · Per Pulse` : 'Per Pulse';
  }, [view?.page.kind, view?.page.title]);

  useEffect(() => () => window.clearTimeout(scrollTimer.current), [route.id]);

  useLayoutEffect(() => {
    if (!view || loading || !workspaceId) return;
    const key = `${workspaceId}-${route.id}-${route.serial}`;
    if (restored.current === key) return;
    const frame = requestAnimationFrame(() => {
      if (route.targetMessageId) document.getElementById(`message-${route.targetMessageId}`)?.scrollIntoView({ block: 'center' });
      else if (main.current) main.current.scrollTop = readPosition(workspaceId, route.id);
      restored.current = key;
    });
    return () => cancelAnimationFrame(frame);
  }, [view, loading, route.id, route.serial, route.targetMessageId, workspaceId]);

  useEffect(() => {
    const preservePosition = () => {
      window.clearTimeout(scrollTimer.current);
      if (workspaceId) savePosition(workspaceId, route.id, main.current?.scrollTop ?? 0);
    };
    window.addEventListener('pagehide', preservePosition);
    return () => window.removeEventListener('pagehide', preservePosition);
  }, [route.id, workspaceId]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setModalError(''); setModal({ kind: 'picker', mode: 'search' }); }
    };
    window.addEventListener('keydown', onShortcut);
    return () => window.removeEventListener('keydown', onShortcut);
  }, []);

  const sendMessage = async () => {
    if (!draft.text.trim() || sending || !workspaceId) return;
    const id = route.id;
    const outgoing = draft;
    setSending(true); setError('');
    try {
      const response = await api.send(id, outgoing.text.trim(), outgoing.references.filter(reference => outgoing.text.includes(reference.label)));
      applyView(response.view);
      const empty = { text: '', references: [] };
      const currentDraft = draftsRef.current[draftKey] ?? readDraft(workspaceId, id);
      const nextDraft = currentDraft.text === outgoing.text && JSON.stringify(currentDraft.references) === JSON.stringify(outgoing.references) ? empty : currentDraft;
      const updated = { ...draftsRef.current, [draftKey]: nextDraft };
      draftsRef.current = updated;
      setDrafts(updated);
      saveDraft(workspaceId, id, nextDraft);
      requestAnimationFrame(() => document.getElementById(`message-${response.messageId}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
      input.current?.focus();
    } catch (problem) { setError(errorMessage(problem)); }
    finally { setSending(false); }
  };

  const createPage = async (title: string, instruction: string) => {
    const sourceId = route.id;
    const message = modal?.kind === 'create' ? modal.message : undefined;
    setBusy(true); setModalError('');
    try {
      const response = await api.createPage({ title, instruction, sourcePageId: sourceId, sourceMessageId: message?.id });
      applyView(response.view);
      refreshPage(sourceId).catch(() => {});
      navigate(response.page.id, { pageId: sourceId, messageId: message?.id, referenceId: response.reference.id });
      setToast('A new page, with a link back to where it began.');
    } catch (problem) { setModalError(errorMessage(problem)); }
    finally { setBusy(false); }
  };

  const selectPage = async (page: PageSummary) => {
    if (modal?.kind !== 'picker') return;
    if (modal.mode === 'search') { navigate(page.id); return; }
    if (modal.mode === 'compose') {
      const { start, end } = composerSelection.current;
      const before = draft.text.slice(0, start);
      const after = draft.text.slice(end);
      const insertion = `${before && !/\s$/.test(before) ? ' ' : ''}${page.title}${after && !/^\s/.test(after) ? ' ' : ''}`;
      changeDraft({ text: before + insertion + after, references: [...draft.references.filter(reference => reference.targetPageId !== page.id), { targetPageId: page.id, label: page.title }] });
      setModal(null);
      requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(before.length + insertion.length, before.length + insertion.length); });
      return;
    }
    setBusy(true); setModalError('');
    try {
      const response = await api.reference(route.id, { targetPageId: page.id, label: page.title, sourceMessageId: modal.message?.id });
      applyView(response.view); setModal(null); setToast(`Linked to ${page.title}.`);
    } catch (problem) { setModalError(errorMessage(problem)); }
    finally { setBusy(false); }
  };

  const bringHome = async (content: string) => {
    if (modal?.kind !== 'return') return;
    setBusy(true); setModalError('');
    try {
      const response = await api.bringHome(route.id, content, modal.message.id);
      applyView(response.view);
      navigate('home', undefined, response.messageId);
      setToast('Brought to Home, with its source.');
    } catch (problem) { setModalError(errorMessage(problem)); }
    finally { setBusy(false); }
  };

  const renamePage = async (title: string) => {
    setBusy(true); setModalError('');
    try { const next = await api.rename(route.id, title); applyView(next); setModal(null); }
    catch (problem) { setModalError(errorMessage(problem)); }
    finally { setBusy(false); }
  };

  const goBack = () => {
    window.clearTimeout(scrollTimer.current);
    if (workspaceId) savePosition(workspaceId, route.id, main.current?.scrollTop ?? 0);
    if (route.origin) {
      const origin = route.origin;
      if (history.length > 1) history.back();
      else navigate(origin.pageId, undefined, origin.messageId);
    } else navigate(view?.page.sourcePageId ?? 'home', undefined, view?.page.sourceMessageId ?? undefined);
  };

  const pages = bootstrap?.pages ?? [];
  const sourcePage = view?.page.sourcePageId ? pages.find(page => page.id === view.page.sourcePageId) : undefined;
  const breadcrumbs = view?.breadcrumbs.length ? view.breadcrumbs : [{ id: 'home', title: 'Home' }];

  return <div className={`app-shell ${sidebarHidden ? 'is-sidebar-collapsed' : ''}`}>
    <Sidebar bootstrap={bootstrap} activePageId={route.id} open={sidebarOpen} close={() => setSidebarOpen(false)} navigate={navigate} onSearch={() => openModal({ kind: 'picker', mode: 'search' })} onCreate={() => openModal({ kind: 'create' })} onSettings={() => openModal({ kind: 'settings' })} />
    <div className="workspace">
      <header className="topbar">
        <button className="icon-button nav-toggle" aria-label="Toggle navigation" onClick={() => { if (window.matchMedia('(max-width: 760px)').matches) setSidebarOpen(previous => !previous); else setSidebarHidden(previous => !previous); }}><Icon name="menu" size={20} /></button>
        <nav className="breadcrumbs" aria-label="Breadcrumb">
          {breadcrumbs.map((crumb, index) => <Fragment key={crumb.id}>
            {index > 0 ? <Icon name="chevron" size={14} /> : null}
            {crumb.id === route.id && crumb.id !== 'home' ? <span className="current-crumb" aria-current="page" title={crumb.title}>{crumb.title}</span> : <button className="crumb-ancestor" onClick={() => navigate(crumb.id)} title={crumb.title}>{crumb.id === 'home' ? <Icon name="home" size={18} /> : null}<span>{crumb.title}</span></button>}
          </Fragment>)}
        </nav>
        {route.id !== 'home' ? <button className="text-button return-to-thread" onClick={goBack}><Icon name="back" size={16} /><span>Back to conversation</span></button> : null}
      </header>
      <main className="workspace-main" ref={main} onScroll={() => {
        if (!workspaceId) return;
        window.clearTimeout(scrollTimer.current);
        const id = route.id;
        const position = main.current?.scrollTop ?? 0;
        scrollTimer.current = window.setTimeout(() => savePosition(workspaceId, id, position), 120);
      }}>
        <div className="page-column">
          <section className="page-heading">
            {route.id === 'home' ? <><h1>A place to keep going.</h1><p className="page-subtitle">Your conversations, connected.</p></> : <>
              <h1><button className="editable-title" onClick={() => openModal({ kind: 'rename' })} title="Rename page">{view?.page.title ?? 'Opening your page…'}<Icon name="edit" size={21} /></button></h1>
              {sourcePage ? <p className="page-subtitle page-origin">A conversation from <SessionLink pageId={sourcePage.id} label={sourcePage.title} navigate={navigate} targetMessageId={view?.page.sourceMessageId ?? undefined} /></p> : <p className="page-subtitle">A little room to work things through.</p>}
            </>}
            {bootstrap?.example ? <p className="mobile-workspace-status">Example workspace · {bootstrap.provider.label}</p> : null}
          </section>
          {error ? <div className="error-notice" role="alert"><span>{error}</span><button onClick={() => { setError(''); Promise.all([refreshBootstrap(), refreshPage(route.id)]).catch(problem => setError(errorMessage(problem))); }}>Try again</button></div> : null}
          {view ? <Conversation view={view} pages={pages} navigate={navigate} onCreate={(message?: Message) => openModal({ kind: 'create', message })} onReference={message => openModal({ kind: 'picker', mode: 'reference', message })} onReturn={(message, selection) => openModal({ kind: 'return', message, content: selection })} /> : loading ? <div className="loading-state" role="status"><span className="loading-dot" />Opening your conversation…</div> : null}
          {view && workspaceId ? <Composer draft={draft} changeDraft={changeDraft} send={sendMessage} busy={sending} inputRef={input} onLink={() => { composerSelection.current = { start: input.current?.selectionStart ?? draft.text.length, end: input.current?.selectionEnd ?? draft.text.length }; openModal({ kind: 'picker', mode: 'compose' }); }} /> : null}
        </div>
      </main>
    </div>
    {toast ? <div className="toast" role="status"><Icon name="check" size={17} />{toast}</div> : null}
    {modal?.kind === 'create' ? <CreatePageDialog initial={modal.message} busy={busy} error={modalError} close={closeModal} create={createPage} /> : null}
    {modal?.kind === 'picker' ? <PagePickerDialog pages={modal.mode === 'search' ? pages : pages.filter(page => page.id !== route.id)} mode={modal.mode} busy={busy} error={modalError} close={closeModal} select={selectPage} create={() => openModal({ kind: 'create', message: modal.message })} /> : null}
    {modal?.kind === 'return' ? <ReturnDialog initial={modal.content} busy={busy} error={modalError} close={closeModal} send={bringHome} /> : null}
    {modal?.kind === 'rename' ? <RenameDialog initial={view?.page.title ?? ''} busy={busy} error={modalError} close={closeModal} rename={renamePage} /> : null}
    {modal?.kind === 'settings' ? <SettingsDialog bootstrap={bootstrap} close={closeModal} learning={() => openModal({ kind: 'learning' })} refresh={() => refreshBootstrap().then(() => setToast('Connection checked.')).catch(problem => setToast(errorMessage(problem)))} /> : null}
    {modal?.kind === 'learning' ? <LearningDialog pageId={route.id} pageTitle={view?.page.title ?? 'this page'} close={closeModal} navigate={navigate} /> : null}
  </div>;
}
