import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { AdaptiveWorkChoices } from '../types';
import { SessionLink, type Navigate } from './SessionLink';

/** A read of an executed collaborator choice, not a live suggestion or reward. */
export function CollaboratorChoices({ pageId, navigate }: { pageId: string; navigate: Navigate }) {
  const { t, errorText } = useLanguage();
  const [expanded, setExpanded] = useState(false);
  const [reading, setReading] = useState(false);
  const [choices, setChoices] = useState<AdaptiveWorkChoices | null>(null);
  const [problem, setProblem] = useState<unknown>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const load = async () => {
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setReading(true);
    setProblem(null);
    try {
      const next = await api.adaptiveChoices(pageId, pending.signal);
      if (!pending.signal.aborted) setChoices(next);
    } catch (error) {
      if (!pending.signal.aborted) setProblem(error);
    } finally {
      if (!pending.signal.aborted) setReading(false);
    }
  };

  return <section className="collaborator-choices">
    <button type="button" className="text-button" aria-expanded={expanded}
      onClick={() => {
        if (!expanded && !choices) void load();
        setExpanded(value => !value);
      }}>{t('collaboration.title')}</button>
    {expanded ? <div className="collaborator-choice-list">
      <p className="artifact-intro">{t('collaboration.description')}</p>
      {reading ? <p role="status">{t('collaboration.loading')}</p> : null}
      {problem ? <p role="alert">{errorText(problem)}</p> : null}
      {!reading && !problem && !choices?.choices.length ? <p>{t('collaboration.empty')}</p> : null}
      {choices?.choices.map(choice => {
        const selected = choice.candidates.find(candidate => candidate.expertId === choice.selectedExpertId);
        return <article key={choice.recordId} className="collaborator-choice">
          <p><strong>{t('collaboration.selected', { expert: choice.selectedName })}</strong>
            {selected ? <> · {t('collaboration.reference', { percentage: (selected.referenceWeight * 100).toFixed(1) })}
              {' · '}{t('collaboration.weight', { percentage: (selected.choiceWeight * 100).toFixed(1) })}</> : null}
          </p>
          <p className="artifact-intro">{t(choice.changedFromReference ? 'collaboration.adjusted' : 'collaboration.unchanged')}</p>
          {choice.resultMessageId ? <SessionLink pageId={pageId} label={t('collaboration.result')}
            origin={{ pageId }} navigate={navigate} targetMessageId={choice.resultMessageId} /> : null}
          {choice.evidence.length ? <details>
            <summary>{t('collaboration.evidence', { count: choice.evidence.length })}</summary>
            <ul>{choice.evidence.map((item, index) => <li key={item.recordId}>
              {item.sourceMessageId ? <SessionLink pageId={pageId}
                label={t('collaboration.source', { number: index + 1 })}
                origin={{ pageId }} navigate={navigate} targetMessageId={item.sourceMessageId} />
                : t('collaboration.sourceUnavailable')}
            </li>)}</ul>
          </details> : <p className="artifact-intro">{t('collaboration.referenceOnly')}</p>}
          {choice.historyIncomplete ? <p className="artifact-intro">{t('collaboration.incomplete')}</p> : null}
        </article>;
      })}
      {choices?.historyMayBeIncomplete ? <p className="artifact-intro">{t('collaboration.incomplete')}</p> : null}
      {!reading ? <button type="button" className="text-button" onClick={() => void load()}>
        {t('collaboration.refresh')}</button> : null}
      <p className="artifact-intro">{t('collaboration.limit')}</p>
    </div> : null}
  </section>;
}
