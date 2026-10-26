import { useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage, type Translate } from '../i18n';
import type { Artifact, ArtifactRead, PageView } from '../types';

function unavailableReason(reason: string | null, t: Translate): string {
  if (reason === 'file_too_large') return t('artifacts.tooLarge');
  if (reason === 'text_files_only') return t('artifacts.nonText');
  if (reason === 'credential_content_blocked') return t('artifacts.sensitive');
  if (reason === 'file_changed_retry') return t('artifacts.retry');
  return t('artifacts.missing');
}

export function PageArtifacts({ view }: { view: PageView }) {
  const { t } = useLanguage();
  const [opened, setOpened] = useState<string | null>(null);
  const [result, setResult] = useState<ArtifactRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(0);
  if (!view.artifacts?.length && !view.artifactsTruncated) return null;

  const read = async (artifact: Artifact) => {
    const current = ++requestId.current;
    if (opened === artifact.id) { setOpened(null); setResult(null); setLoading(false); return; }
    setOpened(artifact.id); setResult(null); setLoading(true); setError('');
    try {
      const next = await api.artifact(view.page.id, artifact.id);
      if (current === requestId.current) setResult(next);
    } catch (problem) {
      if (current === requestId.current) setError(problem instanceof Error ? problem.message : t('artifacts.error'));
    } finally { if (current === requestId.current) setLoading(false); }
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
        {error ? <p role="alert">{error}</p> : null}
        {result?.state === 'current' ? <>
          <pre>{result.content}</pre>
          {result.truncated ? <p>{t('artifacts.limited')}</p> : null}
          {result.redacted ? <p>{t('artifacts.redacted')}</p> : null}
          <p className="artifact-provenance">{t('artifacts.provenance', { time: new Date(artifact.createdAt).toLocaleString() })}</p>
        </> : result?.state === 'changed' ? <p role="status">{t('artifacts.changed')}</p> : result ? <p role="status">{unavailableReason(result.reason, t)}</p> : null}
      </div> : null}
    </li>)}</ul>
    {view.artifactsTruncated ? <p className="artifact-intro">{t('artifacts.more')}</p> : null}
  </section>;
}
