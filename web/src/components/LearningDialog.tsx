// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage, type Translate } from '../i18n';
import type { LessonCondition, LessonVerdict, Message, PostureExperience, PostureView } from '../types';
import { Dialog } from './Dialog';
import type { Navigate } from './SessionLink';

function describeCondition(value: LessonCondition, t: Translate): string {
  if (typeof value === 'string') return value;
  if ('not' in value) return t('learning.except', { condition: describeCondition(value.not, t) });
  const all = 'all' in value;
  const children = all ? value.all : value.any;
  return children.map(child => typeof child === 'string' ? child : `(${describeCondition(child, t)})`).join(t(all ? 'learning.all' : 'learning.any'));
}

type LessonRevision = { active?: boolean; scope?: 'context' | 'subject'; lesson?: string; counterexamples?: string[]; shift?: PostureExperience['shift'] };

function Experience({ experience, pageId, results, busy, revise, feedback, navigate }: {
  experience: PostureExperience; pageId: string; busy: boolean;
  results: Message[];
  revise: (experience: PostureExperience, changes: LessonRevision, evidence?: string) => Promise<void>;
  feedback: (experience: PostureExperience, verdict: LessonVerdict, evidence: string, resultMessageId?: string) => Promise<void>;
  navigate: Navigate;
}) {
  const { t } = useLanguage();
  const [evidence, setEvidence] = useState('');
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [resultMessageId, setResultMessageId] = useState('');
  const [refineOpen, setRefineOpen] = useState(false);
  const [lessonDraft, setLessonDraft] = useState(experience.lesson);
  const [exceptionDraft, setExceptionDraft] = useState(experience.counterexamples.join('\n'));
  const [shiftDraft, setShiftDraft] = useState<PostureExperience['shift']>({ ...experience.shift });
  const [refineEvidence, setRefineEvidence] = useState('');
  const refinedLesson = lessonDraft.trim();
  const refinedExceptions = exceptionDraft.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const refinedShift = {
    understanding: shiftDraft.understanding.trim(),
    candidates: shiftDraft.candidates.trim(),
    delivery: shiftDraft.delivery.trim(),
  };
  const validRefinement = Boolean(refinedLesson) && refinedLesson.length <= 4000
    && refinedExceptions.length >= 1 && refinedExceptions.length <= 8
    && refinedExceptions.every(value => value.length <= 2000)
    && Object.values(refinedShift).every(value => value.length > 0 && value.length <= 2400);
  const changedRefinement = refinedLesson !== experience.lesson
    || JSON.stringify(refinedExceptions) !== JSON.stringify(experience.counterexamples)
    || Object.keys(refinedShift).some(key => refinedShift[key as keyof typeof refinedShift] !== experience.shift[key as keyof typeof refinedShift]);
  const beginRefinement = () => {
    setLessonDraft(experience.lesson);
    setExceptionDraft(experience.counterexamples.join('\n'));
    setShiftDraft({ ...experience.shift });
    setRefineEvidence('');
    setRefineOpen(true);
  };
  const submitRefinement = async () => {
    await revise(experience, { lesson: refinedLesson, counterexamples: refinedExceptions, shift: refinedShift }, refineEvidence.trim() || undefined);
    setRefineEvidence('');
    setRefineOpen(false);
  };
  const originalId = experience.sourceAnchor?.resultMessageId ?? experience.source?.source_message_id;
  const correctionId = experience.sourceAnchor?.feedbackMessageId ?? experience.source?.feedback_message_id;
  // Inspect the original Work without losing the receiving Work's place or draft.
  // Same-page source links need no new return origin.
  const sourceOrigin = experience.sourcePageId && experience.sourcePageId !== pageId ? { pageId } : undefined;
  const submit = async (verdict: LessonVerdict) => {
    await feedback(experience, verdict, evidence.trim(), resultMessageId || undefined);
    setEvidence(''); setResultMessageId(''); setFeedbackOpen(false);
  };
  return <article className={`lesson ${experience.active ? '' : 'lesson-paused'}`}>
    <div className="lesson-heading"><h3>{experience.title}</h3><span>{t(experience.active ? 'learning.active' : 'learning.paused')} · v{experience.revision}</span></div>
    <p>{experience.lesson}</p>
    <p className="lesson-scope">{t(experience.scope === 'subject' ? 'learning.shared' : 'learning.local')}{experience.record_type === 'constructed_fixture' ? ` · ${t('learning.fixture')}` : ''}</p>
    <details><summary>{t('learning.when')}</summary><p>{describeCondition(experience.conditions, t)}</p><h4>{t('learning.exclude')}</h4><ul>{experience.counterexamples.map((value, index) => <li key={index}>{value}</li>)}</ul><h4>{t('learning.changes')}</h4><dl><dt>{t('learning.understanding')}</dt><dd>{experience.shift.understanding}</dd><dt>{t('learning.actions')}</dt><dd>{experience.shift.candidates}</dd><dt>{t('learning.delivery')}</dt><dd>{experience.shift.delivery}</dd></dl></details>
    {(experience.encounter || (experience.sourcePageId && (originalId || correctionId))) ? <details>
      <summary>{t('learning.source')}</summary>
      {experience.encounter ? <dl><dt>{t('learning.situation')}</dt><dd>{experience.encounter.situation}</dd><dt>{t('learning.encounterAction')}</dt><dd>{experience.encounter.action}</dd><dt>{t('learning.outcome')}</dt><dd>{experience.encounter.outcome}</dd></dl> : <p className="lesson-scope">{t('learning.sourcePrivate')}</p>}
      {experience.sourcePageId ? <div className="lesson-actions">
        {originalId ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!, sourceOrigin, originalId)}>{t('learning.original')}</button> : null}
        {correctionId ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!, sourceOrigin, correctionId)}>{t('learning.correction')}</button> : null}
      </div> : null}
    </details> : null}
    {experience.feedback?.length ? <details><summary>{t('learning.later', { count: experience.feedback.length })}</summary>{experience.feedback.map((entry, index) => {
      // Only original-owner feedback includes the private report source. A
      // receiving Work never receives this history or these navigation links.
      const reportedPage = entry.source?.kind === 'operator_report' && entry.source.relation === 'shared_recipient'
        && entry.source.page_id && entry.source.page_id !== pageId ? entry.source.page_id : null;
      const resultPage = entry.source?.kind === 'operator_report' && entry.source.result_message_id
        ? entry.source.page_id : null;
      return <p key={index}><strong>{entry.verdict === 'useful' ? t('learning.verdictUseful') : entry.verdict === 'not_useful' ? t('learning.verdictNotUseful') : entry.verdict === 'harmful' ? t('learning.verdictHarmful') : entry.verdict === 'revised' ? t('learning.verdictRevised') : entry.verdict.replaceAll('_', ' ')}</strong> · {entry.evidence}
        {resultPage ? <> · <button type="button" className="text-button" onClick={() => navigate(resultPage, resultPage !== pageId ? { pageId } : undefined, entry.source!.result_message_id)}>{t('learning.reportedResult')}</button>
          {['TEST_DOUBLE', 'EXPLICIT_MOCK'].includes(entry.source?.result_evidence_class ?? '') ? <> · {t('learning.resultFixture')}</> : null}
        </> : reportedPage ? <> · <button type="button" className="text-button" onClick={() => navigate(reportedPage, { pageId })}>{t('learning.reportedWork')}</button></> : null}
      </p>;
    })}</details> : null}
    {experience.canEdit ? <div className="lesson-actions">
      <button className="secondary-button" disabled={busy} onClick={() => { void revise(experience, { active: !experience.active }).catch(() => {}); }}>{t(experience.active ? 'learning.pause' : 'learning.resume')}</button>
      <button className="secondary-button" disabled={busy} onClick={() => { void revise(experience, { scope: experience.scope === 'context' ? 'subject' : 'context' }).catch(() => {}); }}>{t(experience.scope === 'context' ? 'learning.share' : 'learning.keepLocal')}</button>
      <button className="text-button" disabled={busy} onClick={beginRefinement}>{t('learning.refine')}</button>
    </div> : experience.sourcePageId && !experience.sourceAnchor ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!, sourceOrigin)}>{t('learning.editSource')}</button> : null}
    {experience.canReport ? <div className="lesson-actions"><button type="button" className="text-button" disabled={busy} onClick={() => setFeedbackOpen(value => !value)}>{t('learning.giveFeedback')}</button></div> : null}
    {refineOpen && experience.canEdit ? <div className="lesson-feedback">
      <p className="lesson-note">{t('learning.refineHelp')}</p>
      <label className="field-label" htmlFor={`refine-lesson-${experience.id}`}>{t('learning.refineLesson')}</label>
      <textarea id={`refine-lesson-${experience.id}`} rows={3} value={lessonDraft} maxLength={4000} disabled={busy} onChange={event => setLessonDraft(event.target.value)} />
      <label className="field-label" htmlFor={`refine-exceptions-${experience.id}`}>{t('learning.refineExceptions')}</label>
      <textarea id={`refine-exceptions-${experience.id}`} rows={3} value={exceptionDraft} maxLength={16000} disabled={busy} onChange={event => setExceptionDraft(event.target.value)} />
      <details className="lesson-refinement-shift">
        <summary>{t('learning.refineEffect')}</summary>
        <p className="lesson-note">{t('learning.refineEffectHelp')}</p>
        {(['understanding', 'candidates', 'delivery'] as const).map(field => <div key={field}>
          <label className="field-label" htmlFor={`refine-${field}-${experience.id}`}>
            {t(field === 'understanding' ? 'learning.understanding' : field === 'candidates' ? 'learning.actions' : 'learning.delivery')}
          </label>
          <textarea id={`refine-${field}-${experience.id}`} rows={2} value={shiftDraft[field]} maxLength={2400}
            disabled={busy} onChange={event => setShiftDraft(current => ({ ...current, [field]: event.target.value }))} />
        </div>)}
      </details>
      <label className="field-label" htmlFor={`refine-evidence-${experience.id}`}>{t('learning.refineEvidence')}</label>
      <textarea id={`refine-evidence-${experience.id}`} rows={2} value={refineEvidence} maxLength={1500} disabled={busy} onChange={event => setRefineEvidence(event.target.value)} placeholder={t('learning.refineEvidenceHint')} />
      <div className="lesson-actions">
        <button className="secondary-button" disabled={busy || !validRefinement || !changedRefinement} onClick={() => { void submitRefinement().catch(() => {}); }}>{t('learning.saveRefinement')}</button>
        <button className="text-button" disabled={busy} onClick={() => setRefineOpen(false)}>{t('learning.cancelRefinement')}</button>
      </div>
    </div> : null}
    {experience.scope === 'subject' && experience.canEdit ? <p className="lesson-note">{t('learning.sharingNote')}</p> : null}
    {feedbackOpen && experience.canReport ? <div className="lesson-feedback">
      {!experience.canEdit ? <p className="lesson-note">{t('learning.receivingFeedbackNote')}</p> : null}
      <label className="field-label" htmlFor={`feedback-${experience.id}`}>{t('learning.changed')}</label>
      <textarea id={`feedback-${experience.id}`} rows={3} value={evidence} maxLength={1500} disabled={busy} onChange={event => setEvidence(event.target.value)} placeholder={t('learning.feedbackPlaceholder')} />
      <label className="field-label" htmlFor={`feedback-result-${experience.id}`}>{t('learning.selectResult')}</label>
      <select id={`feedback-result-${experience.id}`} value={resultMessageId} disabled={busy} aria-describedby={`feedback-result-help-${experience.id}`} onChange={event => setResultMessageId(event.target.value)}>
        <option value="">{t('learning.noResult')}</option>
        {results.map((message, index) => <option key={message.id} value={message.id}>{index + 1}. {message.content.replace(/\s+/g, ' ').slice(0, 120)}</option>)}
      </select>
      <p className="lesson-note" id={`feedback-result-help-${experience.id}`}>{t('learning.resultHelp')}</p>
      <div className="lesson-actions">
        <button className="secondary-button" disabled={busy || !evidence.trim()} onClick={() => void submit('useful').catch(() => {})}>{t('learning.helped')}</button>
        <button className="secondary-button" disabled={busy || !evidence.trim()} onClick={() => void submit('not_useful').catch(() => {})}>{t('learning.noBenefit')}</button>
        <button className="secondary-button" disabled={busy || !evidence.trim()} onClick={() => void submit('harmful').catch(() => {})}>{t('learning.hurt')}</button>
      </div>
    </div> : null}
  </article>;
}

export function LearningDialog({ pageId, pageTitle, messages, close, navigate }: { pageId: string; pageTitle: string; messages: Message[]; close: () => void; navigate: Navigate }) {
  const { t } = useLanguage();
  const [view, setView] = useState<PostureView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const writing = useRef(false);
  const [refreshing, setRefreshing] = useState(true);
  const [version, setVersion] = useState(0);
  const [search, setSearch] = useState('');
  const results = messages.filter(message => message.role === 'assistant' && message.status === 'settled');
  // Search only the shareable abstraction, never private source encounters or feedback.
  const needle = search.trim().toLocaleLowerCase();
  const visibleExperiences = (view?.experiences ?? []).filter(item => !needle || [
    item.title, item.lesson, JSON.stringify(item.conditions), ...item.counterexamples,
    item.shift.understanding, item.shift.candidates, item.shift.delivery,
  ].some(value => value.toLocaleLowerCase().includes(needle)));
  useEffect(() => {
    const controller = new AbortController();
    // Keep mounted editors while refreshing after a version conflict. Their
    // typed report and selected result still belong to this page. App keys this
    // dialog by World/page, so neither drafts nor late writes cross contexts.
    setRefreshing(true); setError('');
    api.posture(pageId, controller.signal).then(next => {
      if (!controller.signal.aborted) setView(next);
    }).catch(problem => {
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem.message : t('learning.readError'));
    }).finally(() => { if (!controller.signal.aborted) setRefreshing(false); });
    return () => controller.abort();
  }, [pageId, version]);

  const update = async (operation: () => Promise<{ experience: PostureExperience }>) => {
    if (writing.current || refreshing) throw new Error('Wait for the current lesson request to finish.');
    writing.current = true;
    setBusy(true); setError('');
    try {
      const response = await operation();
      setView(current => current ? { ...current, experiences: current.experiences.map(item => item.id === response.experience.id ? response.experience : item) } : current);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : t('learning.updateError'));
      throw problem;
    } finally { writing.current = false; setBusy(false); }
  };
  return <Dialog title={t('learning.title')} className="learning-dialog" onClose={() => { if (!busy) close(); }}>
    <p className="dialog-description">{t('learning.description', { page: pageTitle })}</p>
    <button className="text-button" disabled={busy || refreshing} onClick={() => { setRefreshing(true); setVersion(value => value + 1); }}>{t('learning.refresh')}</button>
    {error ? <div className="form-error" role="alert">{error}</div> : null}
    {refreshing ? <p role="status">{t('learning.loading')}</p> : null}
    {view && !view.enabled ? <p>{t('learning.disabled')}</p> : null}
    {view?.enabled && !view.experiences.length ? <div className="lessons-empty"><h3>{t('learning.emptyTitle')}</h3><p>{t('learning.emptyHelp')}</p><p>{t('learning.emptyModel')}</p></div> : null}
    {view?.enabled && view.experiences.length > 0 ? <div className="lesson-search">
      <label className="field-label" htmlFor="lesson-search">{t('learning.search')}</label>
      <input id="lesson-search" type="search" autoComplete="off" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('learning.searchHint')} />
    </div> : null}
    {view?.enabled && view.experiences.length > 0 && !visibleExperiences.length ? <p className="lesson-note" role="status">{t('learning.noMatches')}</p> : null}
    {visibleExperiences.map(experience => <Experience key={experience.id} experience={experience} pageId={pageId} results={results} busy={busy || refreshing} navigate={navigate}
      revise={(item, changes, evidence) => update(() => api.reviseExperience(pageId, item, changes, evidence))}
      feedback={(item, verdict, evidence, resultMessageId) => update(() => api.experienceFeedback(pageId, item, verdict, evidence, resultMessageId))} />)}
  </Dialog>;
}
