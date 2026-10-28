import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { useLanguage, type Translate } from '../i18n';
import { ExecutionResponseGate } from '../executionResponseGate';
import type { ActionDetails, PageView } from '../types';

function actionTitle(action: ActionDetails, t: Translate): string {
  if (['mutate_file', 'apply_patch'].includes(action.toolName)) return t('execution.reviewFile');
  if (action.toolName === 'run_command') return t('execution.reviewCommand');
  if (action.toolName === 'work') return t('execution.reviewWork');
  return t('execution.reviewOther', { tool: action.toolName.replaceAll('_', ' ') });
}

export function ExecutionStatus({ view, applyView }: { view: PageView; applyView: (view: PageView) => void }) {
  const { t } = useLanguage();
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const pageId = view.page.id;
  const requestGate = useRef(new ExecutionResponseGate(pageId));
  // Fence the previous Work synchronously, before its in-flight action returns.
  requestGate.current.forPage(pageId);
  useEffect(() => {
    setError(''); setBusy(false); setStopping(false);
    return () => requestGate.current.invalidate();
  }, [pageId]);
  const execution = view.execution;
  const showCurrent = !!execution && (!!error || execution.canCancel || execution.pendingActions.length > 0 || execution.recoveryRequired || execution.status === 'stopped');
  if (!execution || (!showCurrent && !execution.uncertainActions.length)) return null;

  const act = async (operation: () => Promise<{ view: PageView; continuationError?: { message: string } | null }>, stop = false) => {
    if (stop ? stopping : busy || stopping) return;
    const ticket = requestGate.current.begin(pageId);
    if (ticket === null) return;
    const isCurrent = () => requestGate.current.accepts(pageId, ticket);
    if (stop) setStopping(true); else setBusy(true);
    setError('');
    try {
      const result = await operation();
      // A later Stop or page transition wins over an earlier Allow response.
      if (isCurrent()) {
        applyView(result.view);
        if (result.continuationError) setError(result.continuationError.message);
      }
    } catch (problem) {
      if (isCurrent()) {
        setError(problem instanceof Error ? problem.message : t('execution.error'));
        // Unknown outcome: read the native state once, never retry a mutation.
        // The recovery GET itself can become stale while a new action runs.
        try {
          const restored = await api.page(pageId);
          if (isCurrent()) applyView(restored);
        } catch { /* Preserve existing work and the original error. */ }
      }
    } finally {
      if (isCurrent()) {
        setBusy(false);
        setStopping(false);
      }
    }
  };

  return <section className="execution-status" aria-label={t('execution.working')} aria-busy={busy || stopping}>
    {showCurrent ? <div className="execution-heading">
      <div>
        <strong>{execution.pendingActions.length ? t('execution.approval') : error || execution.recoveryRequired || execution.status === 'needs_attention' ? t('execution.attention') : execution.status === 'stopped' ? t('execution.stopped') : t('execution.working')}</strong>
        <p>{execution.recoveryRequired ? execution.canCancel ? t('execution.recoveryStop') : t('execution.recoverySaved') : execution.pendingActions.length ? t('execution.approvalInfo') : error || execution.status === 'needs_attention' ? execution.canCancel ? t('execution.failedStop') : t('execution.reviewResult') : execution.status === 'stopped' ? t('execution.stoppedInfo') : t('execution.keepWriting')}</p>
      </div>
      {execution.canCancel && execution.turnId ? <button className="execution-stop" disabled={stopping} onClick={() => act(() => api.stop(pageId, execution.turnId!), true)}>{stopping ? t('execution.stopping') : t('execution.stop')}</button> : null}
    </div> : null}
    {execution.pendingActions.map(action => <div className="execution-action" key={action.actionId}>
      <h3>{actionTitle(action, t)}</h3>
      <details open>
        <summary>{t('execution.details')}</summary>
        <pre>{JSON.stringify(action.arguments, null, 2)}</pre>
      </details>
      {action.argumentsTruncated ? <p>{t('execution.truncated')}</p> : null}
      {action.argumentsRedacted ? <p>{t('execution.redacted')}</p> : null}
      {action.expiresAt ? <p className="execution-expiry">{t('execution.expiry', { time: new Date(action.expiresAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) })}</p> : null}
      {!action.canResolve ? <p role="status">{!execution.canCancel ? t('execution.expired') : action.blockedReason || t('execution.blocked')}</p> : null}
      <div className="execution-decisions">
        <button className="execution-allow" disabled={busy || stopping || !action.canResolve || action.argumentsTruncated} onClick={() => act(() => api.decide(pageId, action.actionId, 'allow_once'))}>{t('execution.allow')}</button>
        <button disabled={busy || stopping || !action.canResolve} onClick={() => act(() => api.decide(pageId, action.actionId, 'deny'))}>{t('execution.decline')}</button>
      </div>
    </div>)}
    {execution.pendingActionsTruncated ? <p>{t('execution.more')}</p> : null}
    {busy ? <p className="execution-progress" role="status">{t('execution.applying')}</p> : null}
    {error ? <p className="execution-error" role="alert">{error}</p> : null}
    {execution.uncertainActions.length > 0 ? <details className="execution-uncertain" key={pageId}>
      <summary>{t('execution.uncertain')}</summary>
      <p>{t('execution.uncertainInfo')}</p>
      {execution.uncertainActions.map(action => <article className="execution-action" key={action.actionId}>
        <h3>{actionTitle(action, t)}</h3>
        <pre>{JSON.stringify(action.arguments, null, 2)}</pre>
        {action.argumentsTruncated ? <p>{t('execution.uncertainIncomplete')}</p> : null}
        {action.argumentsRedacted ? <p>{t('execution.redacted')}</p> : null}
        <p className="execution-identity">{t('execution.actionIdentity', { id: action.actionId })}</p>
      </article>)}
      {execution.uncertainActionsTruncated ? <p>{t('execution.uncertainMore')}</p> : null}
    </details> : null}
  </section>;
}
