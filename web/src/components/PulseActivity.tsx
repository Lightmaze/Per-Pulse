import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import { PulseActivityRequestGate } from '../pulseActivityRequestGate';
import { attentionSourceTarget, localRetainedAttention } from '../pulseAttention';
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
  const { t, pageTitle } = useLanguage();
  const [pulse, setPulse] = useState<PulseState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState('');
  const [kind, setKind] = useState<'excitatory' | 'inhibitory'>('excitatory');
  const gate = useRef(new PulseActivityRequestGate()).current;
  const options: PageOption[] = [{ id: 'home', title: t('app.home') }, ...pages
    .filter(page => page.id !== 'home').map(page => ({ id: page.id, title: pageTitle(page) }))];
  const validIds = new Set(options.map(page => page.id));
  const availableTargets = options.filter(page => page.id !== pageId);

  const reload = useCallback(async (signal?: AbortSignal, preserveError = false) => {
    const ticket = gate.beginRead();
    if (!ticket) return; // A PATCH owns the visible state until it settles.
    try {
      const status = await api.pulse(signal);
      if (!signal?.aborted && gate.acceptsRead(ticket)) {
        setPulse(status);
        if (!preserveError) setError('');
      }
    } catch (problem) {
      if (!signal?.aborted && gate.acceptsRead(ticket) && !preserveError) {
        setError(problem instanceof Error ? problem.message : t('pulse.readError'));
      }
    }
  }, [gate]);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    const timer = window.setInterval(() => { void reload(controller.signal); }, 10000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [reload]);

  const act = async (call: () => Promise<{ pulse: PulseState }>) => {
    if (!gate.beginWrite()) return; // Also prevent duplicate clicks before React rerenders.
    setBusy(true);
    setError('');
    let unconfirmed = false;
    try {
      const result = await call();
      setPulse(result.pulse);
      setTarget('');
    } catch (problem) {
      // A network failure can hide an already-committed mutation. Re-read the
      // native state rather than claiming it definitely failed or rolling back.
      unconfirmed = true;
      const detail = problem instanceof Error ? problem.message : t('pulse.confirmUnknown');
      setError(t('pulse.confirm', { detail }));
    } finally {
      gate.finishWrite();
      setBusy(false);
      if (unconfirmed) void reload(undefined, true);
    }
  };

  const nameFor = (id: string | null, centerId?: string | null) => {
    if (id && validIds.has(id)) return options.find(page => page.id === id)?.title ?? id;
    if (id === null && centerId !== null && centerId !== undefined) return t('pulse.earlier', { id: shortId(centerId) });
    return t('pulse.unknown');
  };

  const jump = (id: string | null, messageId?: string) => {
    if (!id || !validIds.has(id)) return;
    onClose();
    navigate(id, id === pageId ? undefined : { pageId }, messageId);
  };

  const edges = pulse?.connections.filter(edge => edge.sourcePageId === pageId) ?? [];
  // Model-retained notes are private to their original local context, not a
  // global cross-Work summary. An unknown older center stays unresolved.
  const retained = localRetainedAttention(pulse, pageId);
  const recent = pulse?.recentActivations ?? [];
  const thisPageHeld = pulse?.heldContexts.some(held => held.pageId === pageId) ?? false;

  return <Dialog title={t('pulse.title')} onClose={() => { if (!busy) onClose(); }} className="pulse-activity-dialog">
    <p className="dialog-description">{t('pulse.description')}</p>
    {error ? <p className="form-error" role="alert">{error} <button className="text-button" type="button" onClick={() => void reload()}>{t('pulse.retry')}</button></p> : null}
    {!pulse ? <p role="status" className="pulse-activity-empty">{t('pulse.reading')}</p> : <>
      <section className="pulse-activity-section" aria-label={t('pulse.background')}>
        <div className="pulse-activity-heading">
          <h3>{t('pulse.background')}</h3>
          <button className="text-button" type="button" onClick={() => void reload()}>{t('pulse.refresh')}</button>
          <button className="text-button" type="button" disabled={busy || !pulse.enabled}
            onClick={() => void act(() => api.setPulsePaused(!pulse.paused))}>
            {pulse.paused ? t('pulse.resumeBg') : t('pulse.pauseBg')}
          </button>
        </div>
        <div className="pulse-activity-heading">
          <h3>{t('pulse.current')}</h3>
          <button className="text-button" type="button" disabled={busy || !pulse.enabled}
            onClick={() => void act(() => api.setPulseContextPaused(pageId, !thisPageHeld))}>
            {thisPageHeld ? t('pulse.resumePage') : t('pulse.holdPage')}
          </button>
        </div>
        <p className="pulse-activity-detail">
          {thisPageHeld ? t('pulse.held') : t('pulse.holdInfo')}
        </p>
        <p className="pulse-activity-summary" role="status">{pulse.paused && pulse.enabled ? t('pulse.paused') : pulse.running ? t('pulse.continuing') : pulse.enabled ? t('pulse.enabled') : t('pulse.disabled')} · {t('pulse.queue', { queued: pulse.queue.queued, running: pulse.queue.running })}</p>
        <p className="pulse-activity-detail">{t('pulse.budget', { remaining: pulse.budget.remaining, limit: pulse.budget.limit })}</p>
        {pulse.queue.oldestQueuedAt ? <p className="pulse-activity-detail">{t('pulse.oldest', { time: eventTime(pulse.queue.oldestQueuedAt) })}</p> : null}
        {!pulse.providerConfigured ? <p className="pulse-activity-detail">{t('pulse.noProvider')}</p> : null}
      </section>
      <section className="pulse-activity-section" aria-label={t('pulse.thoughts')}>
        <h3>{t('pulse.thoughts')}</h3>
        <p className="pulse-activity-detail">{t('pulse.thoughtsInfo')}</p>
        {!retained.length ? <p className="pulse-activity-empty">{t('pulse.thoughtsNone')}</p> :
          <ul className="pulse-activity-list">{retained.map(entry => {
            const source = attentionSourceTarget(entry, validIds);
            return <li key={entry.center_id ?? 'home'}>
              <p className="pulse-activity-thought">{entry.note || t('pulse.thoughtsReleased')}</p>
              {entry.note ? <span className="pulse-activity-detail">{t('pulse.thoughtsDrive', { drive: entry.drive.toFixed(2) })}</span> : null}
              {entry.sourceEventId ? <>
                {source
                  ? <button className="pulse-activity-link" type="button"
                      onClick={() => jump(source.pageId, source.messageId)}>
                      {t(source.messageId ? 'pulse.thoughtsOpenSource' : 'pulse.thoughtsOpenPage')}
                    </button>
                  : <span className="pulse-activity-detail">{t('pulse.thoughtsUnavailable')}</span>}
                <span className="pulse-activity-identity" title={entry.sourceEventId}>{t('pulse.source', { id: shortId(entry.sourceEventId) })}</span>
                {entry.sourceActionId ? <span className="pulse-activity-identity" title={entry.sourceActionId}>{t('pulse.thoughtsAction', { id: shortId(entry.sourceActionId) })}</span> : null}
              </> : null}
            </li>;
          })}</ul>}
      </section>
      <section className="pulse-activity-section" aria-label={t('pulse.connections')}>
        <h3>{t('pulse.connections')}</h3>
        <p className="pulse-activity-detail">{t('pulse.connectionsInfo')}</p>
        {!edges.length ? <p className="pulse-activity-empty">{t('pulse.noEdges')}</p> : <ul className="pulse-activity-list">
          {edges.map(edge => <li key={edge.id}>
            <span>{edge.kind === 'inhibitory' ? t('pulse.settles') : t('pulse.excites')} → </span>
            {edge.targetPageId && validIds.has(edge.targetPageId)
              ? <button className="pulse-activity-link" onClick={() => jump(edge.targetPageId)}>{nameFor(edge.targetPageId)}</button>
              : <span>{nameFor(edge.targetPageId, edge.target_center_id)}</span>}
            {edge.targetPageId && validIds.has(edge.targetPageId) ?
              <button className="text-button" type="button" disabled={busy} onClick={() => void act(() => api.disconnectPulse(pageId, edge.targetPageId!))}>{t('pulse.remove')}</button> : null}
          </li>)}
        </ul>}
        {availableTargets.length ? <form className="pulse-activity-add" onSubmit={event => {
          event.preventDefault();
          if (!busy && target && validIds.has(target)) void act(() => api.connectPulse(pageId, target, kind));
        }}>
          <label htmlFor="pulse-target">{t('pulse.connectTo')}</label>
          <select id="pulse-target" value={target} onChange={event => setTarget(event.target.value)}>
            <option value="">{t('pulse.choose')}</option>
            {availableTargets.map(page => <option key={page.id} value={page.id}>{page.title}</option>)}
          </select>
          <label htmlFor="pulse-kind">{t('pulse.influence')}</label>
          <select id="pulse-kind" value={kind} onChange={event => setKind(event.target.value as 'excitatory' | 'inhibitory')}>
            <option value="excitatory">{t('pulse.excite')}</option>
            <option value="inhibitory">{t('pulse.inhibit')}</option>
          </select>
          <button className="secondary-button" type="submit" disabled={busy || !target}>{t('pulse.connect')}</button>
        </form> : null}
      </section>
      <section className="pulse-activity-section" aria-label={t('pulse.recent')}>
        <h3>{t('pulse.recent')}</h3>
        {!recent.length ? <p className="pulse-activity-empty">{t('pulse.none')}</p> : <ol className="pulse-activity-list">
          {recent.slice(0, 12).map(activity => <li key={activity.eventId}>
            <div className="pulse-activity-event">
              {activity.pageId && validIds.has(activity.pageId)
                ? <button className="pulse-activity-link" onClick={() => jump(activity.pageId)}>{nameFor(activity.pageId)}</button>
                : <span>{nameFor(activity.pageId, activity.centerId)}</span>}
              <span className="pulse-activity-state">{activity.status}</span>
            </div>
            <span className="pulse-activity-detail">{eventTime(activity.createdAt)} · {activity.kind}</span>
            {activity.deferredReason ? <span className="pulse-activity-detail">{t('pulse.waiting', { reason: activity.deferredReason })}</span> : null}
            {activity.sourceEventId ? <span className="pulse-activity-detail">{t('pulse.from')} {activity.sourcePageId && validIds.has(activity.sourcePageId)
              ? <button className="pulse-activity-link" onClick={() => jump(activity.sourcePageId)}>{nameFor(activity.sourcePageId)}</button>
              : <span>{nameFor(activity.sourcePageId, activity.sourceCenterId)}</span>}
              <span className="pulse-activity-identity" title={activity.sourceEventId}>{t('pulse.source', { id: shortId(activity.sourceEventId) })}</span>
            </span> : null}
            <span className="pulse-activity-identity" title={activity.eventId}>{t('pulse.event', { id: shortId(activity.eventId) })}</span>
          </li>)}
        </ol>}
      </section>
    </>}
  </Dialog>;
}
