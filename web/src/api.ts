import type { ArtifactRead, Bootstrap, DraftReference, LessonVerdict, MessageHistory, PageReference, PageSummary, PageView, PulseObservation, PulseState, PostureExperience, PostureView, ResponseLanguage, WorkspaceSettings } from './types';

export type ActionReceipt = { accepted: boolean; repeated: boolean; actionStatus: string; actionCompleted: boolean; continuationError: { code: string; message: string } | null; view: PageView };

export class ApiError extends Error {
  constructor(message: string, public readonly code: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('Your workspace could not be reached. Please try again.', 'network_error', 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(body?.error?.message ?? 'Something went wrong. Please try again.', body?.error?.code ?? 'request_failed', response.status);
  }
  if (body === null) throw new ApiError('The workspace returned an unreadable response.', 'invalid_response', response.status);
  return body as T;
}

const pagePath = (id: string) => `/pages/${encodeURIComponent(id)}`;

export const api = {
  bootstrap: (signal?: AbortSignal) => request<Bootstrap>('/bootstrap', { signal }),
  settings: (signal?: AbortSignal) => request<WorkspaceSettings>('/settings', { signal }),
  updateSettings: (responseLanguage: ResponseLanguage) => request<WorkspaceSettings>('/settings', { method: 'PATCH', body: JSON.stringify({ responseLanguage }) }),
  page: (id: string, signal?: AbortSignal, sourceMessageId?: string) =>
    request<PageView>(pagePath(id) + (sourceMessageId ? `?sourceMessageId=${encodeURIComponent(sourceMessageId)}` : ''), { signal }),
  history: (id: string, beforeMessageId: string, signal?: AbortSignal) =>
    request<MessageHistory>(`${pagePath(id)}/messages?beforeMessageId=${encodeURIComponent(beforeMessageId)}`, { signal }),
  createPage: (data: { title: string; instruction: string; sourcePageId: string; sourceMessageId?: string | null }) =>
    request<{ page: PageSummary; reference: PageReference; initialMessageId: string; view: PageView }>('/pages', { method: 'POST', body: JSON.stringify(data) }),
  send: (id: string, content: string, references: DraftReference[]) =>
    request<{ messageId: string; view: PageView }>(`${pagePath(id)}/messages`, { method: 'POST', body: JSON.stringify({ content, references }) }),
  reference: (id: string, data: { targetPageId: string; label: string; sourceMessageId?: string | null }) =>
    request<{ reference: PageReference; view: PageView }>(`${pagePath(id)}/references`, { method: 'POST', body: JSON.stringify(data) }),
  bringHome: (id: string, content: string, messageId?: string) =>
    request<{ messageId: string; reference: PageReference; view: PageView }>(`${pagePath(id)}/return`, { method: 'POST', body: JSON.stringify({ content, messageId }) }),
  pulse: (signal?: AbortSignal) => request<PulseState>('/pulse', { signal }),
  pulseObservation: (signal?: AbortSignal) => request<PulseObservation>('/pulse/observation', { signal }),
  setPulsePaused: (paused: boolean) => request<{ pulse: PulseState }>('/pulse', { method: 'PATCH', body: JSON.stringify({ paused }) }),
  setPulseContextPaused: (pageId: string, paused: boolean) =>
    request<{ pulse: PulseState }>(`/pulse/contexts/${encodeURIComponent(pageId)}`, { method: 'PATCH', body: JSON.stringify({ paused }) }),
  connectPulse: (sourcePageId: string, targetPageId: string, kind: 'excitatory' | 'inhibitory') =>
    request<{ pulse: PulseState }>('/pulse/connections', { method: 'POST', body: JSON.stringify({ sourcePageId, targetPageId, kind, weight: 0.6 }) }),
  disconnectPulse: (sourcePageId: string, targetPageId: string) =>
    request<{ pulse: PulseState }>('/pulse/connections', { method: 'DELETE', body: JSON.stringify({ sourcePageId, targetPageId }) }),
  posture: (id: string, signal?: AbortSignal) => request<PostureView>(`${pagePath(id)}/posture`, { signal }),
  reviseExperience: (pageId: string, experience: PostureExperience, changes: { active?: boolean; scope?: 'context' | 'subject'; lesson?: string; counterexamples?: string[]; shift?: PostureExperience['shift'] }, evidence?: string) =>
    request<{ experience: PostureExperience }>(`${pagePath(pageId)}/posture/experiences/${encodeURIComponent(experience.id)}`, {
      method: 'PATCH', body: JSON.stringify({ expectedRevision: experience.revision, changes, ...(evidence ? { evidence } : {}) }),
    }),
  experienceFeedback: (pageId: string, experience: PostureExperience, verdict: LessonVerdict, evidence: string, resultMessageId?: string) =>
    request<{ experience: PostureExperience }>(`${pagePath(pageId)}/posture/experiences/${encodeURIComponent(experience.id)}/feedback`, {
      method: 'POST', body: JSON.stringify({ expectedRevision: experience.revision, verdict, evidence, resultMessageId }),
    }),
  stop: (id: string, turnId: string) => request<{ accepted: boolean; repeated: boolean; view: PageView }>(`${pagePath(id)}/execution/stop`, { method: 'POST', body: JSON.stringify({ turnId }) }),
  decide: (id: string, actionId: string, decision: 'allow_once' | 'deny' | 'cancel') => request<ActionReceipt>(`${pagePath(id)}/execution/actions/${encodeURIComponent(actionId)}`, { method: 'POST', body: JSON.stringify({ decision }) }),
  artifact: (id: string, artifactId: string) => request<ArtifactRead>(`${pagePath(id)}/artifacts/${encodeURIComponent(artifactId)}`),
  rename: (id: string, title: string) => request<PageView>(pagePath(id), { method: 'PATCH', body: JSON.stringify({ title }) }),
};
