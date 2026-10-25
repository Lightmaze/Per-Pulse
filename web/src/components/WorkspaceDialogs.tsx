import { useRef, useState } from 'react';
import type { Bootstrap, Message, PageSummary, ResponseLanguage } from '../types';
import { useLanguage } from '../i18n';
import { Dialog } from './Dialog';
import { Icon } from './Icon';

export type Modal =
  | { kind: 'create'; message?: Message }
  | { kind: 'picker'; mode: 'search' | 'compose' | 'reference'; message?: Message }
  | { kind: 'return'; message: Message; content: string }
  | { kind: 'settings' }
  | { kind: 'rename' };

export function CreatePageDialog({ initial, busy, error, close, create }: { initial?: Message; busy: boolean; error: string; close: () => void; create: (title: string, instruction: string) => void }) {
  const { t } = useLanguage();
  const [title, setTitle] = useState('');
  const [instruction, setInstruction] = useState(initial?.content ?? '');
  return <Dialog title={t('dialogs.createTitle')} onClose={close}>
    <p className="dialog-description">{t('dialogs.createDescription')}</p>
    <form onSubmit={event => { event.preventDefault(); if (title.trim() && instruction.trim() && !busy) create(title.trim(), instruction.trim()); }}>
      <label className="field-label" htmlFor="new-page-title">{t('dialogs.pageName')}</label>
      <input id="new-page-title" autoFocus value={title} onChange={event => setTitle(event.target.value)} placeholder={t('dialogs.namePlaceholder')} maxLength={160} required />
      <label className="field-label" htmlFor="new-page-focus">{t('dialogs.focusLabel')}</label>
      <textarea id="new-page-focus" value={instruction} onChange={event => setInstruction(event.target.value)} placeholder={t('dialogs.focusPlaceholder')} rows={5} required />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>{t('dialogs.cancel')}</button><button type="submit" className="primary-button" disabled={busy || !title.trim() || !instruction.trim()}>{t(busy ? 'dialogs.creating' : 'dialogs.createPage')}</button></div>
    </form>
  </Dialog>;
}

export function PagePickerDialog({ pages, mode, busy, error, close, select, create }: { pages: PageSummary[]; mode: 'search' | 'compose' | 'reference'; busy: boolean; error: string; close: () => void; select: (page: PageSummary) => void; create: () => void }) {
  const { t, pageTitle } = useLanguage();
  const [query, setQuery] = useState('');
  const description = (page: PageSummary) => page.id === 'home' ? t('dialogs.homeDescription') : page.description;
  const matches = pages.filter(page => `${page.title} ${pageTitle(page)} ${page.description} ${description(page)}`.toLowerCase().includes(query.toLowerCase()));
  return <Dialog title={t(mode === 'search' ? 'dialogs.findTitle' : 'dialogs.linkTitle')} onClose={close} className="picker-dialog">
    <div className="search-field"><Icon name="search" size={19} /><input aria-label={t('dialogs.searchLabel')} placeholder={t('dialogs.searchPlaceholder')} value={query} onChange={event => setQuery(event.target.value)} autoFocus /></div>
    <div className="picker-results">
      {matches.map(page => <button key={page.id} className="picker-result" disabled={busy} onClick={() => select(page)}><Icon name={page.kind === 'home' ? 'home' : 'page'} /><span><strong>{pageTitle(page)}</strong>{description(page) ? <small>{description(page)}</small> : null}</span><Icon name="chevron" size={16} /></button>)}
      {!matches.length ? <p className="picker-empty">{t('dialogs.noPages')}</p> : null}
    </div>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    <button className="create-from-picker" onClick={create}><Icon name="plus" size={18} />{t('dialogs.createNewPage')}</button>
  </Dialog>;
}

export function ReturnDialog({ initial, busy, error, close, send }: { initial: string; busy: boolean; error: string; close: () => void; send: (content: string) => void }) {
  const { t } = useLanguage();
  const [content, setContent] = useState(initial);
  return <Dialog title={t('dialogs.returnTitle')} onClose={close}>
    <p className="dialog-description">{t('dialogs.returnDescription')}</p>
    <form onSubmit={event => { event.preventDefault(); if (content.trim() && !busy) send(content.trim()); }}>
      <label className="field-label" htmlFor="return-content">{t('dialogs.returnContent')}</label>
      <textarea id="return-content" autoFocus value={content} onChange={event => setContent(event.target.value)} rows={7} required />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>{t('dialogs.cancel')}</button><button className="primary-button" type="submit" disabled={busy || !content.trim()}>{t(busy ? 'dialogs.saving' : 'dialogs.bringHome')}</button></div>
    </form>
  </Dialog>;
}

export function SettingsDialog({ bootstrap, close, refresh, saveSettings }: {
  bootstrap: Bootstrap | null;
  close: () => void;
  refresh: () => void;
  saveSettings: (responseLanguage: ResponseLanguage) => Promise<void>;
}) {
  const { t, preference, preferenceSaved, setPreference, errorText } = useLanguage();
  const [pendingLanguage, setPendingLanguage] = useState<ResponseLanguage | null>(null);
  const [settingsError, setSettingsError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const saving = useRef(false);
  const changeResponseLanguage = async (next: ResponseLanguage) => {
    if (saving.current) return;
    saving.current = true;
    setPendingLanguage(next);
    setSettingsError(null);
    setSaved(false);
    try {
      await saveSettings(next);
      setSaved(true);
    } catch (error) {
      setSettingsError(error);
    } finally {
      setPendingLanguage(null);
      saving.current = false;
    }
  };

  return <Dialog title={t('settings.title')} onClose={() => { if (!saving.current) close(); }}>
    <div className="settings-section">
      <h3>{t('settings.languageHeading')}</h3>
      <div className="language-setting">
        <label className="field-label" htmlFor="interface-language">{t('settings.interfaceLanguage')}</label>
        <select id="interface-language" value={preference} aria-describedby="interface-language-hint" onChange={event => {
          const next = event.target.value;
          if (next === 'system' || next === 'en' || next === 'zh-CN') setPreference(next);
        }}>
          <option value="system">{t('settings.followSystem')}</option>
          <option value="en" lang="en">{t('settings.english')}</option>
          <option value="zh-CN" lang="zh-CN">{t('settings.chinese')}</option>
        </select>
        <p id="interface-language-hint" className="settings-detail">{t('settings.interfaceHint')}</p>
        {!preferenceSaved ? <p className="settings-detail" role="status">{t('settings.interfaceNotSaved')}</p> : null}
      </div>
      <div className="language-setting" aria-busy={pendingLanguage !== null}>
        <label className="field-label" htmlFor="response-language">{t('settings.responseLanguage')}</label>
        <select id="response-language" value={pendingLanguage ?? bootstrap?.settings?.responseLanguage ?? 'auto'} disabled={!bootstrap || pendingLanguage !== null} aria-describedby="response-language-hint" onChange={event => {
          const next = event.target.value;
          if (next === 'auto' || next === 'en' || next === 'zh-CN') void changeResponseLanguage(next);
        }}>
          <option value="auto">{t('settings.followConversation')}</option>
          <option value="en" lang="en">{t('settings.english')}</option>
          <option value="zh-CN" lang="zh-CN">{t('settings.chinese')}</option>
        </select>
        <p id="response-language-hint" className="settings-detail">{t('settings.responseHint')}</p>
      </div>
      {settingsError ? <p className="form-error" role="alert">{errorText(settingsError)}</p> : null}
      <p className="settings-language-status" role="status">{pendingLanguage !== null ? t('settings.responseSaving') : saved ? t('settings.responseSaved') : ''}</p>
    </div>
    <div className="settings-section">
      <h3>{t('settings.aiConnection')}</h3>
      <p className="connection-label"><span className={`connection-dot ${bootstrap?.provider.configured ? 'connected' : ''}`} />{bootstrap ? t(bootstrap.provider.configured ? 'sidebar.aiConnected' : 'sidebar.aiDisconnected') : t('settings.checkingConnection')}</p>
      <p>{bootstrap?.provider.configured ? t('settings.connectedTo', { model: bootstrap.provider.model ?? t('settings.configuredModel') }) : t('settings.disconnected')}</p>
      {bootstrap ? <p className="settings-detail">{t(bootstrap.provider.configured ? 'settings.providerContext' : bootstrap.example ? 'settings.exampleProvider' : 'settings.queuedProvider')}</p> : null}
      <button className="secondary-button" onClick={refresh}>{t('settings.checkConnection')}</button>
    </div>
    <div className="settings-section">
      <h3>{t(bootstrap?.example ? 'settings.exampleWorkspace' : 'settings.yourConversations')}</h3>
      <p>{t(bootstrap?.example ? 'settings.exampleDescription' : 'settings.conversationDescription')}</p>
      <p>{t('settings.browserPersistence')}</p>
    </div>
  </Dialog>;
}

export function RenameDialog({ initial, busy, error, close, rename }: { initial: string; busy: boolean; error: string; close: () => void; rename: (title: string) => void }) {
  const { t } = useLanguage();
  const [title, setTitle] = useState(initial);
  return <Dialog title={t('dialogs.renameTitle')} onClose={close}>
    <form onSubmit={event => { event.preventDefault(); if (title.trim() && !busy) rename(title.trim()); }}>
      <label className="field-label" htmlFor="rename-title">{t('dialogs.pageName')}</label>
      <input id="rename-title" autoFocus value={title} onChange={event => setTitle(event.target.value)} maxLength={160} required />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>{t('dialogs.cancel')}</button><button className="primary-button" type="submit" disabled={busy || !title.trim()}>{t(busy ? 'dialogs.saving' : 'dialogs.saveName')}</button></div>
    </form>
  </Dialog>;
}
