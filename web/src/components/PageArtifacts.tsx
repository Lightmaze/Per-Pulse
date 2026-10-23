import { useRef, useState } from 'react';
import { api } from '../api';
import type { Artifact, ArtifactRead, PageView } from '../types';

function unavailableReason(reason: string | null): string {
  if (reason === 'file_too_large') return 'Preview supports text files up to 1 MiB.';
  if (reason === 'text_files_only') return 'This file is not available as a text preview.';
  if (reason === 'credential_content_blocked') return 'This file contains sensitive values and cannot be previewed.';
  if (reason === 'file_changed_retry') return 'This file changed while it was being read. Open it again to check this version.';
  return 'This file is missing or cannot be safely previewed.';
}

export function PageArtifacts({ view }: { view: PageView }) {
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
      if (current === requestId.current) setError(problem instanceof Error ? problem.message : 'This file could not be read.');
    } finally { if (current === requestId.current) setLoading(false); }
  };

  return <section className="page-artifacts" aria-label="Files from this page">
    <h2>Files from this page</h2>
    <p className="artifact-intro">Results of completed file actions, linked to the version this page wrote.</p>
    <ul>{view.artifacts.map(artifact => <li key={artifact.id}>
      <button className="artifact-open" aria-expanded={opened === artifact.id} onClick={() => read(artifact)}>
        <span>{artifact.path}</span><small>Version {artifact.sha256.slice(0, 8)}</small>
      </button>
      {opened === artifact.id ? <div className="artifact-preview">
        {loading ? <p role="status">Reading this version…</p> : null}
        {error ? <p role="alert">{error}</p> : null}
        {result?.state === 'current' ? <>
          <pre>{result.content}</pre>
          {result.truncated ? <p>Showing a limited preview.</p> : null}
          {result.redacted ? <p>Sensitive values have been hidden.</p> : null}
          <p className="artifact-provenance">Written in this page · {new Date(artifact.createdAt).toLocaleString()} · Version verified when opened</p>
        </> : result?.state === 'changed' ? <p role="status">This file has changed since this page wrote it. Its current contents are not the recorded version.</p> : result ? <p role="status">{unavailableReason(result.reason)}</p> : null}
      </div> : null}
    </li>)}</ul>
    {view.artifactsTruncated ? <p className="artifact-intro">Some file results are unavailable in this view.</p> : null}
  </section>;
}
