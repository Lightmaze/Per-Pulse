// Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
import { useEffect, useState } from 'react';
import { api } from '../api';
import type { LessonCondition, PostureExperience, PostureView } from '../types';
import { Dialog } from './Dialog';
import type { Navigate } from './SessionLink';

function describeCondition(value: LessonCondition): string {
  if (typeof value === 'string') return value;
  if ('not' in value) return `Except when: ${describeCondition(value.not)}`;
  const all = 'all' in value;
  const children = all ? value.all : value.any;
  return children.map(child => typeof child === 'string' ? child : `(${describeCondition(child)})`).join(all ? ' AND ' : ' OR ');
}

function Experience({ experience, busy, revise, feedback, navigate }: {
  experience: PostureExperience; busy: boolean;
  revise: (experience: PostureExperience, changes: { active?: boolean; scope?: 'context' | 'subject' }) => void;
  feedback: (experience: PostureExperience, verdict: 'useful' | 'harmful', evidence: string) => Promise<void>;
  navigate: Navigate;
}) {
  const [evidence, setEvidence] = useState('');
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const submit = async (verdict: 'useful' | 'harmful') => {
    await feedback(experience, verdict, evidence.trim());
    setEvidence(''); setFeedbackOpen(false);
  };
  return <article className={`lesson ${experience.active ? '' : 'lesson-paused'}`}>
    <div className="lesson-heading"><h3>{experience.title}</h3><span>{experience.active ? 'In use' : 'Paused'} · v{experience.revision}</span></div>
    <p>{experience.lesson}</p>
    <p className="lesson-scope">{experience.scope === 'subject' ? 'Available across your pages' : 'Used only in this page'}{experience.record_type === 'constructed_fixture' ? ' · Test experience' : ''}</p>
    <details><summary>When it applies</summary><p>{describeCondition(experience.conditions)}</p><h4>Where it should not apply</h4><ul>{experience.counterexamples.map((value, index) => <li key={index}>{value}</li>)}</ul><h4>What it changes</h4><dl><dt>Understanding</dt><dd>{experience.shift.understanding}</dd><dt>Possible actions</dt><dd>{experience.shift.candidates}</dd><dt>Ready to deliver</dt><dd>{experience.shift.delivery}</dd></dl></details>
    {experience.encounter ? <details><summary>Source experience</summary><dl><dt>Situation</dt><dd>{experience.encounter.situation}</dd><dt>What happened</dt><dd>{experience.encounter.action}</dd><dt>Feedback</dt><dd>{experience.encounter.outcome}</dd></dl>{experience.sourcePageId ? <div className="lesson-actions">
      {experience.source?.source_message_id ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!, undefined, experience.source!.source_message_id)}>Go to original result</button> : null}
      {experience.source?.feedback_message_id ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!, undefined, experience.source!.feedback_message_id)}>Go to correction</button> : null}
    </div> : null}</details> : null}
    {experience.feedback?.length ? <details><summary>Later feedback ({experience.feedback.length})</summary>{experience.feedback.map((entry, index) => <p key={index}><strong>{entry.verdict.replaceAll('_', ' ')}</strong> · {entry.evidence}</p>)}</details> : null}
    {experience.canEdit ? <div className="lesson-actions">
      <button className="secondary-button" disabled={busy} onClick={() => revise(experience, { active: !experience.active })}>{experience.active ? 'Pause lesson' : 'Use again'}</button>
      <button className="secondary-button" disabled={busy} onClick={() => revise(experience, { scope: experience.scope === 'context' ? 'subject' : 'context' })}>{experience.scope === 'context' ? 'Use in other pages' : 'Keep in this page'}</button>
      <button className="text-button" disabled={busy} onClick={() => setFeedbackOpen(value => !value)}>Give feedback</button>
    </div> : experience.sourcePageId ? <button className="text-button" onClick={() => navigate(experience.sourcePageId!)}>Open source page to edit</button> : null}
    {experience.scope === 'subject' && experience.canEdit ? <p className="lesson-note">The lesson and its conditions can travel. The original conversation stays here.</p> : null}
    {feedbackOpen ? <div className="lesson-feedback"><label className="field-label" htmlFor={`feedback-${experience.id}`}>What changed in the result?</label><textarea id={`feedback-${experience.id}`} rows={3} value={evidence} maxLength={1500} onChange={event => setEvidence(event.target.value)} placeholder="Describe the actual result, including where this lesson was unhelpful." /><div className="lesson-actions"><button className="secondary-button" disabled={busy || !evidence.trim()} onClick={() => void submit('useful').catch(() => {})}>It helped</button><button className="secondary-button" disabled={busy || !evidence.trim()} onClick={() => void submit('harmful').catch(() => {})}>It hurt · pause</button></div></div> : null}
  </article>;
}

export function LearningDialog({ pageId, pageTitle, close, navigate }: { pageId: string; pageTitle: string; close: () => void; navigate: Navigate }) {
  const [view, setView] = useState<PostureView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setView(null); setError('');
    api.posture(pageId, controller.signal).then(setView).catch(problem => {
      if (!controller.signal.aborted) setError(problem instanceof Error ? problem.message : 'Lessons could not be loaded.');
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
      setError(problem instanceof Error ? problem.message : 'The lesson could not be updated.');
      throw problem;
    } finally { setBusy(false); }
  };
  return <Dialog title="Lessons from the work" className="learning-dialog" onClose={() => { if (!busy) close(); }}>
    <p className="dialog-description">Experience available in {pageTitle}. Lessons guide judgment; their conditions and exceptions matter.</p>
    <button className="text-button" disabled={busy || (!view && !error)} onClick={() => setVersion(value => value + 1)}>Refresh lessons</button>
    {error ? <div className="form-error" role="alert">{error}</div> : null}
    {!view && !error ? <p role="status">Loading lessons…</p> : null}
    {view && !view.enabled ? <p>Experience learning is off. Start Per Pulse with <code>--posture</code> to enable it.</p> : null}
    {view?.enabled && !view.experiences.length ? <div className="lessons-empty"><h3>No saved lessons in this page yet.</h3><p>After a reply, explain what worked or what should change. Per Pulse can keep a reusable correction together with the result that prompted it.</p><p>A connected model is needed to learn from the conversation. You can inspect and pause saved lessons here.</p></div> : null}
    {view?.experiences.map(experience => <Experience key={experience.id} experience={experience} busy={busy} navigate={navigate}
      revise={(item, changes) => { void update(() => api.reviseExperience(pageId, item, changes)).catch(() => {}); }}
      feedback={(item, verdict, evidence) => update(() => api.experienceFeedback(pageId, item, verdict, evidence))} />)}
  </Dialog>;
}
