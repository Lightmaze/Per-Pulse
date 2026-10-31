import { useEffect, useId, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { Bootstrap, Material, Message, PageSummary, ResponseLanguage } from '../types';
import { Dialog } from './Dialog';
import { Icon } from './Icon';
import { messageBody } from '../messageBody';
import { isExactWorkHandoffExcerpt } from '../workHandoffExcerpt';
import { providerSetupCommand, type ProviderShell } from '../providerSetup';
import { CopyButton } from './CopyButton';

export type Modal =
  | { kind: 'create'; message?: Message }
  | { kind: 'picker'; mode: 'search' | 'compose' | 'reference'; message?: Message }
  | { kind: 'return'; message: Message; content: string; needsExactExcerpt?: boolean }
  | { kind: 'transfer'; message: Message; excerpt: string; needsExactExcerpt?: boolean }
  | { kind: 'settings' }
  | { kind: 'learning' }
  | { kind: 'save-example'; message: Message; content: string; needsExactExcerpt?: boolean }
  | { kind: 'history'; beforeMessageId: string }
  | { kind: 'message-search' }
  | { kind: 'materials'; materialId?: string; pageId?: string }
  | { kind: 'rename' };

export function CreatePageDialog({ initial, busy, error, close, create }: { initial?: Message; busy: boolean; error: string; close: () => void; create: (title: string, instruction: string) => void }) {
  const { t } = useLanguage();
  const [title, setTitle] = useState('');
  const [instruction, setInstruction] = useState(initial?.materials?.length ? messageBody(initial) : initial?.content ?? '');
  return <Dialog title={t("dialogs.createTitle")} onClose={close}>
    <p className="dialog-description">{t('dialogs.createDescription')}</p>
    {initial?.materials?.length ? <p className="lesson-note">{t('materials.newPageSource')}</p> : null}
    <form onSubmit={event => { event.preventDefault(); if (title.trim() && instruction.trim() && !busy) create(title.trim(), instruction.trim()); }}>
      <label className="field-label" htmlFor="new-page-title">{t('dialogs.pageName')}</label>
      <input id="new-page-title" autoFocus value={title} onChange={event => setTitle(event.target.value)} placeholder={t('dialogs.namePlaceholder')} maxLength={160} required />
      <label className="field-label" htmlFor="new-page-focus">{t('dialogs.focusLabel')}</label>
      <textarea id="new-page-focus" value={instruction} onChange={event => setInstruction(event.target.value)} placeholder={t('dialogs.focusPlaceholder')} rows={5} required />
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>{t('dialogs.cancel')}</button><button type="submit" className="primary-button" disabled={busy || !title.trim() || !instruction.trim()}>{busy ? t('dialogs.creating') : t('dialogs.createPage')}</button></div>
    </form>
  </Dialog>;
}

export function PagePickerDialog({ pages, mode, busy, error, close, select, create }: { pages: PageSummary[]; mode: 'search' | 'compose' | 'reference'; busy: boolean; error: string; close: () => void; select: (page: PageSummary) => void; create: () => void }) {
  const { t } = useLanguage();
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const pickerId = useId();
  const matches = pages.filter(page => `${page.title} ${page.description}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selectedIndex = Math.min(activeIndex, Math.max(0, matches.length - 1));
  const activeId = matches[selectedIndex] ? `${pickerId}-${matches[selectedIndex].id}` : undefined;
  useEffect(() => { if (activeId) document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' }); }, [activeId]);
  return <Dialog title={mode === 'search' ? t('dialogs.findTitle') : t('dialogs.linkTitle')} onClose={close} className="picker-dialog">
    <div className="search-field"><Icon name="search" size={19} /><input aria-label={t("dialogs.searchLabel")} role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls={`${pickerId}-results`} aria-activedescendant={activeId} aria-describedby={`${pickerId}-hint`} placeholder={t('dialogs.searchPlaceholder')} value={query} onChange={event => { setQuery(event.target.value); setActiveIndex(0); }} onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (matches.length) setActiveIndex(event.key === 'ArrowDown' ? Math.min(selectedIndex + 1, matches.length - 1) : Math.max(selectedIndex - 1, 0));
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (!busy && matches[selectedIndex]) select(matches[selectedIndex]);
      }
    }} autoFocus /></div>
    <p className="picker-results-meta" role="status">{t(query.trim() ? (matches.length === 1 ? 'dialogs.oneFound' : 'dialogs.manyFound') : (matches.length === 1 ? 'dialogs.oneAvailable' : 'dialogs.manyAvailable'), { count: matches.length })}</p>
    <div className="picker-results" id={`${pickerId}-results`} role="listbox" aria-label={t('sidebar.pages')}>
      {matches.map((page, index) => <button key={page.id} id={`${pickerId}-${page.id}`} type="button" role="option" aria-selected={index === selectedIndex} tabIndex={-1} className="picker-result" disabled={busy} onMouseEnter={() => setActiveIndex(index)} onClick={() => select(page)}><Icon name={page.kind === 'home' ? 'home' : 'page'} /><span><strong>{page.title}</strong>{page.description ? <small>{page.description}</small> : null}</span><Icon name="chevron" size={16} /></button>)}
    </div>
    {!matches.length ? <p className="picker-empty">{pages.length ? t('dialogs.noMatching') : t('dialogs.noPagesYet')}</p> : null}
    <p id={`${pickerId}-hint`} className="picker-keyboard-hint">{t('dialogs.chooseHint', { action: mode === 'search' ? t('dialogs.openAction') : t('dialogs.linkAction') })}</p>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    <button className="create-from-picker" disabled={busy} onClick={create}><Icon name="plus" size={18} />{t('dialogs.createNewPage')}</button>
  </Dialog>;
}

export function TransferWorkDialog({ excerpt, sourceText, needsExactExcerpt = false, destinations, busy, error, close, send, sourceTitle }: {
  excerpt: string; sourceText: string; needsExactExcerpt?: boolean;
  destinations: PageSummary[]; busy: boolean; error: string; close: () => void;
  send: (targetId: string, excerpt: string, instruction: string) => void; sourceTitle?: string;
}) {
  const { t } = useLanguage();
  const [targetId, setTargetId] = useState(destinations[0]?.id ?? '');
  const [chosenExcerpt, setChosenExcerpt] = useState(excerpt);
  const [instruction, setInstruction] = useState('');
  const isExact = isExactWorkHandoffExcerpt(sourceText, chosenExcerpt);
  const tooLong = chosenExcerpt.length + instruction.length + 100 > 32_000;
  const canSend = !!targetId && isExact && !!instruction.trim() && !tooLong;
  return <Dialog title={t('dialogs.transferTitle')} onClose={close}>
    <p className="dialog-description">{t('dialogs.transferDescription')}</p>
    <p className="lesson-note">{t('dialogs.from')} <strong>{sourceTitle || t('dialogs.thisPage')}</strong></p>
    <form onSubmit={event => { event.preventDefault(); if (canSend && !busy) send(targetId, chosenExcerpt, instruction.trim()); }}>
      <label className="field-label" htmlFor="transfer-work-target">{t('dialogs.transferDestination')}</label>
      <select id="transfer-work-target" value={targetId} onChange={event => setTargetId(event.target.value)} required disabled={busy}>
        {destinations.map(page => <option key={page.id} value={page.id}>{page.title}</option>)}
      </select>
      <label className="field-label" htmlFor="transfer-work-excerpt">{t('dialogs.transferExcerpt')}</label>
      {needsExactExcerpt ? <p className="lesson-note" role="status">{t('dialogs.transferSelectionMismatch')}</p> : null}
      <textarea id="transfer-work-excerpt" value={chosenExcerpt} onChange={event => setChosenExcerpt(event.target.value)}
        rows={6} aria-invalid={Boolean(chosenExcerpt.trim()) && !isExact} disabled={busy} autoFocus={needsExactExcerpt} />
      {chosenExcerpt.trim() && !isExact ? <p role="alert" className="form-error">{t('dialogs.transferInvalidExcerpt')}</p> : null}
      <details>
        <summary>{t('dialogs.transferRawSource')}</summary>
        <textarea aria-label={t('dialogs.transferRawSource')} readOnly value={sourceText} rows={6} />
      </details>
      <button className="text-button" type="button" disabled={busy} onClick={() => setChosenExcerpt(sourceText)}>
        {t('dialogs.transferFullResult')}
      </button>
      <label className="field-label" htmlFor="transfer-work-instruction">{t('dialogs.transferInstruction')}</label>
      <textarea id="transfer-work-instruction" value={instruction} onChange={event => setInstruction(event.target.value)}
        placeholder={t('dialogs.transferPlaceholder')} rows={3} maxLength={2000} required autoFocus={!needsExactExcerpt} disabled={busy} />
      {tooLong ? <p role="alert" className="form-error">{t('dialogs.transferTooLong')}</p> : null}
      {error ? <p role="alert" className="form-error">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>{t('dialogs.cancel')}</button>
        <button type="submit" className="primary-button" disabled={busy || !canSend}>{busy ? t('dialogs.saving') : t('dialogs.transferAction')}</button></div>
    </form>
  </Dialog>;
}

export function ReturnDialog({ initial, sourceText, needsExactExcerpt = false, busy, error, close, send, sourceTitle, materials = [] }: {
  initial: string; sourceText: string; needsExactExcerpt?: boolean; busy: boolean; error: string;
  close: () => void; send: (content: string, includeMaterials: boolean) => void;
  sourceTitle?: string; materials?: Material[];
}) {
  const { t } = useLanguage();
  const [content, setContent] = useState(initial);
  const [includeMaterials, setIncludeMaterials] = useState(materials.length > 0);
  // Files-only returns remain supported normally. A failed text selection,
  // however, must be resolved deliberately before any Home disclosure.
  const canReturn = (!!content.trim() || includeMaterials) && (!needsExactExcerpt || !!content.trim());
  return <Dialog title={t("dialogs.returnTitle")} onClose={close}>
    <p className="dialog-description">{t('dialogs.returnDescription')}</p>
    <div className="return-preview"><span className="return-preview-source">{t('dialogs.from')} <strong>{sourceTitle || t('dialogs.thisPage')}</strong></span><span aria-hidden="true">→</span><span className="return-preview-destination">{t('sidebar.home')}</span></div>
    <form onSubmit={event => { event.preventDefault(); if (canReturn && !busy) send(content.trim(), includeMaterials); }}>
      <label className="field-label" htmlFor="return-content">{t('dialogs.returnContent')}</label>
      {needsExactExcerpt ? <p className="lesson-note" role="status">{t('dialogs.returnSelectionMismatch')}</p> : null}
      <textarea id="return-content" autoFocus value={content} onChange={event => setContent(event.target.value)} rows={7}
        required={!includeMaterials || needsExactExcerpt} disabled={busy} />
      {needsExactExcerpt ? <>
        <details>
          <summary>{t('dialogs.returnRawSource')}</summary>
          <textarea aria-label={t('dialogs.returnRawSource')} readOnly value={sourceText} rows={6} />
        </details>
        <button className="text-button" type="button" disabled={busy} onClick={() => setContent(sourceText)}>
          {t('dialogs.returnFullResult')}
        </button>
      </> : null}
      {materials.length ? <div className="return-materials"><label><input type="checkbox" checked={includeMaterials} disabled={busy} onChange={event => setIncludeMaterials(event.target.checked)} />{t('materials.include')}</label><p className="lesson-note">{materials.map(item => item.name).join(', ')}</p></div> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>{t('dialogs.cancel')}</button><button className="primary-button" type="submit" disabled={busy || !canReturn}>{busy ? t('dialogs.saving') : t('dialogs.bringHome')}</button></div>
    </form>
  </Dialog>;
}

export function SettingsDialog({ bootstrap, close, refresh, learning }: { bootstrap: Bootstrap | null; close: () => void; refresh: () => void; learning: () => void }) {
  const { t, preference, preferenceSaved, setPreference } = useLanguage();
  const [language, setLanguage] = useState<ResponseLanguage>(bootstrap?.settings?.responseLanguage ?? 'auto');
  const [saving, setSaving] = useState(false);
  const [languageError, setLanguageError] = useState('');
  const [setupShell, setSetupShell] = useState<ProviderShell>('posix');
  const setupCommand = providerSetupCommand(setupShell);
  const changeLanguage = async (next: ResponseLanguage) => {
    if (saving || next === language) return;
    setSaving(true);
    setLanguageError('');
    try {
      const saved = await api.updateSettings(next);
      setLanguage(saved.responseLanguage);
      refresh();
    } catch (problem) {
      setLanguageError(problem instanceof Error ? problem.message : t('settings.replyError'));
    } finally {
      setSaving(false);
    }
  };
  return <Dialog title={t('settings.title')} onClose={close}>
    <div className="settings-section"><h3>{t('settings.aiConnection')}</h3><p className="connection-label"><span className={`connection-dot ${bootstrap?.provider.configured ? 'connected' : ''}`} />{bootstrap?.provider.label ?? t('settings.checkingConnection')}</p><p>{bootstrap?.provider.configured ? t('settings.connectedTo', { model: bootstrap.provider.model ?? t('settings.configuredModel') }) : t('settings.disconnected')}</p>{bootstrap?.provider.detail ? <p className="settings-detail">{bootstrap.provider.detail}</p> : null}<button className="secondary-button" onClick={refresh}>{t('settings.checkConnection')}</button>
      {bootstrap && !bootstrap.provider.configured && !bootstrap.example ? <details className="provider-setup">
        <summary>{t('settings.connectGuide')}</summary>
        <p>{t('settings.connectSteps')}</p>
        <label className="field-label" htmlFor="provider-terminal-shell">{t('settings.terminalShell')}</label>
        <select id="provider-terminal-shell" value={setupShell}
          onChange={event => setSetupShell(event.target.value as ProviderShell)}>
          <option value="posix">{t('settings.shellPosix')}</option>
          <option value="powershell">{t('settings.shellPowerShell')}</option>
        </select>
        <pre aria-label={t('settings.connectCommand')}><code>{setupCommand}</code></pre>
        <CopyButton text={setupCommand} label={t('settings.copySetup')} />
        <p className="settings-detail">{t('settings.doctorHint')}</p>
        <p>{t('settings.connectionSafety')}</p>
      </details> : null}</div>
    <div className="settings-section">
      <h3>{t('settings.languageHeading')}</h3>
      <label className="field-label" htmlFor="interface-language">{t('settings.interfaceLanguage')}</label>
      <select id="interface-language" value={preference} onChange={event => setPreference(event.target.value as 'system' | 'en' | 'zh-CN')}>
        <option value="system">{t('settings.followSystem')}</option>
        <option value="en">{t('settings.english')}</option>
        <option value="zh-CN">{t('settings.chinese')}</option>
      </select>
      <p className="settings-detail">{t('settings.interfaceHint')}</p>
      {!preferenceSaved ? <p role="status">{t('settings.interfaceNotSaved')}</p> : null}
    </div>
    <div className="settings-section">
      <h3>{t('settings.responseLanguage')}</h3>
      <label className="field-label" htmlFor="response-language">{t('settings.replyLabel')}</label>
      <select id="response-language" value={language} disabled={saving} onChange={event => void changeLanguage(event.target.value as ResponseLanguage)}>
        <option value="auto">{t('settings.auto')}</option>
        <option value="en">{t('settings.english')}</option>
        <option value="zh-CN">{t('settings.chinese')}</option>
      </select>
      <p className="settings-detail">{t('settings.replyHint')}</p>
      {saving ? <p role="status">{t('settings.saving')}</p> : null}
      {languageError ? <p className="form-error" role="alert">{languageError}</p> : null}
    </div>
    <div className="settings-section"><h3>{t('settings.actions')}</h3><p>{bootstrap?.provider.workspaceActions ? t('settings.actionsOn') : t('settings.actionsOff')}</p></div>
    <div className="settings-section"><h3>{t('settings.learning')}</h3><p>{bootstrap?.postureEnabled ? t('settings.learningOn') : t('settings.learningOff')}</p><button className="secondary-button" onClick={learning}>{t('settings.lessons')}</button></div>
    <div className="settings-section"><h3>{bootstrap?.example ? t('settings.exampleWorkspace') : t('settings.yourConversations')}</h3><p>{bootstrap?.example ? t('settings.exampleDescription') : t('settings.conversationDescription')}</p><p>{t('settings.savedDraft')}</p></div>
  </Dialog>;
}

export function RenameDialog({ initial, busy, error, close, rename }: { initial: string; busy: boolean; error: string; close: () => void; rename: (title: string) => void }) {
  const { t } = useLanguage();
  const [title, setTitle] = useState(initial);
  return <Dialog title={t("dialogs.renameTitle")} onClose={close}><form onSubmit={event => { event.preventDefault(); if (title.trim() && !busy) rename(title.trim()); }}><label className="field-label" htmlFor="rename-title">{t('dialogs.pageName')}</label><input id="rename-title" autoFocus value={title} onChange={event => setTitle(event.target.value)} maxLength={160} required />{error ? <p className="form-error" role="alert">{error}</p> : null}<div className="dialog-actions"><button type="button" className="secondary-button" onClick={close}>{t('dialogs.cancel')}</button><button className="primary-button" type="submit" disabled={busy || !title.trim()}>{busy ? t('dialogs.saving') : t('dialogs.saveName')}</button></div></form></Dialog>;
}
