import { useEffect, useState } from 'react';
import { api } from '../api';
import type { PageSummary, PulseState } from '../types';
import { Dialog } from './Dialog';

const message = (error: unknown) => error instanceof Error ? error.message : 'Your change could not be saved.';

export function AttentionControl({ pageId, pages, configured }: { pageId: string; pages: PageSummary[]; configured: boolean }) {
  const [pulse, setPulse] = useState<PulseState | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [budget, setBudget] = useState(6);
  const [target, setTarget] = useState('');
  const [influence, setInfluence] = useState(1);
  useEffect(() => {
    const abort = new AbortController();
    let timer: number;
    const refresh = async () => {
      try { setPulse(await api.pulse(abort.signal)); }
      catch (problem) { if (!abort.signal.aborted) setError(message(problem)); }
      if (!abort.signal.aborted) timer = window.setTimeout(refresh, 2000);
    };
    refresh();
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, []);
  const local = pulse?.contexts.find(context => context.pageId === pageId);
  const linked = pulse?.connections.filter(edge => edge.sourcePageId === pageId) ?? [];
  const show = () => { setNote(local?.note ?? ''); setTarget(''); setError(''); setOpen(true); };
  const act = async (task: () => Promise<PulseState>) => {
    setBusy(true); setError('');
    try { setPulse(await task()); }
    catch (problem) { setError(message(problem)); }
    finally { setBusy(false); }
  };
  return <div className="attention-control">
    <button className="text-button" onClick={show}>Keep thinking</button>
    <span className="attention-status" role="status">{pulse?.enabled ? `${pulse.remaining} continuations available` : local?.note && local.drive > 0 ? 'Thought retained' : 'At rest'}</span>
    {pulse?.enabled ? <button className="text-button" disabled={busy} onClick={() => act(() => api.pausePulses())}>Pause</button> : null}
    {error && !open ? <span className="form-error" role="alert">{error}</span> : null}
    {open ? <Dialog title="Let this thought continue." onClose={() => { if (!busy) setOpen(false); }}>
      <p className="dialog-description">Keep an unfinished thought in this page. It can return when its attention builds or a connected page brings something relevant.</p>
      <form onSubmit={event => { event.preventDefault(); if (note.trim() && !busy) act(async () => { await api.holdThought(pageId, note.trim(), 1); return api.runPulses(budget); }); }}>
        <label className="field-label" htmlFor="attention-note">What is still on your mind?</label>
        <textarea id="attention-note" autoFocus rows={4} maxLength={4096} value={note} onChange={event => setNote(event.target.value)} placeholder="An open question, a possibility to explore, or something to think through…" required />
        <label className="field-label" htmlFor="attention-budget">Background allowance</label>
        <select id="attention-budget" value={budget} onChange={event => setBudget(Number(event.target.value))}>
          {[1, 3, 6, 12].map(value => <option key={value} value={value}>Up to {value} {value === 1 ? 'continuation' : 'continuations'}</option>)}
        </select>
        <p className="settings-detail">Shared across your pages. Pausing stops new background turns; a turn already running may finish. Closing the server pauses the allowance.</p>
        {!configured ? <p className="settings-detail">Connect a model to start. You can still save this thought.</p> : null}
        <div className="dialog-actions">
          <button className="secondary-button" type="button" disabled={busy || !note.trim()} onClick={() => act(() => api.holdThought(pageId, note.trim(), 1))}>Save thought</button>
          <button className="primary-button" type="submit" disabled={busy || !configured || !note.trim()}>{busy ? 'Saving…' : 'Continue thinking'}</button>
        </div>
      </form>
      {local?.note ? <div className="attention-release"><button className="text-button" disabled={busy} onClick={() => act(() => api.holdThought(pageId, local.note, 0))}>Let this thought rest</button>{pulse?.enabled ? <button className="text-button" disabled={busy} onClick={() => act(() => api.pausePulses())}>Pause all background thinking</button> : null}</div> : null}
      {pages.length > 1 ? <details className="attention-links"><summary>Connect this thought</summary>
        <p className="settings-detail">A result here can draw another page's attention or help it settle. Only an attributed excerpt becomes available there.</p>
        {linked.map(edge => <div className="attention-link" key={edge.targetPageId}><span>{edge.weight > 0 ? 'Draws attention to' : 'Helps settle'} {pages.find(page => page.id === edge.targetPageId)?.title ?? 'a page'}</span><button className="text-button" disabled={busy} onClick={() => act(() => api.connectAttention(pageId, edge.targetPageId, 0))}>Remove</button></div>)}
        <label className="field-label" htmlFor="attention-target">Related page</label>
        <select id="attention-target" value={target} onChange={event => setTarget(event.target.value)}><option value="">Choose a page</option>{pages.filter(page => page.id !== pageId).map(page => <option key={page.id} value={page.id}>{page.title}</option>)}</select>
        <label className="field-label" htmlFor="attention-influence">When this page has a result</label>
        <select id="attention-influence" value={influence} onChange={event => setInfluence(Number(event.target.value))}><option value={1}>Draw attention to the related page</option><option value={-1}>Help the related page settle</option></select>
        <button className="secondary-button" disabled={busy || !target} onClick={() => act(() => api.connectAttention(pageId, target, influence))}>Connect pages</button>
      </details> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </Dialog> : null}
  </div>;
}
