import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { PageView, PendingAction } from '../types';

function actionTitle(action: PendingAction): string {
  if (['mutate_file', 'apply_patch'].includes(action.toolName)) return 'Review a file change';
  if (action.toolName === 'run_command') return 'Review a command';
  if (action.toolName === 'work') return 'Review a work action';
  return `Review ${action.toolName.replaceAll('_', ' ')}`;
}

export function ExecutionStatus({ view, applyView }: { view: PageView; applyView: (view: PageView) => void }) {
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const pageId = view.page.id;
  const currentPage = useRef(pageId);
  currentPage.current = pageId;
  useEffect(() => { setError(''); setBusy(false); setStopping(false); }, [pageId]);
  const execution = view.execution;
  if (!execution || (!error && !execution.canCancel && !execution.pendingActions.length && !execution.recoveryRequired && execution.status !== 'stopped')) return null;

  const act = async (operation: () => Promise<{ view: PageView; continuationError?: { message: string } | null }>, stop = false) => {
    if (stop ? stopping : busy || stopping) return;
    if (stop) setStopping(true); else setBusy(true);
    setError('');
    try {
      const result = await operation(); applyView(result.view);
      if (currentPage.current === pageId && result.continuationError) setError(result.continuationError.message);
    }
    catch (problem) {
      if (currentPage.current === pageId) setError(problem instanceof Error ? problem.message : 'This action could not be completed.');
      // Refresh a stale decision, but never retry a mutation automatically.
      try { applyView(await api.page(pageId)); } catch { /* Preserve existing work. */ }
    } finally {
      if (currentPage.current === pageId) {
        if (stop) setStopping(false); else setBusy(false);
      }
    }
  };

  return <section className="execution-status" aria-label="Page activity" aria-busy={busy || stopping}>
    <div className="execution-heading">
      <div>
        <strong>{execution.pendingActions.length ? 'Your approval is needed' : error || execution.recoveryRequired || execution.status === 'needs_attention' ? 'This turn needs attention' : execution.status === 'stopped' ? 'This turn was stopped' : 'Working on this page'}</strong>
        <p>{execution.recoveryRequired ? execution.canCancel ? 'This earlier turn cannot continue automatically. Stop it, then send a new message to continue.' : 'This earlier turn cannot continue automatically. Its recorded result remains available.' : execution.pendingActions.length ? 'Review the action below. Your decision applies to this action only.' : error || execution.status === 'needs_attention' ? execution.canCancel ? 'This turn could not finish. You can stop it and send a new message to continue.' : 'Review the recorded result before continuing.' : execution.status === 'stopped' ? 'Your conversation is saved. Send a message when you want to continue.' : 'You can add a message while this work continues.'}</p>
      </div>
      {execution.canCancel && execution.turnId ? <button className="execution-stop" disabled={stopping} onClick={() => act(() => api.stop(pageId, execution.turnId!), true)}>{stopping ? 'Stopping…' : 'Stop this turn'}</button> : null}
    </div>
    {execution.pendingActions.map(action => <div className="execution-action" key={action.actionId}>
      <h3>{actionTitle(action)}</h3>
      <details open>
        <summary>Action details</summary>
        <pre>{JSON.stringify(action.arguments, null, 2)}</pre>
      </details>
      {action.argumentsTruncated ? <p>Some details are omitted. This action cannot be approved from a partial preview.</p> : null}
      {action.argumentsRedacted ? <p>Sensitive values have been hidden.</p> : null}
      {action.expiresAt ? <p className="execution-expiry">Available until {new Date(action.expiresAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.</p> : null}
      {!action.canResolve ? <p role="status">{!execution.canCancel ? 'This action is no longer available. Refresh to see the final state.' : action.blockedReason || 'This action cannot be approved now. Stop the turn and send a new message.'}</p> : null}
      <div className="execution-decisions">
        <button className="execution-allow" disabled={busy || stopping || !action.canResolve || action.argumentsTruncated} onClick={() => act(() => api.decide(pageId, action.actionId, 'allow_once'))}>Allow once</button>
        <button disabled={busy || stopping || !action.canResolve} onClick={() => act(() => api.decide(pageId, action.actionId, 'deny'))}>Decline</button>
      </div>
    </div>)}
    {execution.pendingActionsTruncated ? <p>Additional pending actions will appear as these are resolved.</p> : null}
    {busy ? <p className="execution-progress" role="status">Applying your decision…</p> : null}
    {error ? <p className="execution-error" role="alert">{error}</p> : null}
  </section>;
}
