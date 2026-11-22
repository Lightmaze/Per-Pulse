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
  const request = useRef(0);
  useEffect(() => () => { request.current++; }, []);

  const open = async () => {
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
      </> : read?.state === 'changed' ? <p role="status">{t('artifacts.changed')}</p>
        : read ? <p role="status">{read.reason === 'artifact_undone' ? t('artifacts.undone') : t('artifacts.missing')}</p> : null}
    </> : null}
  </section>;
}
