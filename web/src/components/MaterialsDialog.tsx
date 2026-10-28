import { useEffect, useId, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { useLanguage, type MessageKey } from '../i18n';
import type { WorkspaceFile, WorkspaceFileIdentity, WorkspaceFileList } from '../types';
import { Dialog } from './Dialog';
import { Icon } from './Icon';
import { TextDocument } from './TextDocument';

const MAX_BYTES = 1024 * 1024;
const PREVIEW_CHARS = 64_000;
// The same native file errors also serve result downloads. Keep upload advice
// in this flow rather than replacing their explanations across the product.
const materialErrors: Record<string, MessageKey> = {
  workspace_changed: 'materials.workspaceChanged', file_too_large: 'materials.tooLarge',
  empty_file: 'materials.emptyFile', text_files_only: 'materials.textOnly',
  credential_content_blocked: 'materials.credentials', protected_or_invalid_path: 'materials.invalidPath',
  invalid_path: 'materials.invalidPath', filename_only: 'materials.invalidPath',
  file_changed_retry: 'materials.changed', file_unavailable: 'materials.unavailable',
  path_unavailable: 'materials.unavailable', not_a_single_regular_file: 'materials.invalidPath',
  upload_failed_or_unconfirmed: 'materials.uploadUnconfirmed',
};

export function MaterialsDialog({ identity, close, add }: {
  identity: WorkspaceFileIdentity; close: () => void; add: (file: WorkspaceFile) => boolean;
}) {
  const { t, errorText } = useLanguage();
  const [listing, setListing] = useState<WorkspaceFileList | null>(null);
  const [preview, setPreview] = useState<WorkspaceFile | null>(null);
  const [selected, setSelected] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [stale, setStale] = useState(false);
  const [draftFull, setDraftFull] = useState(false);
  const request = useRef<{ sequence: number; controller: AbortController | null; busy: boolean }>({ sequence: 0, controller: null, busy: false });
  const input = useRef<HTMLInputElement>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const inputId = useId();

  function checked<T extends WorkspaceFileIdentity>(value: T): T {
    if (value.workspaceId !== identity.workspaceId || value.serviceInstanceId !== identity.serviceInstanceId) {
      throw new ApiError(t('materials.workspaceChanged'), 'workspace_changed', 409);
    }
    return value;
  }

  async function perform(work: (signal: AbortSignal, current: () => boolean) => Promise<void>, uploading = false) {
    if (request.current.busy || stale) return;
    const sequence = ++request.current.sequence;
    const controller = new AbortController();
    request.current.controller = controller;
    request.current.busy = true;
    setBusy(true); setError(''); setNotice('');
    const current = () => request.current.sequence === sequence && !controller.signal.aborted;
    try {
      await work(controller.signal, current);
    } catch (problem) {
      if (!current()) return;
      if (problem instanceof ApiError && problem.code === 'workspace_changed') {
        setStale(true); setPreview(null); setError(t('materials.workspaceChanged'));
      } else {
        const key = problem instanceof ApiError ? materialErrors[problem.code] : undefined;
        setError(uploading && problem instanceof ApiError && problem.code === 'network_error'
          ? t('materials.uploadUnconfirmed') : key ? t(key) : errorText(problem));
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
    setPreview(null);
    void perform(async (signal, current) => {
      const result = checked(await api.file(path, identity, signal));
      if (current()) setPreview(result);
    });
  }

  function upload() {
    if (!selected || busy || stale) return;
    if (!selected.size) { setError(t('materials.emptyFile')); return; }
    if (selected.size > MAX_BYTES) { setError(t('materials.tooLarge')); return; }
    const file = selected;
    setPreview(null);
    void perform(async (signal, current) => {
      const result = checked(await api.upload(file, identity, signal));
      if (!current()) return;
      setNotice(t('materials.uploaded', { name: file.name }));
      setSelected(null);
      if (input.current) input.current.value = '';
      const [files, content] = await Promise.all([
        api.files('uploads', identity, signal).then(checked),
        api.file(result.path, identity, signal).then(checked),
      ]);
      if (!current()) return;
      setListing(files); setPreview(content);
    }, true);
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
      setDraftFull(false);
      previewHeading.current?.focus({ preventScroll: true });
      previewHeading.current?.scrollIntoView({ block: 'start' });
    }
  }, [preview]);

  const disabled = busy || stale;
  const directory = listing?.path ?? '';
  return <Dialog title={t('materials.title')} onClose={close} className="materials-dialog">
    <div className="material-body">
    <p className="dialog-description">{t('materials.description')}</p>
    <form className="material-upload" onSubmit={event => { event.preventDefault(); upload(); }}>
      <label className="field-label" htmlFor={inputId}>{t('materials.choose')}</label>
      <p id={`${inputId}-hint`} className="material-hint">{t('materials.hint')}</p>
      <input ref={input} id={inputId} type="file" accept="text/*,.txt,.md,.markdown,.csv,.tsv,.json,.yaml,.yml,.log,.py,.ts,.js,.html,.css" disabled={disabled}
        aria-describedby={`${inputId}-hint${error ? ` ${inputId}-error` : ''}`}
        onChange={event => { setSelected(event.target.files?.[0] ?? null); setPreview(null); setError(''); setNotice(''); }} />
      {selected ? <p className="material-selected">{selected.name} <span>({selected.size.toLocaleString()} {t('materials.bytes')})</span></p> : null}
      <button className="secondary-button" type="submit" disabled={disabled || !selected}>{t('materials.upload')}</button>
    </form>
    {error ? <p className="form-error" id={`${inputId}-error`} role="alert">{error}</p> : null}
    {stale ? <button className="secondary-button" onClick={() => window.location.reload()}>{t('materials.reload')}</button> : null}
    <p className="material-notice" role="status" aria-live="polite">{busy ? t('materials.working') : notice}</p>
    <section className="material-browser" aria-label={t('materials.existing')}>
      <div className="material-browser-heading"><h3>{t('materials.existing')}</h3><button className="text-button" disabled={disabled} onClick={() => browse(directory)}>{t('materials.refresh')}</button></div>
      <div className="material-location"><button className="text-button" disabled={disabled || !directory} onClick={() => browse('')}>{t('materials.workspace')}</button>{directory ? <><span aria-hidden="true">/</span><span>{directory}</span><button className="text-button" disabled={disabled} onClick={() => browse(directory.split('/').slice(0, -1).join('/'))}>{t('materials.up')}</button></> : null}</div>
      {listing?.entries.length ? <ul className="material-list">{listing.entries.map(entry => <li key={entry.path}>
        <button disabled={disabled} className={preview?.path === entry.path ? 'selected' : ''} onClick={() => entry.kind === 'directory' ? browse(entry.path) : openFile(entry.path)}>
          <Icon name={entry.kind === 'directory' ? 'chevron' : 'page'} size={16} /><span>{entry.name}{entry.kind === 'directory' ? '/' : ''}</span>
          {entry.bytes !== null ? <small>{entry.bytes.toLocaleString()} {t('materials.bytes')}</small> : null}
        </button>
      </li>)}</ul> : listing ? <p className="material-hint">{t('materials.empty')}</p> : null}
      {listing?.truncated ? <p className="material-hint">{t('materials.listLimited')}</p> : null}
    </section>
    {preview ? <section className="material-preview" aria-label={t('materials.preview')}>
      <h3 ref={previewHeading} tabIndex={-1}>{preview.path.split('/').at(-1)}</h3>
      <TextDocument key={`${preview.path}/${preview.sha256}`} path={preview.path} content={preview.content.slice(0, PREVIEW_CHARS)} truncated={preview.content.length > PREVIEW_CHARS} />
      {preview.content.length > PREVIEW_CHARS ? <p className="material-hint">{t('materials.previewLimited')}</p> : null}
      <details><summary>{t('materials.details')}</summary><p>{preview.path}</p><p>SHA-256: {preview.sha256}</p></details>
    </section> : null}
    </div>
    {preview ? <div className="material-footer">
      <p className="material-hint">{t('materials.draftHint')}</p>
      <button className="primary-button" disabled={disabled || Boolean(selected)} onClick={() => { if (!add(preview)) setDraftFull(true); }}>{t('materials.add')}</button>
      {draftFull ? <p className="form-error" role="alert">{t('materials.draftFull')}</p> : null}
    </div> : null}
  </Dialog>;
}
