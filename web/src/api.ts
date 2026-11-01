import type { Artifact, ArtifactRead, Bootstrap, DraftReference, LessonCondition, LessonShift, LessonVerdict, Material, MaterialList, MaterialRead, MessageHistory, PageReference, PageSummary, PageView, PulseObservation, PulseState, PostureExperience, PostureView, ResponseLanguage, WorkspaceSettings } from './types';
import type { AdaptiveWorkChoices, WorkspaceFile, WorkspaceFileIdentity, WorkspaceFileList, WorkspaceUpload } from './types';

export type ActionReceipt = { accepted: boolean; repeated: boolean; actionStatus: string; actionCompleted: boolean; continuationError: { code: string; message: string } | null; view: PageView };
export type PulseWriteIdentity = Pick<PulseObservation, 'workspaceId' | 'serviceInstanceId'>;

export class ApiError extends Error {
  constructor(message: string, public readonly code: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

async function fetchResponse(path: string, options: RequestInit = {}): Promise<Response> {
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
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.error?.message ?? 'Something went wrong. Please try again.', body?.error?.code ?? 'request_failed', response.status);
  }
  return response;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetchResponse(path, options);
  const body = await response.json().catch(() => null);
  if (body === null) throw new ApiError('The workspace returned an unreadable response.', 'invalid_response', response.status);
  return body as T;
}

async function verifiedDownload(response: Response, expectedDigest: string, errorCode: string) {
  if (response.headers.get('content-type') !== 'application/octet-stream'
    || response.headers.get('etag') !== `"${expectedDigest}"`) {
    throw new ApiError('The downloaded version could not be verified.', errorCode, 0);
  }
  const encodedName = response.headers.get('content-disposition')?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (!encodedName) throw new ApiError('The workspace returned an unreadable download.', 'invalid_response', response.status);
  const filename = decodeURIComponent(encodedName);
  const bytes = await response.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const actual = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
  if (actual !== expectedDigest) throw new ApiError('The downloaded version could not be verified.', errorCode, 0);
  return { blob: new Blob([bytes], { type: 'application/octet-stream' }), filename };
}

const pagePath = (id: string) => `/pages/${encodeURIComponent(id)}`;
const pulseWriteHeaders = (identity: PulseWriteIdentity) => ({
  'X-Per-Pulse-World': identity.workspaceId,
  'X-Per-Pulse-Service-Instance': identity.serviceInstanceId,
});

export const api = {
  files: (path: string, identity: WorkspaceFileIdentity, signal?: AbortSignal) =>
    request<WorkspaceFileList>(`/workspace/files?path=${encodeURIComponent(path)}`, { headers: pulseWriteHeaders(identity), signal }),
  file: (path: string, identity: WorkspaceFileIdentity, signal?: AbortSignal) =>
    request<WorkspaceFile>(`/workspace/files/content?path=${encodeURIComponent(path)}`, { headers: pulseWriteHeaders(identity), signal }),
  upload: (file: File, identity: WorkspaceFileIdentity, signal?: AbortSignal) =>
    request<WorkspaceUpload>(`/workspace/files?name=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: { ...pulseWriteHeaders(identity), 'Content-Type': 'application/octet-stream' }, body: file, signal,
    }),
  bootstrap: (signal?: AbortSignal) => request<Bootstrap>('/bootstrap', { signal }),
  settings: (signal?: AbortSignal) => request<WorkspaceSettings>('/settings', { signal }),
  updateSettings: (responseLanguage: ResponseLanguage) => request<WorkspaceSettings>('/settings', { method: 'PATCH', body: JSON.stringify({ responseLanguage }) }),
  page: (id: string, signal?: AbortSignal, sourceMessageId?: string) =>
    request<PageView>(pagePath(id) + (sourceMessageId ? `?sourceMessageId=${encodeURIComponent(sourceMessageId)}` : ''), { signal }),
  adaptiveChoices: (id: string, signal?: AbortSignal) =>
    request<AdaptiveWorkChoices>(`${pagePath(id)}/team/choices`, { signal }),
  history: (id: string, beforeMessageId: string, signal?: AbortSignal) =>
    request<MessageHistory>(`${pagePath(id)}/messages?beforeMessageId=${encodeURIComponent(beforeMessageId)}`, { signal }),
  searchMessages: (id: string, query: string, beforeMessageId: string | null, signal?: AbortSignal) =>
    request<MessageHistory>(`${pagePath(id)}/messages/search?q=${encodeURIComponent(query)}${beforeMessageId ? `&beforeMessageId=${encodeURIComponent(beforeMessageId)}` : ''}`, { signal }),
  createPage: (data: { title: string; instruction: string; sourcePageId: string; sourceMessageId?: string | null }) =>
    request<{ page: PageSummary; reference: PageReference; initialMessageId: string; view: PageView }>('/pages', { method: 'POST', body: JSON.stringify(data) }),
  send: (id: string, content: string, references: DraftReference[], materials: string[] = []) =>
    request<{ messageId: string; view: PageView }>(`${pagePath(id)}/messages`, { method: 'POST', body: JSON.stringify({ content, references, materials }) }),
  materials: (id: string, signal?: AbortSignal) => request<MaterialList>(`${pagePath(id)}/materials`, { signal }),
  material: (id: string, materialId: string, signal?: AbortSignal) => request<MaterialRead>(`${pagePath(id)}/materials/${encodeURIComponent(materialId)}`, { signal }),
  downloadMaterial: async (pageId: string, material: Material, identity: WorkspaceFileIdentity, signal: AbortSignal) => {
    const response = await fetchResponse(`${pagePath(pageId)}/materials/${encodeURIComponent(material.id)}/download`, {
      headers: pulseWriteHeaders(identity), signal,
    });
    if (response.headers.get('x-per-pulse-world') !== identity.workspaceId
      || response.headers.get('x-per-pulse-service-instance') !== identity.serviceInstanceId) {
      throw new ApiError('The workspace changed. Reload before downloading the source.', 'workspace_changed', 409);
    }
    return verifiedDownload(response, material.sha256, 'material_download_unverified');
  },
  uploadMaterial: async (pageId: string, identity: WorkspaceFileIdentity, uploadId: string, file: File, signal?: AbortSignal) => {
    if (file.size > 1024 * 1024) throw new ApiError('Choose a text file of at most 1 MiB.', 'material_too_large', 413);
    const bytes = await file.arrayBuffer();
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), value => value.toString(16).padStart(2, '0')).join('');
    const result = await request<WorkspaceFileIdentity & { material: Material; repeated: boolean }>(`${pagePath(pageId)}/materials?name=${encodeURIComponent(file.name)}`, {
      method: 'POST', body: bytes, signal, headers: {
        ...pulseWriteHeaders(identity), 'Content-Type': 'application/octet-stream',
        'X-Per-Pulse-Upload-Id': uploadId, 'X-Per-Pulse-File-SHA256': digest,
      },
    });
    if (!result.material || result.material.id !== uploadId || result.material.pageId !== pageId || result.material.sha256 !== digest || result.material.bytes !== file.size) {
      throw new ApiError('The uploaded file could not be verified. Refresh source files before trying again.', 'material_upload_unconfirmed', 0);
    }
    return result;
  },
  referenceMaterial: (pageId: string, file: Pick<WorkspaceFile, 'path' | 'sha256'>, identity: WorkspaceFileIdentity, signal?: AbortSignal) =>
    request<WorkspaceFileIdentity & { material: Material; repeated: boolean }>(`${pagePath(pageId)}/materials/reference`, {
      method: 'POST', headers: pulseWriteHeaders(identity), signal,
      body: JSON.stringify({ path: file.path, sha256: file.sha256 }),
    }),
  reference: (id: string, data: { targetPageId: string; label: string; sourceMessageId?: string | null }) =>
    request<{ reference: PageReference; view: PageView }>(`${pagePath(id)}/references`, { method: 'POST', body: JSON.stringify(data) }),
  bringHome: (id: string, content: string, messageId?: string, includeMaterials = false) =>
    request<{ messageId: string; reference: PageReference; view: PageView }>(`${pagePath(id)}/return`, { method: 'POST', body: JSON.stringify({ content, messageId, includeMaterials }) }),
  transferWork: (id: string, targetPageId: string, messageId: string, excerpt: string, instruction: string) =>
    request<{ messageId: string; reference: PageReference; view: PageView }>(`${pagePath(id)}/transfer`, {
      method: 'POST', body: JSON.stringify({ targetPageId, messageId, excerpt, instruction }),
    }),
  pulse: (signal?: AbortSignal) => request<PulseState>('/pulse', { signal }),
  pulseObservation: (signal?: AbortSignal) => request<PulseObservation>('/pulse/observation', { signal }),
  setPulsePaused: (paused: boolean, identity: PulseWriteIdentity) =>
    request<{ pulse: PulseState }>('/pulse', { method: 'PATCH', headers: pulseWriteHeaders(identity), body: JSON.stringify({ paused }) }),
  setPulseContextPaused: (pageId: string, paused: boolean, identity: PulseWriteIdentity) =>
    request<{ pulse: PulseState }>(`/pulse/contexts/${encodeURIComponent(pageId)}`, { method: 'PATCH', headers: pulseWriteHeaders(identity), body: JSON.stringify({ paused }) }),
  connectPulse: (sourcePageId: string, targetPageId: string, kind: 'excitatory' | 'inhibitory', identity: PulseWriteIdentity) =>
    request<{ pulse: PulseState }>('/pulse/connections', { method: 'POST', headers: pulseWriteHeaders(identity), body: JSON.stringify({ sourcePageId, targetPageId, kind, weight: 0.6 }) }),
  disconnectPulse: (sourcePageId: string, targetPageId: string, identity: PulseWriteIdentity) =>
    request<{ pulse: PulseState }>('/pulse/connections', { method: 'DELETE', headers: pulseWriteHeaders(identity), body: JSON.stringify({ sourcePageId, targetPageId }) }),
  posture: (id: string, signal?: AbortSignal) => request<PostureView>(`${pagePath(id)}/posture`, { signal }),
  saveExample: (pageId: string, data: { sourceMessageId: string; feedbackMessageId?: string; title: string; excerpt: string; note: string }) =>
    request<{ experience: PostureExperience }>(`${pagePath(pageId)}/posture/examples`, { method: 'POST', body: JSON.stringify(data) }),
  reviseExperience: (pageId: string, experience: PostureExperience, changes: { active?: boolean; scope?: 'context' | 'subject'; lesson?: string; note?: string; conditions?: LessonCondition; counterexamples?: string[]; shift?: LessonShift }, evidence?: string) =>
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
  downloadArtifact: async (id: string, artifact: Artifact, signal: AbortSignal) => {
    const response = await fetchResponse(`${pagePath(id)}/artifacts/${encodeURIComponent(artifact.id)}/download`, { signal });
    return verifiedDownload(response, artifact.sha256, 'artifact_download_unverified');
  },
  rename: (id: string, title: string) => request<PageView>(pagePath(id), { method: 'PATCH', body: JSON.stringify({ title }) }),
};
