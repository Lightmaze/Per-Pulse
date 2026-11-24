import { useState } from 'react';
import type { Bootstrap } from '../types';
import { pageHref } from '../storage';
import { Icon } from './Icon';
import { useLanguage } from '../i18n';
import { interceptNavigation, type Navigate } from './SessionLink';
import { SIDEBAR_PAGE_BATCH, sidebarPageWindow } from '../sidebarPages';

export function Sidebar({ bootstrap, sendingPages, sendErrors, activePageId, stale = false, open, close, navigate, onSearch, onCreate, onSettings }: {
  bootstrap: Bootstrap | null; sendingPages: Record<string, boolean>; sendErrors: Record<string, string>; activePageId: string; stale?: boolean; open: boolean; close: () => void; navigate: Navigate; onSearch: () => void; onCreate: () => void; onSettings: () => void;
}) {
  const { t, pageTitle } = useLanguage();
  const pages = bootstrap?.pages.filter(page => page.kind === 'work') ?? [];
  const [pageQuery, setPageQuery] = useState('');
  const [visibleLimit, setVisibleLimit] = useState(SIDEBAR_PAGE_BATCH);
  const pageWindow = sidebarPageWindow(pages, pageQuery, activePageId, visibleLimit);
  // These are only browser-side save states, not native model-turn outcomes.
  // A lost response may have reached the server, so direct the user to
  // inspect the original conversation instead of implying it was not saved.
  const homeKey = `${bootstrap?.workspaceId ?? ''}/home`;
  const homeSaving = Boolean(sendingPages[homeKey]);
  const homeNeedsCheck = Boolean(sendErrors[homeKey]);
  return <>
    {open ? <button className="sidebar-scrim" aria-label={t('sidebar.closeNavigation')} onClick={close} /> : null}
    <aside className={`sidebar ${open ? 'is-open' : ''}`} aria-label={t('sidebar.navigation')}>
      <a href="/" className="wordmark" onClick={event => interceptNavigation(event, () => navigate('home'))}><Icon name="pulse" size={30} /><span>Per Pulse</span></a>
      <nav className="main-nav">
        <a href="/" className={`nav-row ${activePageId === 'home' ? 'is-active' : ''}`} aria-current={activePageId === 'home' ? 'page' : undefined} onClick={event => interceptNavigation(event, () => navigate('home'))}><Icon name="home" /><span>{t('sidebar.home')}</span>{homeNeedsCheck
          ? <span className="attention-mark" aria-label={t('sidebar.checkMessage')} title={t('sidebar.checkMessage')}>!</span>
          : homeSaving ? <span className="working-dot" aria-label={t('sidebar.savingMessage')} title={t('sidebar.savingMessage')} /> : null}</a>
        <button className="nav-row" onClick={onSearch}><Icon name="search" /><span>{t('sidebar.search')}</span></button>
      </nav>
      <div className="pages-label"><span>{t('sidebar.pages')}</span><button className="icon-button" aria-label={t('sidebar.createPage')} title={t('sidebar.createPage')} onClick={onCreate}><Icon name="plus" size={17} /></button></div>
      {pages.length > SIDEBAR_PAGE_BATCH ? <div className="sidebar-page-filter">
        <Icon name="search" size={16} />
        <input type="search" aria-label={t('sidebar.filterPages')} placeholder={t('sidebar.filterPages')}
          value={pageQuery} onChange={event => { setPageQuery(event.target.value); setVisibleLimit(SIDEBAR_PAGE_BATCH); }} />
      </div> : null}
      <nav className="page-nav" aria-label={t('sidebar.pages')}>
        {pageWindow.visible.map(page => {
          const pageKey = `${bootstrap?.workspaceId ?? ''}/${page.id}`;
          const saving = Boolean(sendingPages[pageKey]);
          const needsCheck = Boolean(sendErrors[pageKey]);
          return <a key={page.id} href={pageHref(page.id)} className={`nav-row ${activePageId === page.id ? 'is-active' : ''}`} aria-current={activePageId === page.id ? 'page' : undefined} onClick={event => interceptNavigation(event, () => navigate(page.id))}>
            <Icon name="page" /><span>{pageTitle(page)}</span>
            {needsCheck ? <span className="attention-mark" aria-label={t('sidebar.checkMessage')} title={t('sidebar.checkMessage')}>!</span>
              : saving ? <span className="working-dot" aria-label={t('sidebar.savingMessage')} title={t('sidebar.savingMessage')} />
              : page.status === 'working' && bootstrap?.provider.configured && !stale ? <span className="working-dot" aria-label={t('sidebar.working')} />
              : page.status === 'needs_attention' ? <span className="attention-mark" aria-label={t('sidebar.needsAttention')}>!</span>
              : page.status === 'error' ? <span className="attention-mark" aria-label={t('sidebar.couldNotFinish')}>!</span> : null}
          </a>;
        })}
        {!pages.length ? <button className="empty-pages" onClick={onCreate}>{t('sidebar.emptyPages')}</button> : null}
        {pages.length > 0 && pageWindow.total === 0 ? <p className="empty-pages" role="status">{t('sidebar.noMatchingPages')}</p> : null}
        {pageWindow.hasMore ? <div className="sidebar-more-pages">
          <span role="status">{t('sidebar.showingPages', { shown: pageWindow.shown, total: pageWindow.total })}</span>
          <button type="button" onClick={() => setVisibleLimit(limit => limit + SIDEBAR_PAGE_BATCH)}>
            {t('sidebar.morePages')}
          </button>
        </div> : null}
      </nav>
      <div className="sidebar-footer">
        <div className="workspace-label">{bootstrap?.example ? t('sidebar.exampleWorkspace') : t('sidebar.yourWorkspace')}</div>
        {bootstrap ? <button className={`provider-indicator ${bootstrap.provider.configured ? 'is-connected' : ''}`} onClick={onSettings}><span />{bootstrap.provider.label}</button> : null}
        <div className="footer-rule" />
        <button className="nav-row" onClick={onSettings}><Icon name="settings" /><span>{t('sidebar.settings')}</span></button>
      </div>
    </aside>
  </>;
}
