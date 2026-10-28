// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { useLanguage } from '../i18n';
import type { MaterialRead, WorkspaceFileIdentity } from '../types';
import { Dialog } from './Dialog';
import { TextDocument } from './TextDocument';

/** Inspect exactly the version identified by a native message attachment. */
export function MaterialDialog({ identity, pageId, materialId, close, browse }: {
  identity: WorkspaceFileIdentity; pageId: string; materialId: string; close: () => void; browse: () => void;
}) {
  const { t, locale, errorText } = useLanguage();
  const [preview, setPreview] = useState<MaterialRead | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reading, setReading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [downloadStarted, setDownloadStarted] = useState(false);
  const downloadRequest = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setPreview(null); setError(null); setReading(true);
    api.material(pageId, materialId, controller.signal).then(value => {
      if (!controller.signal.aborted) setPreview(value);
    }).catch(problem => { if (!controller.signal.aborted) setError(problem); })
      .finally(() => { if (!controller.signal.aborted) setReading(false); });
    return () => { controller.abort(); downloadRequest.current?.abort(); };
  }, [pageId, materialId]);
  useEffect(() => { if (preview) heading.current?.focus({ preventScroll: true }); }, [preview]);
  async function download() {
    if (!preview?.downloadable || downloadRequest.current) return;
    const controller = new AbortController();
    downloadRequest.current = controller;
    setDownloading(true); setDownloadStarted(false); setError(null);
    try {
      const file = await api.downloadMaterial(pageId, preview.material, identity, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(file.blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = file.filename;
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setDownloadStarted(true);
    } catch (problem) {
      if (!controller.signal.aborted) setError(problem);
    } finally {
      if (!controller.signal.aborted) setDownloading(false);
      if (downloadRequest.current === controller) downloadRequest.current = null;
    }
  }
  return <Dialog title={t('materials.title')} className="materials-dialog material-source-dialog" onClose={close}>
    <div className="material-body">
      <p className="dialog-description">{t('materials.sourcePreview')}</p>
      {error ? <p className="form-error" role="alert">{error instanceof ApiError && error.code === 'workspace_changed' ? t('library.workspaceChanged') : errorText(error)}</p> : null}
      <section className="material-preview" aria-label={t('materials.preview')} aria-busy={reading}>
        {reading ? <p role="status">{t('materials.loading')}</p> : null}
        {preview ? <>
          <h3 ref={heading} tabIndex={-1}>{preview.material.name}</h3>
          <p className="material-hint">{t('materials.bytes', { count: new Intl.NumberFormat(locale).format(preview.material.bytes) })}</p>
          {preview.redacted ? <p>{t('materials.redacted')}</p> : null}
          {preview.truncated ? <p>{t('materials.truncated')}</p> : null}
          <div aria-label={t('materials.content')}><TextDocument key={`${preview.material.id}/${preview.material.sha256}`} path={preview.material.path} content={preview.content} truncated={preview.truncated} /></div>
          <details><summary>{t('library.details')}</summary><p>{preview.material.path}</p><p>SHA-256: {preview.material.sha256}</p></details>
        </> : null}
      </section>
    </div>
    <div className="material-source-actions">
      {preview?.downloadable ? <button className="secondary-button" disabled={downloading} onClick={() => void download()}>{t(downloading ? 'artifacts.downloading' : 'library.downloadOriginal')}</button> : null}
      <button className="text-button" onClick={browse}>{t('library.browse')}</button>
      {downloadStarted ? <span role="status">{t('artifacts.downloadStarted')}</span> : null}
    </div>
  </Dialog>;
}
