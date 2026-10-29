/** A quiet Home doorway back into verified local computation.
 *
 * Read-only World projection: never creates new user messages, model turns,
 * cross-Work transcript copies or automatic Home results.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import { useEffect, useState } from 'react';
import { api } from '../api';
import { homeContinuations, homeWorkNeedsAttention, verifiedHomePulse } from '../homeContinuations';
import { useLanguage } from '../i18n';
import { markVisitedLocalEvent, readVisitedLocalEvents } from '../storage';
import { pulseActivityLabel } from '../pulseActivityLabels';
import type { Bootstrap, PageSummary, PulseObservation } from '../types';
import type { Navigate } from './SessionLink';

export function HomeContinuations({ pages, navigate, workspaceId }: {
  pages: PageSummary[];
  navigate: Navigate;
  workspaceId: string;
}) {
  const { t, pageTitle } = useLanguage();
  const [visited, setVisited] = useState<{ workspaceId: string; eventIds: string[] }>(() => ({
    workspaceId, eventIds: readVisitedLocalEvents(workspaceId),
  }));
  // Foreground Work and Pulse are read independently. Do not pair their
  // results until they agree on both the persistent World and live process.
  // A restart can happen between the requests even within one polling tick.
  const [snapshot, setSnapshot] = useState<{
    workspaceId: string; observation: PulseObservation | null; bootstrap: Bootstrap | null;
  } | null>(null);
  const currentSnapshot = snapshot?.workspaceId === workspaceId ? snapshot : null;
  const freshBootstrap = currentSnapshot?.bootstrap?.workspaceId === workspaceId
    ? currentSnapshot.bootstrap : null;
  const pulse = verifiedHomePulse(workspaceId, freshBootstrap, currentSnapshot?.observation ?? null);
  const freshPages = freshBootstrap?.pages ?? null;

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
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, bootstrap: previous?.workspaceId === workspaceId ? previous.bootstrap : null,
          observation: latest.workspaceId === workspaceId ? latest : null,
        }));
      } catch {
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, bootstrap: previous?.workspaceId === workspaceId ? previous.bootstrap : null,
          observation: null,
        }));
      } finally {
        readingPulse = false;
      }
    };
    const refreshWorkspace = async () => {
      if (readingWorkspace || controller.signal.aborted) return;
      readingWorkspace = true;
      try {
        const latest = await api.bootstrap(controller.signal);
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, observation: previous?.workspaceId === workspaceId ? previous.observation : null,
          bootstrap: latest.workspaceId === workspaceId ? latest : null,
        }));
      } catch {
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, observation: previous?.workspaceId === workspaceId ? previous.observation : null,
          bootstrap: null,
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
  }, [workspaceId]);

  // Verify page ownership against the fresh native World before producing
  // exact-source Pulse links or actionable Work status.
  const currentPages = freshPages ?? pages;
  const visitedIds = visited.workspaceId === workspaceId
    ? visited.eventIds : readVisitedLocalEvents(workspaceId);
  const items = homeContinuations(freshPages ? pulse : null, freshPages ?? [], 3, new Set(visitedIds));
  const needAttention = homeWorkNeedsAttention(freshPages ?? []);
  if (!items.length && !needAttention.length) return null;

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
    </section> : null}
    {items.length ? <section className="home-continuations" aria-label={t('pulse.home.title')}>
      <h2>{t('pulse.home.title')}</h2>
      <p className="home-continuations-intro">{t('pulse.home.info')}</p>
      <ol className="home-continuations-list">
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
      </ol>
    </section> : null}
  </>;
}
