/** A quiet Home doorway back into verified local computation.
 *
 * Read-only World projection: never creates new user messages, model turns,
 * cross-Work transcript copies or automatic Home results.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import { useEffect, useState } from 'react';
import { api } from '../api';
import { homeContinuations, homeWorkNeedsAttention } from '../homeContinuations';
import { useLanguage } from '../i18n';
import { pulseActivityLabel } from '../pulseActivityLabels';
import type { PageSummary, PulseState } from '../types';
import type { Navigate } from './SessionLink';

export function HomeContinuations({ pages, navigate, workspaceId }: {
  pages: PageSummary[];
  navigate: Navigate;
  workspaceId: string;
}) {
  const { t, pageTitle } = useLanguage();
  // Keep both read models in a single World-tagged snapshot. If the local
  // service switches Worlds, no prior Work names or Pulse events may flash
  // into the new Home before its own bootstrap is confirmed.
  const [snapshot, setSnapshot] = useState<{
    workspaceId: string; pulse: PulseState | null; pages: PageSummary[] | null;
  } | null>(null);
  const currentSnapshot = snapshot?.workspaceId === workspaceId ? snapshot : null;
  const pulse = currentSnapshot?.pulse ?? null;
  const freshPages = currentSnapshot?.pages ?? null;

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
        const latest = await api.pulse(controller.signal);
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, pages: previous?.workspaceId === workspaceId ? previous.pages : null,
          pulse: latest,
        }));
      } catch {
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, pages: previous?.workspaceId === workspaceId ? previous.pages : null,
          pulse: null,
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
          workspaceId, pulse: previous?.workspaceId === workspaceId ? previous.pulse : null,
          pages: latest.workspaceId === workspaceId ? latest.pages : null,
        }));
      } catch {
        if (!controller.signal.aborted) setSnapshot(previous => ({
          workspaceId, pulse: previous?.workspaceId === workspaceId ? previous.pulse : null,
          pages: null,
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
  const items = homeContinuations(freshPages ? pulse : null, freshPages ?? []);
  const needAttention = homeWorkNeedsAttention(freshPages ?? []);
  if (!items.length && !needAttention.length) return null;

  const titleOf = (id: string) => id === 'home' ? t('app.home')
    : currentPages.find(page => page.id === id)?.title ?? '';
  const open = (id: string, messageId?: string) => navigate(id, { pageId: 'home' }, messageId);

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
          <button type="button" className="home-continuation-open" onClick={() => open(receiver.pageId, receiver.messageId)}>
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
