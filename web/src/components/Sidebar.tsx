import type { Bootstrap } from '../types';
import { pageHref } from '../storage';
import { Icon } from './Icon';
import { interceptNavigation, type Navigate } from './SessionLink';

export function Sidebar({ bootstrap, activePageId, stale = false, open, close, navigate, onSearch, onCreate, onSettings }: {
  bootstrap: Bootstrap | null; activePageId: string; stale?: boolean; open: boolean; close: () => void; navigate: Navigate; onSearch: () => void; onCreate: () => void; onSettings: () => void;
}) {
  const pages = bootstrap?.pages.filter(page => page.kind === 'work') ?? [];
  return <>
    {open ? <button className="sidebar-scrim" aria-label="Close navigation" onClick={close} /> : null}
    <aside className={`sidebar ${open ? 'is-open' : ''}`} aria-label="Workspace navigation">
      <a href="/" className="wordmark" onClick={event => interceptNavigation(event, () => navigate('home'))}><Icon name="pulse" size={30} /><span>Per Pulse</span></a>
      <nav className="main-nav">
        <a href="/" className={`nav-row ${activePageId === 'home' ? 'is-active' : ''}`} aria-current={activePageId === 'home' ? 'page' : undefined} onClick={event => interceptNavigation(event, () => navigate('home'))}><Icon name="home" /><span>Home</span></a>
        <button className="nav-row" onClick={onSearch}><Icon name="search" /><span>Search</span></button>
      </nav>
      <div className="pages-label"><span>Pages</span><button className="icon-button" aria-label="Create a page" title="Create a page" onClick={onCreate}><Icon name="plus" size={17} /></button></div>
      <nav className="page-nav" aria-label="Pages">
        {pages.map(page => <a key={page.id} href={pageHref(page.id)} className={`nav-row ${activePageId === page.id ? 'is-active' : ''}`} aria-current={activePageId === page.id ? 'page' : undefined} onClick={event => interceptNavigation(event, () => navigate(page.id))}><Icon name="page" /><span>{page.title}</span>{page.status === 'working' && bootstrap?.provider.configured && !stale ? <span className="working-dot" aria-label="Working" /> : null}</a>)}
        {!pages.length ? <button className="empty-pages" onClick={onCreate}>Make room for a new idea.</button> : null}
      </nav>
      <div className="sidebar-footer">
        <div className="workspace-label">{bootstrap?.example ? 'Example workspace' : 'Your workspace'}</div>
        {bootstrap ? <button className={`provider-indicator ${bootstrap.provider.configured ? 'is-connected' : ''}`} onClick={onSettings}><span />{bootstrap.provider.label}</button> : null}
        <div className="footer-rule" />
        <button className="nav-row" onClick={onSettings}><Icon name="settings" /><span>Settings</span></button>
      </div>
    </aside>
  </>;
}
