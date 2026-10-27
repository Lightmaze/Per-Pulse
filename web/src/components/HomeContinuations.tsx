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
    let reading = false;
    const refresh = async () => {
      if (reading || controller.signal.aborted) return;
      reading = true;
      try {
        // The two read-only projections fail independently. Native Work status
        // is meaningful even when Pulse is disabled or unavailable.
        const [pulseRead, workspaceRead] = await Promise.allSettled([
          api.pulse(controller.signal),
          api.bootstrap(controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setSnapshot({
          workspaceId,
          pulse: pulseRead.status === 'fulfilled' ? pulseRead.value : null,
          pages: workspaceRead.status === 'fulfilled'
            && workspaceRead.value.workspaceId === workspaceId
            ? workspaceRead.value.pages : null,
        });
      } finally {
        reading = false;
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 12000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [workspaceId]);

  const currentPages = freshPages ?? pages;
  const items = homeContinuations(pulse, currentPages);
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
