import type { Bootstrap } from '../types';
import { pageHref } from '../storage';
import { useLanguage } from '../i18n';
import { Icon } from './Icon';
import { interceptNavigation, type Navigate } from './SessionLink';

export function Sidebar({ bootstrap, activePageId, open, close, navigate, onSearch, onCreate, onSettings }: {
  bootstrap: Bootstrap | null; activePageId: string; open: boolean; close: () => void; navigate: Navigate; onSearch: () => void; onCreate: () => void; onSettings: () => void;
}) {
  const { t } = useLanguage();
  const pages = bootstrap?.pages.filter(page => page.kind === 'work') ?? [];
  return <>
    {open ? <button className="sidebar-scrim" aria-label={t('sidebar.closeNavigation')} onClick={close} /> : null}
    <aside className={`sidebar ${open ? 'is-open' : ''}`} aria-label={t('sidebar.navigation')}>
      <a href="/" className="wordmark" onClick={event => interceptNavigation(event, () => navigate('home'))}><Icon name="pulse" size={30} /><span>Per Pulse</span></a>
      <nav className="main-nav">
        <a href="/" className={`nav-row ${activePageId === 'home' ? 'is-active' : ''}`} aria-current={activePageId === 'home' ? 'page' : undefined} onClick={event => interceptNavigation(event, () => navigate('home'))}><Icon name="home" /><span>{t('sidebar.home')}</span></a>
        <button className="nav-row" onClick={onSearch}><Icon name="search" /><span>{t('sidebar.search')}</span></button>
      </nav>
      <div className="pages-label"><span>{t('sidebar.pages')}</span><button className="icon-button" aria-label={t('sidebar.createPage')} title={t('sidebar.createPage')} onClick={onCreate}><Icon name="plus" size={17} /></button></div>
      <nav className="page-nav" aria-label={t('sidebar.pages')}>
        {pages.map(page => <a key={page.id} href={pageHref(page.id)} className={`nav-row ${activePageId === page.id ? 'is-active' : ''}`} aria-current={activePageId === page.id ? 'page' : undefined} onClick={event => interceptNavigation(event, () => navigate(page.id))}><Icon name="page" /><span>{page.title}</span>{page.status === 'working' ? <span className="working-dot" aria-label={t('sidebar.working')} /> : null}</a>)}
        {!pages.length ? <button className="empty-pages" onClick={onCreate}>{t('sidebar.emptyPages')}</button> : null}
      </nav>
      <div className="sidebar-footer">
        <div className="workspace-label">{t(bootstrap?.example ? 'sidebar.exampleWorkspace' : 'sidebar.yourWorkspace')}</div>
        {bootstrap ? <button className={`provider-indicator ${bootstrap.provider.configured ? 'is-connected' : ''}`} onClick={onSettings}><span />{t(bootstrap.provider.configured ? 'sidebar.aiConnected' : 'sidebar.aiDisconnected')}</button> : null}
        <div className="footer-rule" />
        <button className="nav-row" onClick={onSettings}><Icon name="settings" /><span>{t('sidebar.settings')}</span></button>
      </div>
    </aside>
  </>;
}
