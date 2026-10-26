// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useEffect, useState } from 'react';
import { api } from '../api';
import { useLanguage, type Translate } from '../i18n';
import type { LessonCondition, PostureExperience, PostureView } from '../types';
import { Dialog } from './Dialog';
import type { Navigate } from './SessionLink';

function describeCondition(value: LessonCondition, t: Translate): string {
  if (typeof value === 'string') return value;
  if ('not' in value) return t('learning.except', { condition: describeCondition(value.not, t) });
  const all = 'all' in value;
  const children = all ? value.all : value.any;
  return children.map(child => typeof child === 'string' ? child : `(${describeCondition(child, t)})`).join(t(all ? 'learning.all' : 'learning.any'));
}

function Experience({ experience, busy, revise, feedback, navigate }: {
  experience: PostureExperience; busy: boolean;
  revise: (experience: PostureExperience, changes: { active?: boolean; scope?: 'context' | 'subject' }) => void;
  feedback: (experience: PostureExperience, verdict: 'useful' | 'harmful', evidence: string) => Promise<void>;
  navigate: Navigate;
}) {
  const { t } = useLanguage();
  const [evidence, setEvidence] = useState('');
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const submit = async (verdict: 'useful' | 'harmful') => {
    await feedback(experience, verdict, evidence.trim());
    setEvidence(''); setFeedbackOpen(false);
  };
  return <article className={`lesson ${experience.active ? '' : 'lesson-paused'}`}>
    <div className="lesson-heading"><h3>{experience.title}</h3><span>{t(experience.active ? 'learning.active' : 'learning.paused')} · v{experience.revision}</span></div>
    <p>{experience.lesson}</p>
    <p className="lesson-scope">{t(experience.scope === 'subject' ? 'learning.shared' : 'learning.local')}{experience.record_type === 'constructed_fixture' ? ` · ${t('learning.fixture')}` : ''}</p>
    <details><summary>{t('learning.when')}</summary><p>{describeCondition(experience.conditions, t)}</p><h4>{t('learning.exclude')}</h4><ul>{experience.counterexamples.map((value, index) => <li key={index}>{value}</li>)}</ul><h4>{t('learning.changes')}</h4><dl><dt>{t('learning.understanding')}</dt><dd>{experience.shift.understanding}</dd><dt>{t('learning.actions')}</dt><dd>{experience.shift.candidates}</dd><dt>{t('learning.delivery')}</dt><dd>{experience.shift.delivery}</dd></dl></details>
    {experience.encounter ? <details><summary>{t('learning.source')}</summary><dl><dt>{t('learning.situation')}</dt><dd>{experience.encounter.situation}</dd><dt>{t('learning.encounterAction')}</dt><dd>{experience.encounter.action}</dd><dt>{t('learning.outcome')}</dt><dd>{experience.encounter.outcome}</dd></dl>{experience.sourcePageId ? <div className="lesson-actions">
      {experience.source?.source_message_id ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!, undefined, experience.source!.source_message_id)}>{t('learning.original')}</button> : null}
      {experience.source?.feedback_message_id ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!, undefined, experience.source!.feedback_message_id)}>{t('learning.correction')}</button> : null}
    </div> : null}</details> : null}
    {experience.feedback?.length ? <details><summary>{t('learning.later', { count: experience.feedback.length })}</summary>{experience.feedback.map((entry, index) => <p key={index}><strong>{entry.verdict === 'useful' ? t('learning.verdictUseful') : entry.verdict === 'harmful' ? t('learning.verdictHarmful') : entry.verdict.replaceAll('_', ' ')}</strong> · {entry.evidence}</p>)}</details> : null}
    {experience.canEdit ? <div className="lesson-actions">
      <button className="secondary-button" disabled={busy} onClick={() => revise(experience, { active: !experience.active })}>{t(experience.active ? 'learning.pause' : 'learning.resume')}</button>
      <button className="secondary-button" disabled={busy} onClick={() => revise(experience, { scope: experience.scope === 'context' ? 'subject' : 'context' })}>{t(experience.scope === 'context' ? 'learning.share' : 'learning.keepLocal')}</button>
      <button className="text-button" disabled={busy} onClick={() => setFeedbackOpen(value => !value)}>{t('learning.giveFeedback')}</button>
    </div> : experience.sourcePageId ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!)}>{t('learning.editSource')}</button> : null}
    {experience.scope === 'subject' && experience.canEdit ? <p className="lesson-note">{t('learning.sharingNote')}</p> : null}
    {feedbackOpen ? <div className="lesson-feedback"><label className="field-label" htmlFor={`feedback-${experience.id}`}>{t('learning.changed')}</label><textarea id={`feedback-${experience.id}`} rows={3} value={evidence} maxLength={1500} onChange={event => setEvidence(event.target.value)} placeholder={t('learning.feedbackPlaceholder')} /><div className="lesson-actions"><button className="secondary-button" disabled={busy || !evidence.trim()} onClick={() => void submit('useful').catch(() => {})}>{t('learning.helped')}</button><button className="secondary-button" disabled={busy || !evidence.trim()} onClick={() => void submit('harmful').catch(() => {})}>{t('learning.hurt')}</button></div></div> : null}
  </article>;
}

export function LearningDialog({ pageId, pageTitle, close, navigate }: { pageId: string; pageTitle: string; close: () => void; navigate: Navigate }) {
  const { t } = useLanguage();
  const [view, setView] = useState<PostureView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setView(null); setError('');
    api.posture(pageId, controller.signal).then(setView).catch(problem => {
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem.message : t('learning.readError'));
    });
    return () => controller.abort();
  }, [pageId, version]);

  const update = async (operation: () => Promise<{ experience: PostureExperience }>) => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const response = await operation();
      setView(current => current ? { ...current, experiences: current.experiences.map(item => item.id === response.experience.id ? response.experience : item) } : current);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : t('learning.updateError'));
      throw problem;
    } finally { setBusy(false); }
  };
  return <Dialog title={t('learning.title')} className="learning-dialog" onClose={() => { if (!busy) close(); }}>
    <p className="dialog-description">{t('learning.description', { page: pageTitle })}</p>
    <button className="text-button" disabled={busy || (!view && !error)} onClick={() => setVersion(value => value + 1)}>{t('learning.refresh')}</button>
    {error ? <div className="form-error" role="alert">{error}</div> : null}
    {!view && !error ? <p role="status">{t('learning.loading')}</p> : null}
    {view && !view.enabled ? <p>{t('learning.disabled')}</p> : null}
    {view?.enabled && !view.experiences.length ? <div className="lessons-empty"><h3>{t('learning.emptyTitle')}</h3><p>{t('learning.emptyHelp')}</p><p>{t('learning.emptyModel')}</p></div> : null}
    {view?.experiences.map(experience => <Experience key={experience.id} experience={experience} busy={busy} navigate={navigate}
      revise={(item, changes) => { void update(() => api.reviseExperience(pageId, item, changes)).catch(() => {}); }}
      feedback={(item, verdict, evidence) => update(() => api.experienceFeedback(pageId, item, verdict, evidence))} />)}
  </Dialog>;
}
