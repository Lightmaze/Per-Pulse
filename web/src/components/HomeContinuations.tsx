/** A quiet Home doorway back into verified local computation.
 *
 * Read-only World projection: never creates new user messages, model turns,
 * cross-Work transcript copies or automatic Home results.
 * Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
 */
import { useEffect, useState } from 'react';
import { api } from '../api';
import { homeContinuations } from '../homeContinuations';
import { useLanguage } from '../i18n';
import { pulseActivityLabel } from '../pulseActivityLabels';
import type { PageSummary, PulseState } from '../types';
import type { Navigate } from './SessionLink';

export function HomeContinuations({ pages, navigate }: {
  pages: PageSummary[];
  navigate: Navigate;
}) {
  const { t, pageTitle } = useLanguage();
  const [pulse, setPulse] = useState<PulseState | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    const refresh = async () => {
      if (reading || controller.signal.aborted) return;
      reading = true;
      try {
        const next = await api.pulse(controller.signal);
        if (!controller.signal.aborted) setPulse(next);
      } catch {
        // Home is usable without Pulse. Never display stale activity as current.
        if (!controller.signal.aborted) setPulse(null);
      } finally {
        reading = false;
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 12000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, []);

  const items = homeContinuations(pulse, pages);
  if (!items.length) return null; // Home stays calm when no verified local work exists.

  const titleOf = (id: string) => id === 'home' ? t('app.home')
    : pages.find(page => page.id === id)?.title ?? '';
  const open = (id: string, messageId?: string) => navigate(id, { pageId: 'home' }, messageId);

  return <section className="home-continuations" aria-label={t('pulse.home.title')}>
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
  </section>;
}
