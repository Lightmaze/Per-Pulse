import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage } from '../i18n';
import type { CollaboratorFeedbackScope, CollaboratorFeedbackView } from '../types';

type Proposal = { kind: 'save' | 'withdraw'; content: string; scope: CollaboratorFeedbackScope };

/** A local-Work human authorization, separate from rating or native RIM sampling. */
export function CollaboratorFeedbackEditor({ pageId, recordId }: { pageId: string; recordId: string }) {
  const { t, errorText } = useLanguage();
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<CollaboratorFeedbackView | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [content, setContent] = useState('');
  const [scope, setScope] = useState<CollaboratorFeedbackScope>('work');
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);
  const [needsReload, setNeedsReload] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const load = async () => {
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setReading(true);
    setProblem(null);
    try {
      const next = await api.collaboratorFeedback(pageId, recordId, pending.signal);
      if (!pending.signal.aborted) {
        setSaved(next);
        // This is an explicit discard-and-reload. Never combine a stale draft
        // with a newer revision number after a conflict or lost HTTP receipt.
        setRevision(next.revision);
        setContent(next.current?.content ?? '');
        setScope(next.current?.scope ?? 'work');
        setNeedsReload(false);
        setProposal(null);
      }
    } catch (error) {
      if (!pending.signal.aborted) setProblem(error);
    } finally {
      if (!pending.signal.aborted) setReading(false);
    }
  };

  const submit = async () => {
    if (!saved || revision === null || needsReload || !proposal) return;
    const { kind, content: note, scope: extent } = proposal;
    setSaving(true);
    setProblem(null);
    setProposal(null);
    try {
      const next = await api.updateCollaboratorFeedback(
        pageId, recordId, revision, note, extent, kind === 'withdraw',
      );
      setSaved(next);
      setRevision(next.revision);
      setContent(next.current?.content ?? '');
      setScope(next.current?.scope ?? 'work');
    } catch (error) {
      // An HTTP error can mean a CAS conflict or an unknown write receipt.
      // Do not blindly resend an effectful request using a newer revision.
      setProblem(error);
      setNeedsReload(true);
    } finally {
      setSaving(false);
    }
  };

  const current = saved?.current;
  const active = Boolean(current && !current.withdrawn);
  const changed = !active || content !== current?.content || scope !== current?.scope;
  const canSave = Boolean(saved && revision !== null && content.trim()
    && changed && !reading && !saving && !needsReload && revision < saved.max_revisions);
  const canWithdraw = Boolean(active && saved && revision !== null && !reading && !saving && !needsReload);

  return <div className="collaborator-feedback">
    <button type="button" className="text-button" aria-expanded={open}
      onClick={() => {
        if (open) controller.current?.abort();
        else if (!saved) void load();
        setOpen(value => !value);
      }}>{t('collaboration.feedbackTitle')}</button>
    {open ? <div>
      {reading ? <p role="status">{t('collaboration.feedbackLoading')}</p> : null}
      {problem ? <p role="alert">{errorText(problem)}</p> : null}
      {needsReload ? <p role="alert">{t('collaboration.feedbackConflict')}</p> : null}
      {saved ? <>
        <p className="artifact-intro" role="status">{t(
          current?.withdrawn ? 'collaboration.feedbackStatusWithdrawn'
            : current?.scope === 'collaborator' ? 'collaboration.feedbackStatusShared'
              : current ? 'collaboration.feedbackStatusLocal' : 'collaboration.feedbackStatusEmpty',
        )}</p>
        <label>
          <span>{t('collaboration.feedbackNote')}</span>
          <textarea rows={3} maxLength={2048} value={content} disabled={saving || needsReload}
            onChange={event => { setContent(event.target.value); setProposal(null); }} />
        </label>
        <label>
          <span>{t('collaboration.feedbackScope')}</span>
          <select value={scope} disabled={saving || needsReload}
            onChange={event => { setScope(event.target.value as CollaboratorFeedbackScope); setProposal(null); }}>
            <option value="work">{t('collaboration.feedbackWork')}</option>
            <option value="collaborator">{t('collaboration.feedbackShared')}</option>
          </select>
        </label>
        {proposal ? <div className="collaborator-rating">
          <p className="artifact-intro">{t(
            proposal.kind === 'withdraw' ? 'collaboration.feedbackConfirmWithdraw'
              : proposal.scope === 'collaborator' ? 'collaboration.feedbackConfirmShared'
                : 'collaboration.feedbackConfirmLocal',
          )}</p>
          <div className="collaborator-rating-actions">
            <button type="button" className="text-button" disabled={saving || needsReload}
              onClick={() => void submit()}>{t('collaboration.feedbackConfirm')}</button>
            <button type="button" className="text-button" disabled={saving}
              onClick={() => setProposal(null)}>{t('collaboration.feedbackCancel')}</button>
          </div>
        </div> : <div className="collaborator-rating-actions">
          <button type="button" className="text-button" disabled={!canSave}
            onClick={() => setProposal({ kind: 'save', content, scope })}>
            {t('collaboration.feedbackSave')}
          </button>
          {active ? <button type="button" className="text-button" disabled={!canWithdraw}
            onClick={() => setProposal({ kind: 'withdraw', content: current!.content, scope: current!.scope })}>
            {t('collaboration.feedbackWithdraw')}
          </button> : null}
        </div>}
        {saving ? <p role="status">{t('collaboration.feedbackSaving')}</p> : null}
        <p className="artifact-intro">{t('collaboration.feedbackLimit')}</p>
      </> : null}
      {!saving && !reading ? <button type="button" className="text-button"
        onClick={() => void load()}>{saved || needsReload
          ? t('collaboration.feedbackRefresh') : t('collaboration.feedbackLoad')}</button> : null}
    </div> : null}
  </div>;
}
