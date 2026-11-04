import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { AdaptiveWorkChoices, CollaboratorOutcome, PageSummary } from '../types';
import { historicSourceRating, visibleRatedSourceWork } from '../collaborationEvidence';
import { SessionLink, type Navigate } from './SessionLink';
import { CollaboratorFeedbackEditor } from './CollaboratorFeedbackEditor';

/** A read of an executed collaborator choice, not a live suggestion or reward. */
export function CollaboratorChoices({ pageId, pages, navigate }: { pageId: string; pages: PageSummary[]; navigate: Navigate }) {
  const { t, pageTitle, errorText } = useLanguage();
  const [expanded, setExpanded] = useState(false);
  const [reading, setReading] = useState(false);
  const [choices, setChoices] = useState<AdaptiveWorkChoices | null>(null);
  const [problem, setProblem] = useState<unknown>(null);
  const [proposal, setProposal] = useState<{ recordId: string; outcome: CollaboratorOutcome } | null>(null);
  const [submitting, setSubmitting] = useState<string | null>(null);
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

  const submit = async (recordId: string, outcome: CollaboratorOutcome) => {
    setSubmitting(recordId);
    setProblem(null);
    try {
      await api.rateAdaptiveChoice(pageId, recordId, outcome);
      setProposal(null);
      await load();
    } catch (error) {
      setProblem(error);
    } finally {
      setSubmitting(null);
    }
  };

  const renderAssessment = (entry: { recordId: string; resultMessageId: string | null; outcome: CollaboratorOutcome | null }) => (
    <>{entry.outcome ? <p className="artifact-intro" role="status">{
            t(entry.outcome === 'adopted' ? 'collaboration.ratedAdopted'
              : entry.outcome === 'revised' ? 'collaboration.ratedRevised' : 'collaboration.ratedDiscarded')
          }</p> : entry.resultMessageId ? <div className="collaborator-rating">
            <p className="artifact-intro">{t('collaboration.ratingQuestion')}</p>
            {proposal?.recordId === entry.recordId
              ? <div>
                <p className="artifact-intro">{t('collaboration.confirmImmutable')}</p>
                <div className="collaborator-rating-actions">
                  <button type="button" className="text-button" disabled={submitting !== null}
                    onClick={() => void submit(entry.recordId, proposal.outcome)}>{t('collaboration.confirm')}</button>
                  <button type="button" className="text-button" disabled={submitting !== null}
                    onClick={() => setProposal(null)}>{t('collaboration.cancel')}</button>
                </div>
              </div>
              : <div className="collaborator-rating-actions" role="group"
                  aria-label={t('collaboration.ratingQuestion')}>
                {(['adopted', 'revised', 'discarded'] as const).map(outcome =>
                  <button type="button" className="text-button" key={outcome} disabled={submitting !== null}
                    onClick={() => setProposal({ recordId: entry.recordId, outcome })}>
                    {t(outcome === 'adopted' ? 'collaboration.rateAdopted'
                      : outcome === 'revised' ? 'collaboration.rateRevised' : 'collaboration.rateDiscarded')}
                  </button>)}
              </div>}
            {submitting === entry.recordId ? <p role="status">{t('collaboration.saving')}</p> : null}
          </div> : null}</>
  );

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
      {!reading && !problem && !choices?.choices.length && !choices?.guidedConsultations.length
        ? <p>{t('collaboration.empty')}</p> : null}
      {choices?.guidedConsultations.length ? <h4>{t('collaboration.guidedTitle')}</h4> : null}
      {choices?.guidedConsultations.map(consultation => <article key={consultation.recordId}
        className="collaborator-choice">
        <p><strong>{t('collaboration.guidedConsult', { expert: consultation.selectedName })}</strong></p>
        {consultation.resultMessageId ? <SessionLink pageId={pageId}
          label={t('collaboration.result')} origin={{ pageId }} navigate={navigate}
          targetMessageId={consultation.resultMessageId} /> : null}
        {renderAssessment(consultation)}
        {consultation.resultMessageId ? <CollaboratorFeedbackEditor
          key={`${pageId}/${consultation.recordId}`}
          pageId={pageId} recordId={consultation.recordId} /> : null}
        <details><summary>{t('collaboration.guidedEvidence', { count: consultation.guidance.length })}</summary>
          <ul>{consultation.guidance.map((note, index) => {
            const originalWork = visibleRatedSourceWork(pages, note.sourceWorkId, note.sourceMessageId);
            return <li key={note.sourceRecordId}>
              {originalWork ? <SessionLink pageId={originalWork.id}
                label={t('collaboration.guidedSource', { number: index + 1 }) + ` · ${pageTitle(originalWork)}`}
                origin={{ pageId }} navigate={navigate} targetMessageId={note.sourceMessageId!} />
                : t('collaboration.sourceUnavailable')}
              <p className="artifact-intro">{note.content}</p>
            </li>;
          })}</ul>
        </details>
      </article>)}
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
          {renderAssessment(choice)}

          {choice.resultMessageId ? <CollaboratorFeedbackEditor key={`${pageId}/${choice.recordId}`}
            pageId={pageId} recordId={choice.recordId} /> : null}
          {choice.evidence.length ? <details>
            <summary>{t('collaboration.evidence', { count: choice.evidence.length })}</summary>
            <ul>{choice.evidence.map((item, index) => {
              const originalWork = visibleRatedSourceWork(pages, item.sourceWorkId, item.sourceMessageId);
              const sourceLabel = t('collaboration.source', { number: index + 1 })
                + (originalWork ? ` · ${pageTitle(originalWork)}` : '');
              const rating = historicSourceRating(item.outcomeProxy);
              return <li key={item.recordId}>
                {originalWork && item.sourceMessageId ? <SessionLink pageId={originalWork.id}
                  label={sourceLabel} origin={{ pageId }} navigate={navigate}
                  targetMessageId={item.sourceMessageId} />
                  : t('collaboration.sourceUnavailable')}
                {' · '}<span className="artifact-intro">{t(
                  rating === 'adopted' ? 'collaboration.sourceRatedAdopted'
                    : rating === 'revised' ? 'collaboration.sourceRatedRevised'
                      : rating === 'discarded' ? 'collaboration.sourceRatedDiscarded'
                        : 'collaboration.sourceRatingUnknown',
                )}</span>
              </li>;
            })}</ul>
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
