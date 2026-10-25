import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from './api';
import { useLanguage, type MessageKey } from './i18n';
import { useWorkspace } from './useWorkspace';
import { EMPTY_DRAFT, readDraft, readPosition, saveDraft, savePosition } from './storage';
import type { Draft, Message, NavigationOrigin, PageSummary, ResponseLanguage } from './types';
import { Icon } from './components/Icon';
import { Sidebar } from './components/Sidebar';
import { SessionLink, type Navigate } from './components/SessionLink';
import { Conversation } from './components/Conversation';
import { Composer } from './components/Composer';
import { CreatePageDialog, PagePickerDialog, RenameDialog, ReturnDialog, SettingsDialog, type Modal } from './components/WorkspaceDialogs';

type Toast = { key: MessageKey; values?: Record<string, string | number> } | { error: unknown };

export default function App() {
  const { t, pageTitle, errorText } = useLanguage();
  const workspace = useWorkspace();
  const { bootstrap, route, view, loading, error, setError, applyView, refreshBootstrap, refreshPage } = workspace;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [modalError, setModalError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const draftsRef = useRef(drafts);
  draftsRef.current = drafts;
  const main = useRef<HTMLElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const restored = useRef('');
  const scrollTimer = useRef<number | undefined>(undefined);
  const composerSelection = useRef({ start: 0, end: 0 });
  const settingsWrites = useRef<Promise<void>>(Promise.resolve());
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

  const openModal = (next: Modal) => {
    setModalError(''); setModal(next); setSidebarOpen(false);
    if (next.kind === 'settings') workspace.refreshSettings().catch(problem => setToast({ error: problem }));
  };
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
    const timer = window.setTimeout(() => setToast(null), 4500);
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
    } catch (problem) { setError(problem); }
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
      setToast({ key: 'app.pageCreated' });
    } catch (problem) { setModalError(problem); }
    finally { setBusy(false); }
  };

  const selectPage = async (page: PageSummary) => {
    if (modal?.kind !== 'picker') return;
    if (modal.mode === 'search') { navigate(page.id); return; }
    if (modal.mode === 'compose') {
      const label = pageTitle(page);
      const { start, end } = composerSelection.current;
      const before = draft.text.slice(0, start);
      const after = draft.text.slice(end);
      const insertion = `${before && !/\s$/.test(before) ? ' ' : ''}${label}${after && !/^\s/.test(after) ? ' ' : ''}`;
      changeDraft({ text: before + insertion + after, references: [...draft.references.filter(reference => reference.targetPageId !== page.id), { targetPageId: page.id, label }] });
      setModal(null);
      requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(before.length + insertion.length, before.length + insertion.length); });
      return;
    }
    setBusy(true); setModalError('');
    try {
      const response = await api.reference(route.id, { targetPageId: page.id, label: pageTitle(page), sourceMessageId: modal.message?.id });
      applyView(response.view); setModal(null); setToast({ key: 'app.pageLinked', values: { title: pageTitle(page) } });
    } catch (problem) { setModalError(problem); }
    finally { setBusy(false); }
  };

  const bringHome = async (content: string) => {
    if (modal?.kind !== 'return') return;
    setBusy(true); setModalError('');
    try {
      const response = await api.bringHome(route.id, content, modal.message.id);
      applyView(response.view);
      navigate('home', undefined, response.messageId);
      setToast({ key: 'app.broughtHome' });
    } catch (problem) { setModalError(problem); }
    finally { setBusy(false); }
  };

  const renamePage = async (title: string) => {
    setBusy(true); setModalError('');
    try { const next = await api.rename(route.id, title); applyView(next); setModal(null); }
    catch (problem) { setModalError(problem); }
    finally { setBusy(false); }
  };

  const saveSettings = (language: ResponseLanguage) => {
    // Serialize writes even if a keyboard shortcut changes the active dialog.
    // A late response must never undo the user's more recent selection.
    const write = settingsWrites.current.catch(() => {}).then(async () => {
      const next = await api.saveSettings(language);
      workspace.applySettings(next);
    });
    settingsWrites.current = write;
    return write;
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
  const breadcrumbs = view?.breadcrumbs.length ? view.breadcrumbs : [{ id: 'home', title: t('app.home') }];
  const modalErrorText = modalError ? errorText(modalError) : '';

  return <div className={`app-shell ${sidebarHidden ? 'is-sidebar-collapsed' : ''}`}>
    <Sidebar bootstrap={bootstrap} activePageId={route.id} open={sidebarOpen} close={() => setSidebarOpen(false)} navigate={navigate} onSearch={() => openModal({ kind: 'picker', mode: 'search' })} onCreate={() => openModal({ kind: 'create' })} onSettings={() => openModal({ kind: 'settings' })} />
    <div className="workspace">
      <header className="topbar">
        <button className="icon-button nav-toggle" aria-label={t('app.toggleNavigation')} onClick={() => { if (window.matchMedia('(max-width: 760px)').matches) setSidebarOpen(previous => !previous); else setSidebarHidden(previous => !previous); }}><Icon name="menu" size={20} /></button>
        <nav className="breadcrumbs" aria-label={t('app.breadcrumb')}>
          {breadcrumbs.map((crumb, index) => <Fragment key={crumb.id}>
            {index > 0 ? <Icon name="chevron" size={14} /> : null}
            {crumb.id === route.id && crumb.id !== 'home' ? <span className="current-crumb" aria-current="page" title={crumb.title}>{crumb.title}</span> : <button className="crumb-ancestor" onClick={() => navigate(crumb.id)} title={pageTitle(crumb)}>{crumb.id === 'home' ? <Icon name="home" size={18} /> : null}<span>{pageTitle(crumb)}</span></button>}
          </Fragment>)}
        </nav>
        {route.id !== 'home' ? <button className="text-button return-to-thread" aria-label={t('app.back')} title={t('app.back')} onClick={goBack}><Icon name="back" size={16} /><span>{t('app.back')}</span></button> : null}
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
            {route.id === 'home' ? <><h1>{t('app.heading')}</h1><p className="page-subtitle">{t('app.subtitle')}</p></> : <>
              <h1><button className="editable-title" onClick={() => openModal({ kind: 'rename' })} title={t('app.rename')}>{view?.page.title ?? t('app.openingPage')}<Icon name="edit" size={21} /></button></h1>
              {sourcePage ? <p className="page-subtitle page-origin">{t('app.origin')} <SessionLink pageId={sourcePage.id} label={pageTitle(sourcePage)} navigate={navigate} targetMessageId={view?.page.sourceMessageId ?? undefined} /></p> : <p className="page-subtitle">{t('app.pageSubtitle')}</p>}
            </>}
            {bootstrap?.example ? <p className="mobile-workspace-status">{t('app.exampleStatus', { connection: t(bootstrap.provider.configured ? 'app.connected' : 'app.disconnected') })}</p> : null}
          </section>
          {error ? <div className="error-notice" role="alert"><span>{errorText(error)}</span><button onClick={() => { setError(''); Promise.all([refreshBootstrap(), refreshPage(route.id)]).catch(problem => setError(problem)); }}>{t('app.retry')}</button></div> : null}
          {view ? <Conversation view={view} pages={pages} navigate={navigate} onCreate={(message?: Message) => openModal({ kind: 'create', message })} onReference={message => openModal({ kind: 'picker', mode: 'reference', message })} onReturn={(message, selection) => openModal({ kind: 'return', message, content: selection })} /> : loading ? <div className="loading-state" role="status"><span className="loading-dot" />{t('app.openingConversation')}</div> : null}
          {view && workspaceId ? <Composer draft={draft} changeDraft={changeDraft} send={sendMessage} busy={sending} inputRef={input} onLink={() => { composerSelection.current = { start: input.current?.selectionStart ?? draft.text.length, end: input.current?.selectionEnd ?? draft.text.length }; openModal({ kind: 'picker', mode: 'compose' }); }} /> : null}
        </div>
      </main>
    </div>
    {toast ? <div className="toast" role="status"><Icon name="check" size={17} />{'key' in toast ? t(toast.key, toast.values) : errorText(toast.error)}</div> : null}
    {modal?.kind === 'create' ? <CreatePageDialog initial={modal.message} busy={busy} error={modalErrorText} close={closeModal} create={createPage} /> : null}
    {modal?.kind === 'picker' ? <PagePickerDialog pages={modal.mode === 'search' ? pages : pages.filter(page => page.id !== route.id)} mode={modal.mode} busy={busy} error={modalErrorText} close={closeModal} select={selectPage} create={() => openModal({ kind: 'create', message: modal.message })} /> : null}
    {modal?.kind === 'return' ? <ReturnDialog initial={modal.content} busy={busy} error={modalErrorText} close={closeModal} send={bringHome} /> : null}
    {modal?.kind === 'rename' ? <RenameDialog initial={view?.page.title ?? ''} busy={busy} error={modalErrorText} close={closeModal} rename={renamePage} /> : null}
    {modal?.kind === 'settings' ? <SettingsDialog bootstrap={bootstrap} close={closeModal} saveSettings={saveSettings} refresh={() => refreshBootstrap().then(() => setToast({ key: 'app.connectionChecked' })).catch(problem => setToast({ error: problem }))} /> : null}
  </div>;
}
