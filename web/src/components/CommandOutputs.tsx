import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { useLanguage } from '../i18n';
import type { CommandOutput, CommandOutputRead, CommandOutputStream, PageView } from '../types';

type Selection = { output: CommandOutput; stream: CommandOutputStream; offset: number; previous: number[] };

export function CommandOutputs({ view }: { view: PageView }) {
  const { t, errorText } = useLanguage();
  const [selection, setSelection] = useState<Selection | null>(null);
  const [passage, setPassage] = useState<CommandOutputRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [downloadState, setDownloadState] = useState<'ready' | 'loading' | 'started'>('ready');
  const readRequest = useRef<AbortController | null>(null);
  const downloadRequest = useRef<AbortController | null>(null);
  const serial = useRef(0);
  useEffect(() => () => {
    serial.current++;
    readRequest.current?.abort();
    downloadRequest.current?.abort();
  }, []);

  const outputs = view.execution?.commandOutputs;
  if (!outputs?.items.length && !outputs?.truncated) return null;

  const close = () => {
    serial.current++;
    readRequest.current?.abort(); downloadRequest.current?.abort();
    readRequest.current = null; downloadRequest.current = null;
    setSelection(null); setPassage(null); setError(null); setLoading(false); setDownloadState('ready');
  };

  const read = async (next: Selection) => {
    const current = ++serial.current;
    readRequest.current?.abort(); downloadRequest.current?.abort(); downloadRequest.current = null;
    const controller = new AbortController(); readRequest.current = controller;
    setSelection(next); setPassage(null); setLoading(true); setError(null); setDownloadState('ready');
    try {
      const result = await api.commandOutput(view.page.id, next.output.actionId, next.stream, next.offset, controller.signal);
      const expected = next.output.streams[next.stream];
      if (!expected || result.kind !== 'pulse.command-output.v1' || result.action_id !== next.output.actionId
        || result.stream !== next.stream || result.sha256 !== expected.sha256 || result.offset !== next.offset
        || result.saved_bytes !== expected.savedBytes || result.observed_bytes !== expected.observedBytes
        || result.capture_complete !== expected.complete || typeof result.content !== 'string'
        || result.offset_unit !== 'unicode_characters_in_redacted_text' || result.encoding !== 'utf-8-with-replacement'
        || !Number.isSafeInteger(result.total_chars) || result.total_chars < next.offset
        || (result.next_offset !== null && (!Number.isSafeInteger(result.next_offset)
          || result.next_offset <= next.offset || result.next_offset > result.total_chars))) {
        throw new ApiError('Saved command output could not be verified.', 'command_output_unverified', 0);
      }
      if (current === serial.current) setPassage(result);
    } catch (problem) {
      if (current === serial.current && !controller.signal.aborted) setError(problem);
    } finally {
      if (current === serial.current) setLoading(false);
      if (readRequest.current === controller) readRequest.current = null;
    }
  };

  const download = async () => {
    if (!selection || !passage || downloadRequest.current) return;
    const controller = new AbortController(); downloadRequest.current = controller;
    setDownloadState('loading'); setError(null);
    try {
      const file = await api.downloadCommandOutput(view.page.id, selection.output, selection.stream, controller.signal);
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

  return <details className="command-outputs" onToggle={event => { if (!event.currentTarget.open) close(); }}>
    <summary>{t('commands.title')} <small>{t('commands.count', { count: outputs!.items.length })}</small></summary>
    <p className="command-output-note">{t('commands.intro')}</p>
    <ul>{outputs!.items.map(output => <li key={output.actionId}>
      <button type="button" className="command-output-open" aria-expanded={selection?.output.actionId === output.actionId}
        disabled={Boolean(output.error) || !output.streams.stdout || !output.streams.stderr}
        onClick={() => { if (selection?.output.actionId === output.actionId) close(); else void read({ output, stream: 'stdout', offset: 0, previous: [] }); }}>
        <span>{output.commandLabel || t('commands.command')}<time dateTime={output.createdAt}>{new Date(output.createdAt).toLocaleString()}</time></span>
        {output.exitCode !== null ? <small>{t('commands.exit', { code: output.exitCode })}</small> : null}
      </button>
      {output.error ? <p className="command-output-note">{t('commands.captureFailed')}</p> : null}
      {selection?.output.actionId === output.actionId ? <div className="command-output-reader" role="region" aria-label={t('commands.title')}>
        <div className="command-output-streams" role="group" aria-label={t('commands.title')}>
          {(['stdout', 'stderr'] as const).map(stream => <button type="button" key={stream} aria-pressed={selection.stream === stream}
            onClick={() => void read({ output, stream, offset: 0, previous: [] })}>{t(`commands.${stream}`)}</button>)}
        </div>
        <p className={`command-output-note${output.streams[selection.stream]!.complete ? '' : ' command-output-incomplete'}`}>{output.streams[selection.stream]!.complete
          ? t('commands.complete', { bytes: output.streams[selection.stream]!.savedBytes })
          : t('commands.partial', { saved: output.streams[selection.stream]!.savedBytes, observed: output.streams[selection.stream]!.observedBytes })}</p>
        {loading ? <p role="status">{t('commands.reading')}</p> : null}
        {error ? <p className="command-output-error" role="alert">{errorText(error)}</p> : null}
        {passage ? <>
          <p className="command-output-range">{t('commands.range', { start: passage.total_chars ? passage.offset + 1 : 0,
            end: passage.next_offset ?? passage.total_chars, total: passage.total_chars })}</p>
          {passage.content ? <pre tabIndex={0}>{passage.content}</pre> : <p>{t('commands.empty')}</p>}
          <div className="command-output-actions">
            <button type="button" className="secondary-button" disabled={!selection.previous.length}
              onClick={() => void read({ ...selection, offset: selection.previous[selection.previous.length - 1], previous: selection.previous.slice(0, -1) })}>{t('commands.previous')}</button>
            <button type="button" className="secondary-button" disabled={passage.next_offset === null}
              onClick={() => { if (passage.next_offset !== null) void read({ ...selection, offset: passage.next_offset, previous: [...selection.previous, selection.offset] }); }}>{t('commands.next')}</button>
          </div>
          {passage.next_offset === null ? <p className="command-output-note">{t('commands.end')}</p> : null}
          {passage.redacted ? <p className="command-output-note">{t('commands.redacted')}</p> : null}
          <div className="command-output-actions">
            <button type="button" className="secondary-button" disabled={downloadState === 'loading'} onClick={() => void download()}>{t(downloadState === 'loading' ? 'commands.downloading' : 'commands.download')}</button>
            {downloadState === 'started' ? <span role="status">{t('commands.downloaded')}</span> : null}
          </div>
        </> : null}
        {!loading && error ? <button type="button" className="text-button" onClick={() => void read(selection)}>{t('commands.retry')}</button> : null}
        <details className="command-output-details"><summary>{t('commands.details')}</summary>
          <p className="command-output-note">{t('commands.bounds')}</p>
          <p className="command-output-note">{t('commands.encoding')}</p>
          <p className="command-output-source">{t('commands.source', { action: output.actionId, turn: output.turnId })}</p>
        </details>
      </div> : null}
    </li>)}</ul>
    {outputs!.truncated ? <p className="command-output-note">{t('commands.more')}</p> : null}
  </details>;
}
