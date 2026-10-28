import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage, type Translate } from '../i18n';
import type { Artifact, ArtifactRead, PageView } from '../types';
import { TextDocument } from './TextDocument';

function unavailableReason(reason: string | null, t: Translate): string {
  if (reason === 'file_too_large') return t('artifacts.tooLarge');
  if (reason === 'text_files_only') return t('artifacts.nonText');
  if (reason === 'credential_content_blocked') return t('artifacts.sensitive');
  if (reason === 'file_changed_retry') return t('artifacts.retry');
  return t('artifacts.missing');
}

export function PageArtifacts({ view }: { view: PageView }) {
  const { t, errorText } = useLanguage();
  const [opened, setOpened] = useState<string | null>(null);
  const [result, setResult] = useState<ArtifactRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [downloadState, setDownloadState] = useState<'ready' | 'loading' | 'started'>('ready');
  const downloadRequest = useRef<AbortController | null>(null);
  const requestId = useRef(0);
  useEffect(() => () => { requestId.current++; downloadRequest.current?.abort(); }, []);
  if (!view.artifacts?.length && !view.artifactsTruncated) return null;

  const read = async (artifact: Artifact, refresh = false) => {
    const current = ++requestId.current;
    downloadRequest.current?.abort(); downloadRequest.current = null;
    setDownloadState('ready');
    if (opened === artifact.id && !refresh) { setOpened(null); setResult(null); setLoading(false); return; }
    setOpened(artifact.id); setResult(null); setLoading(true); setError('');
    try {
      const next = await api.artifact(view.page.id, artifact.id);
      if (current === requestId.current) setResult(next);
    } catch (problem) {
      if (current === requestId.current) setError(problem);
    } finally { if (current === requestId.current) setLoading(false); }
  };

  const download = async (artifact: Artifact) => {
    if (downloadRequest.current) return;
    const controller = new AbortController();
    downloadRequest.current = controller; setDownloadState('loading'); setError(null);
    try {
      const file = await api.downloadArtifact(view.page.id, artifact, controller.signal);
      // Leaving this page, closing the preview or opening another result
      // cancels a pending download before any browser-side file action.
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(file.blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = file.filename;
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setDownloadState('started');
    } catch (problem) {
      if (!controller.signal.aborted) { setError(problem); setDownloadState('ready'); }
    } finally { if (downloadRequest.current === controller) downloadRequest.current = null; }
  };

  return <section className="page-artifacts" aria-label={t('artifacts.title')}>
    <h2>{t('artifacts.title')}</h2>
    <p className="artifact-intro">{t('artifacts.intro')}</p>
    <ul>{view.artifacts.map(artifact => <li key={artifact.id}>
      <button className="artifact-open" aria-expanded={opened === artifact.id} onClick={() => read(artifact)}>
        <span>{artifact.path}</span><small>{t('artifacts.version', { digest: artifact.sha256.slice(0, 8) })}</small>
      </button>
      {opened === artifact.id ? <div className="artifact-preview">
        {loading ? <p role="status">{t('artifacts.reading')}</p> : null}
        {error ? <p role="alert">{errorText(error)}</p> : null}
        {result?.state === 'current' ? <>
          <TextDocument key={artifact.id} path={artifact.path} content={result.content ?? ''} />
          {result.truncated ? <p>{t('artifacts.limited')}</p> : null}
          {result.redacted ? <p>{t('artifacts.redacted')}</p> : null}
          <p className="artifact-provenance">{t('artifacts.provenance', { time: new Date(artifact.createdAt).toLocaleString() })}</p>
          {!result.redacted ? <div className="artifact-actions">
            <button type="button" className="secondary-button" disabled={downloadState === 'loading'} onClick={() => void download(artifact)}>{t(downloadState === 'loading' ? 'artifacts.downloading' : 'artifacts.download')}</button>
            {downloadState === 'started' ? <span role="status">{t('artifacts.downloadStarted')}</span> : null}
          </div> : null}
        </> : result?.state === 'changed' ? <p role="status">{t('artifacts.changed')}</p> : result ? <p role="status">{unavailableReason(result.reason, t)}</p> : null}
        {!loading ? <button type="button" className="text-button" onClick={() => void read(artifact, true)}>{t('artifacts.refresh')}</button> : null}
      </div> : null}
    </li>)}</ul>
    {view.artifactsTruncated ? <p className="artifact-intro">{t('artifacts.more')}</p> : null}
  </section>;
}
