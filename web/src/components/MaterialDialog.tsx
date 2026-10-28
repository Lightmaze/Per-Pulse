// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { useLanguage } from '../i18n';
import type { Material, MaterialList, MaterialRead } from '../types';
import { Dialog } from './Dialog';

export function MaterialDialog({ pageId, workspaceId, initialId, selectedIds, attach, close, readOnly = false }: {
  pageId: string; workspaceId: string; initialId?: string; selectedIds: string[];
  attach: (material: Material) => void; close: () => void;
  readOnly?: boolean;
}) {
  const { t, locale, errorText } = useLanguage();
  const [listing, setListing] = useState<MaterialList | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [previewId, setPreviewId] = useState(initialId ?? '');
  const [preview, setPreview] = useState<MaterialRead | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const mounted = useRef(false);
  const writing = useRef(false);
  const uploadId = useRef('');
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    api.materials(pageId, controller.signal).then(value => {
      if (!controller.signal.aborted) setListing(value);
    }).catch(problem => { if (!controller.signal.aborted) setError(problem); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [pageId, version]);
  useEffect(() => {
    if (!previewId) { setPreview(null); setPreviewError(null); return; }
    const controller = new AbortController();
    setPreview(null); setPreviewError(null); setReading(true);
    api.material(pageId, previewId, controller.signal).then(value => {
      if (!controller.signal.aborted) setPreview(value);
    }).catch(problem => { if (!controller.signal.aborted) setPreviewError(problem); })
      .finally(() => { if (!controller.signal.aborted) setReading(false); });
    return () => controller.abort();
  }, [pageId, previewId, version]);

  const full = selectedIds.length >= 5;
  const size = (bytes: number) => t('materials.bytes', { count: new Intl.NumberFormat(locale).format(bytes) });
  const upload = async () => {
    if (!file || file.size > 1024 * 1024 || full || writing.current) return;
    writing.current = true; setBusy(true); setError(null);
    try {
      const result = await api.uploadMaterial(pageId, workspaceId, uploadId.current, file);
      if (mounted.current) attach(result.material);
    } catch (problem) {
      if (mounted.current) {
        setError(problem instanceof ApiError && ['network_error', 'invalid_response', 'request_failed'].includes(problem.code)
          ? new ApiError('The upload was not confirmed. Refresh source files before trying again.', 'material_upload_unconfirmed', problem.status) : problem);
        setVersion(value => value + 1);
      }
    } finally {
      writing.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return <Dialog title={t('materials.title')} className="material-dialog" onClose={() => { if (!writing.current) close(); }}>
    <p className="dialog-description">{t(readOnly ? 'materials.sourcePreview' : 'materials.description')}</p>
    <p className="lesson-note">{t('materials.sharedWorkspace')}</p>
    {!readOnly && full ? <p role="status">{t('materials.selectionFull')}</p> : null}
    {!readOnly ? <div className="material-upload">
      <label className="field-label" htmlFor="material-file">{t('materials.choose')}</label>
      <input id="material-file" type="file" disabled={busy || full} onChange={event => {
        setFile(event.target.files?.[0] ?? null); uploadId.current = crypto.randomUUID().replaceAll('-', ''); setError(null);
      }} />
      <p className="lesson-note">{t('materials.fileTypes')}</p>
      {file ? <p>{file.name} · {size(file.size)}</p> : null}
      {file && file.size > 1024 * 1024 ? <p className="form-error">{t('materials.tooLarge')}</p> : null}
      <button className="primary-button" disabled={busy || full || !file || file.size > 1024 * 1024} onClick={() => void upload()}>{t(busy ? 'materials.uploading' : 'materials.uploadAttach')}</button>
    </div> : null}
    {error ? <p className="form-error" role="alert">{errorText(error)}</p> : null}
    <div className="material-list-heading"><h3>{t('materials.saved')}</h3><button className="text-button" disabled={loading || busy} onClick={() => { setError(null); setVersion(value => value + 1); }}>{t('materials.refresh')}</button></div>
    {loading ? <p role="status">{t('materials.loading')}</p> : null}
    {listing && !listing.materials.length ? <p className="lesson-note">{t('materials.empty')}</p> : null}
    <ul className="material-list">{listing?.materials.map(material => <li key={material.id}>
      <button className="text-button material-name" onClick={() => setPreviewId(material.id)}>{material.name}</button>
      <span>{size(material.bytes)}</span>
      {selectedIds.includes(material.id) ? <span>{t('materials.inMessage')}</span> : null}
    </li>)}</ul>
    {listing?.truncated ? <p className="lesson-note">{t('materials.recent')}</p> : null}
    {previewId ? <section className="material-preview" aria-label={t('materials.preview')} aria-busy={reading}>
      {reading ? <p role="status">{t('materials.loading')}</p> : null}
      {previewError ? <p className="form-error" role="alert">{errorText(previewError)}</p> : null}
      {preview ? <>
        <h3>{preview.material.name}</h3>
        <p className="lesson-note">{size(preview.material.bytes)}</p>
        {preview.redacted ? <p>{t('materials.redacted')}</p> : null}
        {preview.truncated ? <p>{t('materials.truncated')}</p> : null}
        <pre tabIndex={0} aria-label={t('materials.content')}><code>{preview.content}</code></pre>
        {!readOnly ? <button className="primary-button" disabled={busy || full || selectedIds.includes(preview.material.id)} onClick={() => attach(preview.material)}>{t(selectedIds.includes(preview.material.id) ? 'materials.inMessage' : 'materials.attach')}</button> : null}
      </> : null}
    </section> : null}
  </Dialog>;
}
