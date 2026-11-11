import { useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import type { Draft, ProviderState } from '../types';
import { Icon } from './Icon';
import { useLanguage } from '../i18n';
import { CopyButton } from './CopyButton';
import { isSupportedUpload } from '../uploadLimits';

export function Composer({ draft, changeDraft, send, busy, onLink, onMaterials, onInspectMaterial, onPasteFiles, pastingImage = false, inputRef, pageTitle, pageKind = 'home', provider, onSettings, draftSaveFailed = false, retryDraftSave }: {
  draft: Draft; changeDraft: (draft: Draft) => void; send: () => void; busy: boolean; onLink: () => void; inputRef: RefObject<HTMLTextAreaElement | null>;
  pageTitle?: string; pageKind?: 'home' | 'work'; provider?: ProviderState; onSettings?: () => void;
  onMaterials?: () => void;
  onInspectMaterial?: (materialId: string) => void;
  onPasteFiles?: (files: File[]) => void;
  pastingImage?: boolean;
  draftSaveFailed?: boolean;
  retryDraftSave?: () => void;
}) {
  const { t, pageTitle: localizedPageTitle } = useLanguage();
  const composing = useRef(false);
  const [draggingFile, setDraggingFile] = useState(false);
  const [unsupportedDrop, setUnsupportedDrop] = useState(false);
  const connectionId = useId();
  const destination = pageKind === 'home' ? localizedPageTitle({ id: 'home', title: 'Home' }) : pageTitle || t('composer.thisPage');
  const disconnected = provider && !provider.configured;
  const canSend = !!draft.text.trim() || !!draft.materials?.length;
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = 'auto'; input.style.height = `${Math.min(220, Math.max(38, input.scrollHeight))}px`; }
  }, [draft.text, inputRef]);
  return <div className="composer-wrap">
    <form className={`composer${draggingFile ? ' composer-drag-target' : ''}`}
      onDragOver={event => {
        if (!onPasteFiles || !event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        if (!draggingFile) setDraggingFile(true);
      }}
      onDragLeave={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDraggingFile(false);
      }}
      onDrop={event => {
        if (!onPasteFiles || !event.dataTransfer.files.length) return;
        event.preventDefault(); // A file drop never navigates away or sends a model turn.
        setDraggingFile(false);
        const files = Array.from(event.dataTransfer.files);
        const supported = files.filter(isSupportedUpload);
        setUnsupportedDrop(supported.length !== files.length);
        if (supported.length) onPasteFiles(supported); // The existing durable draft upload path.

      }}
      onSubmit={event => { event.preventDefault(); if (canSend && !busy && !pastingImage) send(); }}>
      {draft.materials?.length ? <ul className="composer-materials" aria-label={t('materials.forMessage')}>{draft.materials.map(material => <li key={material.id}>{onInspectMaterial ? <button type="button" className="composer-material-inspect" aria-label={t('materials.inspectDraft', { name: material.name })} onClick={() => onInspectMaterial(material.id)}>{material.name}</button> : <span>{material.name}</span>}<button type="button" className="composer-material-remove" aria-label={t('materials.remove', { name: material.name })} onClick={() => changeDraft({ ...draft, materials: draft.materials?.filter(item => item.id !== material.id) })}><Icon name="close" size={14} /></button></li>)}</ul> : null}
      <textarea ref={inputRef} value={draft.text} onChange={event => { setUnsupportedDrop(false); changeDraft({ ...draft, text: event.target.value }); }} onPaste={event => {
        if (!onPasteFiles) return;
        const files = Array.from(event.clipboardData.items)
          .filter(item => item.kind === 'file')
          .map(item => item.getAsFile())
          .filter((file): file is File => file !== null);
        if (files.length) {
          event.preventDefault();
          const supported = files.filter(isSupportedUpload);
          setUnsupportedDrop(supported.length !== files.length);
          if (supported.length) onPasteFiles(supported);
        }
      }} placeholder={pageKind === 'work' ? t('composer.continueIn', { destination }) : t('composer.placeholder')} aria-label={t('composer.messageTo', { destination })} aria-describedby={disconnected ? connectionId : undefined} rows={1} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !composing.current && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); if (canSend && !busy && !pastingImage) send(); }
      }} />
      <div className="composer-toolbar">
        <div className="composer-tools"><button type="button" className="text-button link-page-button" onClick={onLink}><Icon name="link" size={17} />{t('composer.linkPage')}{draft.references.length ? <span className="reference-count">{draft.references.length}</span> : null}</button>
        {onMaterials ? <button type="button" className="text-button attach-file-button" onClick={onMaterials}><Icon name="page" size={17} />{t('library.title')}</button> : null}
        </div>
        <button className="send-button" type="submit" aria-label={t('composer.send')} disabled={busy || pastingImage || !canSend}><Icon name="send" size={21} /></button>
      </div>
    </form>
    {draftSaveFailed ? <div className="composer-connection composer-draft-notice" role="status">
      <span>{t(draft.text || draft.references.length || draft.materials?.length ? 'composer.draftNotSaved' : 'composer.draftNotCleared')}</span>{' '}
      {draft.text ? <CopyButton text={draft.text} label={t('composer.copyDraftText')} /> : null}{' '}
      {retryDraftSave ? <button type="button" className="text-button" onClick={retryDraftSave}>{t('composer.retryDraftSave')}</button> : null}
    </div> : null}
    {disconnected ? <p id={connectionId} className="composer-connection">{t('composer.disconnected')}{onSettings ? <button type="button" className="text-button composer-connection-action" onClick={onSettings}>{t('composer.connectionSettings')}</button> : null}</p> : null}
    <p className="composer-hint" role="status">{pastingImage ? t('composer.pastingImage') : draggingFile ? t('composer.dropImage') : unsupportedDrop ? t('composer.dropUnsupported') : busy ? t('composer.saving') : t('composer.hint')}</p>
  </div>;
}
