import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import type { PageSummary, PulseState } from '../types';
import { Dialog } from './Dialog';
import type { Navigate } from './SessionLink';

type PageOption = { id: string; title: string };

function eventTime(value: string): string {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString();
}

function shortId(value: string): string {
  return value.length > 18 ? value.slice(0, 16) + '…' : value;
}

export function PulseActivity({ pageId, pages, navigate, onClose }: {
  pageId: string;
  pages: PageSummary[];
  navigate: Navigate;
  onClose: () => void;
}) {
  const [pulse, setPulse] = useState<PulseState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState('');
  const [kind, setKind] = useState<'excitatory' | 'inhibitory'>('excitatory');
  const options: PageOption[] = [{ id: 'home', title: 'Home' }, ...pages
    .filter(page => page.id !== 'home').map(page => ({ id: page.id, title: page.title }))];
  const validIds = new Set(options.map(page => page.id));
  const availableTargets = options.filter(page => page.id !== pageId);

  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const status = await api.pulse(signal);
      if (!signal?.aborted) { setPulse(status); setError(''); }
    } catch (problem) {
      if (!signal?.aborted) setError(problem instanceof Error ? problem.message : 'Could not read activity.');
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    const timer = window.setInterval(() => { void reload(controller.signal); }, 10000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [reload]);

  const act = async (call: () => Promise<{ pulse: PulseState }>) => {
    setBusy(true);
    setError('');
    try {
      const result = await call();
      setPulse(result.pulse);
      setTarget('');
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'The connection could not be changed.');
    } finally {
      setBusy(false);
    }
  };

  const nameFor = (id: string | null, centerId?: string | null) => {
    if (id && validIds.has(id)) return options.find(page => page.id === id)?.title ?? id;
    if (id === null && centerId !== null && centerId !== undefined) return 'Earlier Work · ' + shortId(centerId);
    return 'Work not in the current page list';
  };

  const jump = (id: string | null) => {
    if (!id || !validIds.has(id)) return;
    onClose();
    navigate(id);
  };

  const edges = pulse?.connections.filter(edge => edge.sourcePageId === pageId) ?? [];
  const recent = pulse?.recentActivations ?? [];

  return <Dialog title="Activity" onClose={() => { if (!busy) onClose(); }} className="pulse-activity-dialog">
    <p className="dialog-description">Local activity and connections in this continuing workspace. These are native records, not a separate agent or a replay of another page.</p>
    {error ? <p className="form-error" role="alert">{error} <button className="text-button" type="button" onClick={() => void reload()}>Try again</button></p> : null}
    {!pulse ? <p role="status" className="pulse-activity-empty">Reading activity…</p> : <>
      <section className="pulse-activity-section" aria-label="Background activity">
        <div className="pulse-activity-heading">
          <h3>Background activity</h3>
          <button className="text-button" type="button" onClick={() => void reload()}>Refresh</button>
        </div>
        <p className="pulse-activity-summary" role="status">{pulse.running ? 'Continuing' : pulse.enabled ? 'Enabled · not running' : 'Not enabled'} · {pulse.queue.queued} queued · {pulse.queue.running} running</p>
        <p className="pulse-activity-detail">{pulse.budget.remaining} of {pulse.budget.limit} background admissions left in the rolling hour. This is a turn count, not a model token or money limit.</p>
        {pulse.queue.oldestQueuedAt ? <p className="pulse-activity-detail">Oldest queued: {eventTime(pulse.queue.oldestQueuedAt)}</p> : null}
        {!pulse.providerConfigured ? <p className="pulse-activity-detail">A model is not connected; no background response is generated.</p> : null}
      </section>
      <section className="pulse-activity-section" aria-label="Context connections">
        <h3>Connections from this page</h3>
        <p className="pulse-activity-detail">These links carry influence after a result settles. Opening a page link does not itself trigger background work.</p>
        {!edges.length ? <p className="pulse-activity-empty">No outgoing computation connections.</p> : <ul className="pulse-activity-list">
          {edges.map(edge => <li key={edge.id}>
            <span>{edge.kind === 'inhibitory' ? 'Settles' : 'Excites'} → </span>
            {edge.targetPageId && validIds.has(edge.targetPageId)
              ? <button className="pulse-activity-link" onClick={() => jump(edge.targetPageId)}>{nameFor(edge.targetPageId)}</button>
              : <span>{nameFor(edge.targetPageId, edge.target_center_id)}</span>}
            {edge.targetPageId && validIds.has(edge.targetPageId) ?
              <button className="text-button" type="button" disabled={busy} onClick={() => void act(() => api.disconnectPulse(pageId, edge.targetPageId!))}>Remove</button> : null}
          </li>)}
        </ul>}
        {availableTargets.length ? <form className="pulse-activity-add" onSubmit={event => {
          event.preventDefault();
          if (!busy && target && validIds.has(target)) void act(() => api.connectPulse(pageId, target, kind));
        }}>
          <label htmlFor="pulse-target">Connect to</label>
          <select id="pulse-target" value={target} onChange={event => setTarget(event.target.value)}>
            <option value="">Choose a page</option>
            {availableTargets.map(page => <option key={page.id} value={page.id}>{page.title}</option>)}
          </select>
          <label htmlFor="pulse-kind">Influence</label>
          <select id="pulse-kind" value={kind} onChange={event => setKind(event.target.value as 'excitatory' | 'inhibitory')}>
            <option value="excitatory">Excite</option>
            <option value="inhibitory">Inhibit</option>
          </select>
          <button className="secondary-button" type="submit" disabled={busy || !target}>Connect</button>
        </form> : null}
      </section>
      <section className="pulse-activity-section" aria-label="Recent activity">
        <h3>Recent activity</h3>
        {!recent.length ? <p className="pulse-activity-empty">No local background activity recorded yet.</p> : <ol className="pulse-activity-list">
          {recent.slice(0, 12).map(activity => <li key={activity.eventId}>
            <div className="pulse-activity-event">
              {activity.pageId && validIds.has(activity.pageId)
                ? <button className="pulse-activity-link" onClick={() => jump(activity.pageId)}>{nameFor(activity.pageId)}</button>
                : <span>{nameFor(activity.pageId, activity.centerId)}</span>}
              <span className="pulse-activity-state">{activity.status}</span>
            </div>
            <span className="pulse-activity-detail">{eventTime(activity.createdAt)} · {activity.kind}</span>
            {activity.deferredReason ? <span className="pulse-activity-detail">Waiting: {activity.deferredReason}</span> : null}
            <span className="pulse-activity-identity" title={activity.eventId}>Event: {shortId(activity.eventId)}</span>
          </li>)}
        </ol>}
      </section>
    </>}
  </Dialog>;
}
