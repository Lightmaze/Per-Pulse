import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { ArtifactRead, Message } from '../types';
import { SessionLink, type Navigate } from './SessionLink';
import { TextDocument } from './TextDocument';

type ReportedArtifactSource = NonNullable<Message['reportedArtifact']>;

/** Read an actual Work file only through its original scoped Kernel receipt. */
export function ReportedArtifact({ source, homeMessageId, navigate }: {
  source: ReportedArtifactSource; homeMessageId: string; navigate: Navigate;
}) {
  const { t, errorText } = useLanguage();
  const [opened, setOpened] = useState(false);
  const [loading, setLoading] = useState(false);
  const [read, setRead] = useState<ArtifactRead | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [downloadState, setDownloadState] = useState<'ready' | 'loading' | 'started'>('ready');
  const downloadRequest = useRef<AbortController | null>(null);
  const request = useRef(0);
  useEffect(() => () => { request.current++; downloadRequest.current?.abort(); }, []);

  const open = async () => {
    downloadRequest.current?.abort(); downloadRequest.current = null;
    setDownloadState('ready');
    if (opened) { request.current++; setOpened(false); setRead(null); setLoading(false); return; }
    const id = ++request.current;
    setOpened(true); setLoading(true); setRead(null); setError(null);
    try {
      const value = await api.artifact(source.pageId, source.artifactId);
      if (value.pageId !== source.pageId || value.artifact.id !== source.artifactId
          || value.artifact.sha256 !== source.sha256 || value.artifact.path !== source.path) {
        throw new Error('The recorded file result no longer matches its original Work.');
      }
      if (request.current === id) setRead(value);
    } catch (problem) {
      if (request.current === id) setError(problem);
    } finally {
      if (request.current === id) setLoading(false);
    }
  };

  const download = async () => {
    // The preview belongs to the exact producing Work. Download rechecks that
    // same original action and digest in the native file endpoint.
    if (downloadRequest.current || read?.state !== 'current' || !read.downloadable) return;
    const controller = new AbortController();
    downloadRequest.current = controller;
    setDownloadState('loading'); setError(null);
    try {
      const file = await api.downloadArtifact(source.pageId, read.artifact, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(file.blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = file.filename;
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
      setDownloadState('started');
    } catch (problem) {
      if (!controller.signal.aborted) {
        // The file might have changed since the preview was opened. Do not
        // leave the previous bytes presented as an available deliverable.
        setRead(null); setError(problem); setDownloadState('ready');
      }
    } finally {
      if (downloadRequest.current === controller) downloadRequest.current = null;
    }
  };

  return <section className="artifact-preview reported-artifact" aria-label={t('artifacts.reported')}>
    <p><strong>{t('artifacts.reported')}</strong> · <code>{source.path}</code> · {t('artifacts.version', { digest: source.sha256.slice(0, 8) })}</p>
    <div className="artifact-actions">
      <button type="button" className="secondary-button" aria-expanded={opened} onClick={() => void open()}>
        {t(opened ? 'artifacts.closeReported' : 'artifacts.openReported')}
      </button>
      <SessionLink pageId={source.pageId} label={t('artifacts.sourceWork')} origin={{ pageId: 'home', messageId: homeMessageId }} navigate={navigate} />
    </div>
    {opened ? <>
      {loading ? <p role="status">{t('artifacts.reading')}</p> : null}
      {error ? <p role="alert">{errorText(error)}</p> : null}
      {read?.state === 'current' ? <>
        <TextDocument path={source.path} content={read.content ?? ''} truncated={read.truncated} />
        {read.truncated ? <p>{t('artifacts.limited')}</p> : null}
        {read.downloadable ? <div className="artifact-actions">
          <button type="button" className="secondary-button" disabled={downloadState === 'loading'}
            onClick={() => void download()}>{t(downloadState === 'loading' ? 'artifacts.downloading' : 'artifacts.download')}</button>
          {downloadState === 'started' ? <span role="status">{t('artifacts.downloadStarted')}</span> : null}
        </div> : null}
      </> : read?.state === 'changed' ? <p role="status">{t('artifacts.changed')}</p>
        : read ? <p role="status">{read.reason === 'artifact_undone' ? t('artifacts.undone') : t('artifacts.missing')}</p> : null}
    </> : null}
  </section>;
}
