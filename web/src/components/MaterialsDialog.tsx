import { useEffect, useId, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { useLanguage, type MessageKey } from '../i18n';
import type { Material, WorkspaceFile, WorkspaceFileIdentity, WorkspaceFileList } from '../types';
import { Dialog } from './Dialog';
import { Icon } from './Icon';
import { TextDocument } from './TextDocument';

const MAX_BYTES = 1024 * 1024;
const PREVIEW_CHARS = 64_000;
// The same native file errors also serve result downloads. Keep upload advice
// in this flow rather than replacing their explanations across the product.
const materialErrors: Record<string, MessageKey> = {
  workspace_changed: 'library.workspaceChanged', file_too_large: 'library.tooLarge',
  empty_file: 'library.emptyFile', text_files_only: 'library.textOnly',
  credential_content_blocked: 'library.credentials', protected_or_invalid_path: 'library.invalidPath',
  invalid_path: 'library.invalidPath', filename_only: 'library.invalidPath',
  file_changed_retry: 'library.changed', file_unavailable: 'library.unavailable',
  path_unavailable: 'library.unavailable', not_a_single_regular_file: 'library.invalidPath',
  upload_failed_or_unconfirmed: 'library.uploadUnconfirmed',
  material_file_too_large: 'library.tooLarge', material_too_large: 'library.tooLarge',
  material_text_files_only: 'library.textOnly', material_credential_content_blocked: 'library.credentials',
  material_upload_unconfirmed: 'library.uploadUnconfirmed',
  material_reference_unconfirmed: 'library.referenceUnconfirmed',
};

export function MaterialsDialog({ identity, pageId, selectedIds, close, add }: {
  identity: WorkspaceFileIdentity; pageId: string; selectedIds: string[];
  close: () => void; add: (material: Material) => boolean;
}) {
  const { t, errorText } = useLanguage();
  const [listing, setListing] = useState<WorkspaceFileList | null>(null);
  const [preview, setPreview] = useState<WorkspaceFile | null>(null);
  const [selected, setSelected] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [stale, setStale] = useState(false);
  const [attachmentError, setAttachmentError] = useState('');
  const request = useRef<{ sequence: number; controller: AbortController | null; busy: boolean }>({ sequence: 0, controller: null, busy: false });
  const input = useRef<HTMLInputElement>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const inputId = useId();
  const uploadId = useRef('');

  function checked<T extends WorkspaceFileIdentity>(value: T): T {
    if (value.workspaceId !== identity.workspaceId || value.serviceInstanceId !== identity.serviceInstanceId) {
      throw new ApiError(t('library.workspaceChanged'), 'workspace_changed', 409);
    }
    return value;
  }

  async function perform(work: (signal: AbortSignal, current: () => boolean) => Promise<void>, uploading = false, attaching = false) {
    if (request.current.busy || stale) return;
    const sequence = ++request.current.sequence;
    const controller = new AbortController();
    request.current.controller = controller;
    request.current.busy = true;
    setBusy(true); setError(''); setNotice(''); setAttachmentError('');
    const current = () => request.current.sequence === sequence && !controller.signal.aborted;
    try {
      await work(controller.signal, current);
    } catch (problem) {
      if (!current()) return;
      if (problem instanceof ApiError && problem.code === 'workspace_changed') {
        setStale(true); setPreview(null); setError(t('library.workspaceChanged'));
      } else {
        const key = problem instanceof ApiError ? materialErrors[problem.code] : undefined;
        const message = uploading && problem instanceof ApiError && problem.code === 'network_error'
          ? t('library.uploadUnconfirmed') : key ? t(key) : errorText(problem);
        if (attaching) setAttachmentError(message); else setError(message);
      }
    } finally {
      if (current()) { request.current.busy = false; setBusy(false); }
    }
  }

  function browse(path: string) {
    void perform(async (signal, current) => {
      const result = checked(await api.files(path, identity, signal));
      if (current()) { setListing(result); setPreview(null); }
    });
  }

  function openFile(path: string) {
    // Choosing an existing file replaces any pending upload selection, including
    // after an upload whose response was lost but whose bytes were saved.
    setSelected(null);
    if (input.current) input.current.value = '';
    setPreview(null);
    void perform(async (signal, current) => {
      const result = checked(await api.file(path, identity, signal));
      if (current()) setPreview(result);
    });
  }

  function upload() {
    if (!selected || busy || stale) return;
    if (!selected.size) { setError(t('library.emptyFile')); return; }
    if (selected.size > MAX_BYTES) { setError(t('library.tooLarge')); return; }
    const file = selected;
    setPreview(null);
    void perform(async (signal, current) => {
      const result = checked(await api.uploadMaterial(pageId, identity, uploadId.current, file, signal));
      if (!current()) return;
      setNotice(t('library.uploaded', { name: file.name }));
      setSelected(null);
      if (input.current) input.current.value = '';
      const [files, content] = await Promise.all([
        api.files('uploads', identity, signal).then(checked),
        api.file(result.material.path, identity, signal).then(checked),
      ]);
      if (!current()) return;
      if (content.sha256 !== result.material.sha256) throw new ApiError(t('library.changed'), 'file_changed_retry', 409);
      setListing(files); setPreview(content);
    }, true);
  }

  function attach() {
    if (!preview || busy || stale) return;
    if (selectedIds.length >= 5) { setAttachmentError(t('materials.selectionFull')); return; }
    void perform(async (signal, current) => {
      const result = checked(await api.referenceMaterial(pageId, preview, identity, signal));
      if (current() && !add(result.material)) setAttachmentError(t('materials.selectionFull'));
    }, false, true);
  }

  useEffect(() => {
    browse('');
    return () => {
      ++request.current.sequence;
      request.current.controller?.abort();
      request.current.busy = false;
    };
    // This dialog is keyed to World, service instance and page by its owner.
  }, []);

  useEffect(() => {
    // Opening a source should take keyboard and phone users to its content,
    // rather than leaving a successful preview below the upload controls.
    if (preview) {
      setAttachmentError('');
      previewHeading.current?.focus({ preventScroll: true });
      previewHeading.current?.scrollIntoView({ block: 'start' });
    }
  }, [preview]);

  const disabled = busy || stale;
  const directory = listing?.path ?? '';
  return <Dialog title={t('library.title')} onClose={close} className="materials-dialog">
    <div className="material-body">
    <p className="dialog-description">{t('library.description')}</p>
    <form className="material-upload" onSubmit={event => { event.preventDefault(); upload(); }}>
      <label className="field-label" htmlFor={inputId}>{t('library.choose')}</label>
      <p id={`${inputId}-hint`} className="material-hint">{t('library.hint')}</p>
      <input ref={input} id={inputId} type="file" accept="text/*,.txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.log,.py,.ts,.js,.html,.css" disabled={disabled}
        aria-describedby={`${inputId}-hint${error ? ` ${inputId}-error` : ''}`}
        onChange={event => { setSelected(event.target.files?.[0] ?? null); uploadId.current = crypto.randomUUID().replaceAll('-', ''); setPreview(null); setError(''); setNotice(''); }} />
      {selected ? <p className="material-selected">{selected.name} <span>({selected.size.toLocaleString()} {t('library.bytes')})</span></p> : null}
      <button className="secondary-button" type="submit" disabled={disabled || !selected}>{t('library.upload')}</button>
    </form>
    {error ? <p className="form-error" id={`${inputId}-error`} role="alert">{error}</p> : null}
    {stale ? <button className="secondary-button" onClick={() => window.location.reload()}>{t('library.reload')}</button> : null}
    <p className="material-notice" role="status" aria-live="polite">{busy ? t('library.working') : notice}</p>
    <section className="material-browser" aria-label={t('library.existing')}>
      <div className="material-browser-heading"><h3>{t('library.existing')}</h3><button className="text-button" disabled={disabled} onClick={() => browse(directory)}>{t('library.refresh')}</button></div>
      <div className="material-location"><button className="text-button" disabled={disabled || !directory} onClick={() => browse('')}>{t('library.workspace')}</button>{directory ? <><span aria-hidden="true">/</span><span>{directory}</span><button className="text-button" disabled={disabled} onClick={() => browse(directory.split('/').slice(0, -1).join('/'))}>{t('library.up')}</button></> : null}</div>
      {listing?.entries.length ? <ul className="material-list">{listing.entries.map(entry => <li key={entry.path}>
        <button disabled={disabled} className={preview?.path === entry.path ? 'selected' : ''} onClick={() => entry.kind === 'directory' ? browse(entry.path) : openFile(entry.path)}>
          <Icon name={entry.kind === 'directory' ? 'chevron' : 'page'} size={16} /><span>{entry.name}{entry.kind === 'directory' ? '/' : ''}</span>
          {entry.bytes !== null ? <small>{entry.bytes.toLocaleString()} {t('library.bytes')}</small> : null}
        </button>
      </li>)}</ul> : listing ? <p className="material-hint">{t('library.empty')}</p> : null}
      {listing?.truncated ? <p className="material-hint">{t('library.listLimited')}</p> : null}
    </section>
    {preview ? <section className="material-preview" aria-label={t('library.preview')}>
      <h3 ref={previewHeading} tabIndex={-1}>{preview.path.split('/').at(-1)}</h3>
      <TextDocument key={`${preview.path}/${preview.sha256}`} path={preview.path} content={preview.content.slice(0, PREVIEW_CHARS)} truncated={preview.content.length > PREVIEW_CHARS} />
      {preview.content.length > PREVIEW_CHARS ? <p className="material-hint">{t('library.previewLimited')}</p> : null}
      <details><summary>{t('library.details')}</summary><p>{preview.path}</p><p>SHA-256: {preview.sha256}</p></details>
    </section> : null}
    </div>
    {preview ? <div className="material-footer">
      <p className="material-hint">{t('library.draftHint')}</p>
      <button className="primary-button" disabled={disabled || Boolean(selected)} onClick={attach}>{t('library.add')}</button>
      {attachmentError ? <p className="form-error" role="alert">{attachmentError}</p> : null}
    </div> : null}
  </Dialog>;
}
