/** A quiet Home doorway back into verified local computation.
 *
 * Read-only World projection: never creates new user messages, model turns,
 * cross-Work transcript copies or automatic Home results.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { homeContinuations, homeWorkNeedsAttention, verifiedHomePulse } from '../homeContinuations';
import { useLanguage } from '../i18n';
import { markVisitedLocalEvent, readVisitedLocalEvents } from '../storage';
import { pulseActivityLabel } from '../pulseActivityLabels';
import type { Bootstrap, PageSummary, PulseHistoricalResult, PulseObservation, PulseResultHistory } from '../types';
import type { Navigate } from './SessionLink';

export function HomeContinuations({ pages, navigate, workspaceId, serviceInstanceId, readWorkspace }: {
  pages: PageSummary[];
  navigate: Navigate;
  workspaceId: string;
  serviceInstanceId: string;
  readWorkspace: (signal: AbortSignal, expected: Pick<Bootstrap, 'workspaceId' | 'serviceInstanceId'>) => Promise<Bootstrap>;
}) {
  const { t, pageTitle } = useLanguage();
  // An explicit reconnect updates the next scheduled read without starting
  // another observation or replacing the existing interval.
  const serviceInstance = useRef(serviceInstanceId);
  serviceInstance.current = serviceInstanceId;
  const [showAllPending, setShowAllPending] = useState(false);
  const [showAllResults, setShowAllResults] = useState(false);
  const [refreshRequest, setRefreshRequest] = useState(0);
  const [olderHistory, setOlderHistory] = useState<PulseResultHistory | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const historyReading = useRef(false);
  const [visited, setVisited] = useState<{ workspaceId: string; eventIds: string[] }>(() => ({
    workspaceId, eventIds: readVisitedLocalEvents(workspaceId),
  }));
  // Foreground Work and Pulse are read independently. Do not pair their
  // results until they agree on both the persistent World and live process.
  // A restart can happen between the requests even within one polling tick.
  const [snapshot, setSnapshot] = useState<{
    workspaceId: string; observation: PulseObservation | null; bootstrap: Bootstrap | null;
    pulseUnavailable: boolean; workspaceUnavailable: boolean;
  } | null>(null);
  const currentSnapshot = snapshot?.workspaceId === workspaceId ? snapshot : null;
  // A previous read is useful for a disabled preview, never for an approval
  // or a navigable result. Both World and service identity must still match.
  const lastKnownBootstrap = currentSnapshot?.bootstrap?.workspaceId === workspaceId
    && currentSnapshot.bootstrap.serviceInstanceId === serviceInstanceId
    ? currentSnapshot.bootstrap : null;
  const freshBootstrap = currentSnapshot?.workspaceUnavailable ? null : lastKnownBootstrap;
  const pulse = verifiedHomePulse(workspaceId, freshBootstrap,
    currentSnapshot?.pulseUnavailable ? null : currentSnapshot?.observation ?? null);
  const freshPages = freshBootstrap?.pages ?? null;
  // A failed Pulse observation does not invalidate a successfully read World
  // page list. Current Work approvals remain navigable from Home.
  const workspaceUnavailable = Boolean(currentSnapshot?.workspaceUnavailable);
  const unavailable = Boolean(currentSnapshot?.pulseUnavailable || workspaceUnavailable);

  useEffect(() => {
    const controller = new AbortController();
    let readingPulse = false;
    let readingWorkspace = false;
    // Foreground Work review must not wait behind a slow Pulse read. These
    // independent native read models share only a World-tagged UI projection.
    const refreshPulse = async () => {
      if (readingPulse || controller.signal.aborted) return;
      readingPulse = true;
      try {
        const latest = await api.pulseObservation(controller.signal);
        if (!controller.signal.aborted) setSnapshot(previous => {
          const sameWorld = previous?.workspaceId === workspaceId;
          const verified = latest.workspaceId === workspaceId
            && latest.serviceInstanceId === serviceInstance.current;
          return {
            workspaceId, bootstrap: sameWorld ? previous.bootstrap : null,
            observation: verified ? latest : sameWorld ? previous.observation : null,
            pulseUnavailable: !verified, workspaceUnavailable: sameWorld ? previous.workspaceUnavailable : false,
          };
        });
      } catch {
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, bootstrap: previous?.workspaceId === workspaceId ? previous.bootstrap : null,
          observation: previous?.workspaceId === workspaceId ? previous.observation : null,
          pulseUnavailable: true,
          workspaceUnavailable: previous?.workspaceId === workspaceId ? previous.workspaceUnavailable : false,
        }));
      } finally {
        readingPulse = false;
      }
    };
    const refreshWorkspace = async () => {
      if (readingWorkspace || controller.signal.aborted) return;
      readingWorkspace = true;
      try {
        // The existing Home observation also updates shared page summaries.
        // Its reader preserves newer local views and fences service changes.
        const latest = await readWorkspace(controller.signal, { workspaceId, serviceInstanceId: serviceInstance.current });
        if (!controller.signal.aborted) setSnapshot(previous => {
          const sameWorld = previous?.workspaceId === workspaceId;
          const verified = latest.workspaceId === workspaceId
            && latest.serviceInstanceId === serviceInstance.current;
          return {
            workspaceId, observation: sameWorld ? previous.observation : null,
            bootstrap: verified ? latest : sameWorld ? previous.bootstrap : null,
            pulseUnavailable: sameWorld ? previous.pulseUnavailable : false,
            workspaceUnavailable: !verified,
          };
        });
      } catch {
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, observation: previous?.workspaceId === workspaceId ? previous.observation : null,
          bootstrap: previous?.workspaceId === workspaceId ? previous.bootstrap : null,
          pulseUnavailable: previous?.workspaceId === workspaceId ? previous.pulseUnavailable : false,
          workspaceUnavailable: true,
        }));
      } finally {
        readingWorkspace = false;
      }
    };
    void refreshPulse();
    void refreshWorkspace();
    const interval = window.setInterval(() => {
      void refreshPulse();
      void refreshWorkspace();
    }, 12000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [workspaceId, serviceInstanceId, readWorkspace, refreshRequest]);

  // Verify page ownership against the fresh native World before producing
  // exact-source Pulse links or actionable Work status.
  const currentPages = freshPages ?? pages;
  const visitedIds = visited.workspaceId === workspaceId
    ? visited.eventIds : readVisitedLocalEvents(workspaceId);
  // The native observation supplies a bounded recent window. Reveal every
  // verified, not-yet-opened Work result in that window on request, instead
  // of silently hiding the fourth result behind the quiet Home preview.
  const availableResults = homeContinuations(
    freshPages ? pulse : null, freshPages ?? [], freshPages?.length ?? 0, new Set(visitedIds),
  );
  const items = showAllResults ? availableResults : availableResults.slice(0, 3);
  const history = olderHistory?.workspaceId === workspaceId
    && olderHistory.serviceInstanceId === serviceInstanceId ? olderHistory : null;
  const knownWorks = new Map((freshPages ?? []).filter(page => page.kind === 'work')
    .map(page => [page.id, page]));
  const recentEventIds = new Set(pulse?.recentActivations.map(event => event.eventId) ?? []);
  const olderResults = (history?.results ?? []).filter(
    result => !recentEventIds.has(result.eventId) && !visitedIds.includes(result.eventId)
      && knownWorks.has(result.pageId),
  );
  const canBrowseHistory = Boolean(freshPages && pulse
    && (pulse.recentActivations.length > 0 || history));
  const readOlder = async () => {
    if (historyReading.current || !freshPages || !pulse || history?.nextBeforeSeq === null) return;
    historyReading.current = true;
    setHistoryLoading(true);
    setHistoryError(false);
    try {
      const next = await api.pulseResults(history?.nextBeforeSeq ?? undefined);
      if (next.workspaceId !== workspaceId || next.serviceInstanceId !== serviceInstanceId) {
        setHistoryError(true);
        return;
      }
      setOlderHistory(previous => {
        const prior = previous?.workspaceId === workspaceId
          && previous.serviceInstanceId === serviceInstanceId ? previous.results : [];
        const unique = new Map<string, PulseHistoricalResult>();
        for (const row of [...prior, ...next.results]) unique.set(row.eventId, row);
        return { ...next, results: [...unique.values()] };
      });
    } catch {
      setHistoryError(true);
    } finally {
      historyReading.current = false;
      setHistoryLoading(false);
    }
  };
  // Show the first three without burying additional decisions or failed Works.
  // Expansion uses the same live, World-verified bootstrap; never cached page names.
  const pending = homeWorkNeedsAttention(freshPages ?? [], freshPages?.length ?? 0);
  const needAttention = showAllPending ? pending : pending.slice(0, 3);
  // Keep only a same-service, read-only last-known preview during an outage.
  // In particular, stale action/approval and exact-source links disappear.
  const lastKnownPages = lastKnownBootstrap?.pages ?? [];
  const lastKnownPulse = verifiedHomePulse(workspaceId, lastKnownBootstrap,
    currentSnapshot?.observation ?? null);
  const staleResults = unavailable ? homeContinuations(
    lastKnownPulse, lastKnownPages, lastKnownPages.length, new Set(visitedIds),
  ).slice(0, 3) : [];
  const stalePending = currentSnapshot?.workspaceUnavailable
    ? homeWorkNeedsAttention(lastKnownPages, lastKnownPages.length).slice(0, 3) : [];
  if (!items.length && !needAttention.length && !unavailable && !canBrowseHistory) return null;

  const titleOf = (id: string) => id === 'home' ? t('app.home')
    : currentPages.find(page => page.id === id)?.title ?? '';
  const open = (id: string, messageId?: string) => navigate(id, { pageId: 'home' }, messageId);
  const openLocalResult = (eventId: string, id: string, messageId: string) => {
    // A read receipt is confined to this browser and World. Inspecting the
    // causal source alone does not mark the receiving result as visited.
    const eventIds = markVisitedLocalEvent(workspaceId, eventId);
    setVisited({ workspaceId, eventIds });
    open(id, messageId);
  };

  return <>
    {unavailable ? <section className="home-continuations" aria-label={t('pulse.home.unavailableTitle')}>
      <h2>{t('pulse.home.unavailableTitle')}</h2>
      <p role="status" className="home-continuations-intro">{t(workspaceUnavailable
        ? 'pulse.home.unavailableInfo' : 'pulse.home.pulseUnavailableInfo')}</p>
      <button type="button" className="text-button" onClick={() => setRefreshRequest(value => value + 1)}>
        {t('pulse.refresh')}
      </button>
      {stalePending.length || staleResults.length ? <>
        <p className="home-continuations-intro">{t('pulse.home.lastKnown')}</p>
        <ul className="home-continuations-list">
          {stalePending.map(page => <li key={`pending-${page.id}`}>{pageTitle(page)}</li>)}
          {staleResults.map(({ event, page }) => <li key={`result-${event.eventId}`}>{pageTitle(page)}</li>)}
        </ul>
      </> : null}
    </section> : null}
    {needAttention.length ? <section className="home-continuations" aria-label={t('pulse.homeReview.title')}>
      <h2>{t('pulse.homeReview.title')}</h2>
      <p className="home-continuations-intro">{t('pulse.homeReview.info')}</p>
      <ol className="home-continuations-list">
        {needAttention.map(page => <li key={page.id}>
          <button type="button" className="home-continuation-open" onClick={() => open(page.id)}>
            {t('pulse.homeReview.open', { title: pageTitle(page) })}
          </button>
          <span className="home-continuation-detail">
            {t(page.status === 'error' ? 'sidebar.couldNotFinish' : 'sidebar.needsAttention')}
          </span>
        </li>)}
      </ol>
      {pending.length > 3 ? <button type="button" className="text-button"
        aria-expanded={showAllPending} onClick={() => setShowAllPending(value => !value)}>
        {showAllPending ? t('pulse.homeReview.showFewer') : t('pulse.homeReview.showAll', { count: pending.length })}
      </button> : null}
    </section> : null}
    {items.length || canBrowseHistory ? <section className="home-continuations" aria-label={t('pulse.home.title')}>
      <h2>{t('pulse.home.title')}</h2>
      <p className="home-continuations-intro">{t('pulse.home.info')}</p>
      {items.length ? <ol id="home-local-results" className="home-continuations-list">
        {items.map(({ event, page, receiver, source }) => <li key={event.eventId}>
          <button type="button" className="home-continuation-open" onClick={() => openLocalResult(event.eventId, receiver.pageId, receiver.messageId)}>
            {t('pulse.home.open', { title: pageTitle(page) })}
          </button>
          <span className="home-continuation-detail" title={event.status}>
            {pulseActivityLabel(t, 'kind', event.kind)}
          </span>
          {source && source.messageId ? <button type="button" className="home-continuation-source"
            onClick={() => open(source.pageId, source.messageId)}>
            {t('pulse.home.source', { title: titleOf(source.pageId) })}
          </button> : null}
          <span className="home-continuation-event" title={event.eventId}>{t('pulse.home.provenance')}</span>
        </li>)}
      </ol> : null}
      {availableResults.length > 3 ? <button type="button" className="text-button"
        aria-controls="home-local-results" aria-expanded={showAllResults}
        onClick={() => setShowAllResults(value => !value)}>
        {showAllResults ? t('pulse.home.showFewer') : t('pulse.home.showAll', { count: availableResults.length })}
      </button> : null}
      {canBrowseHistory ? <>
        {olderResults.length ? <ol className="home-continuations-list" aria-label={t('pulse.home.historyTitle')}>
          {olderResults.map(result => <li key={result.eventId}>
            <button type="button" className="home-continuation-open"
              onClick={() => openLocalResult(result.eventId, result.pageId, result.resultMessageId)}>
              {t('pulse.home.open', { title: pageTitle(knownWorks.get(result.pageId)!) })}
            </button>
            <span className="home-continuation-detail">{pulseActivityLabel(t, 'kind', result.kind)}</span>
            {result.sourcePageId && result.sourceMessageId
              && (result.sourcePageId === 'home' || knownWorks.has(result.sourcePageId))
              ? <button type="button" className="home-continuation-source"
                  onClick={() => open(result.sourcePageId!, result.sourceMessageId!)}>
                  {t('pulse.home.source', { title: titleOf(result.sourcePageId) })}
                </button> : null}
            <span className="home-continuation-event">{t('pulse.home.provenance')}</span>
          </li>)}
        </ol> : null}
        {historyError ? <p role="alert">{t('pulse.home.historyError')}</p> : null}
        {(!history || history.nextBeforeSeq !== null)
          ? <button type="button" className="text-button" disabled={historyLoading}
              onClick={() => void readOlder()}>
              {historyLoading ? t('pulse.home.historyLoading')
                : history ? t('pulse.home.historyMore') : t('pulse.home.historyBrowse')}
            </button>
          : <p className="home-continuations-intro">{t('pulse.home.historyEnd')}</p>}
      </> : null}
    </section> : null}
  </>;
}
