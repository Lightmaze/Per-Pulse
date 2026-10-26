export type PageSummary = {
  id: string;
  kind: 'home' | 'work';
  title: string;
  status: 'idle' | 'queued' | 'working' | 'needs_attention' | 'error';
  createdAt: string;
  updatedAt: string;
  sourcePageId: string | null;
  sourceMessageId: string | null;
  description: string;
};

export type PageReference = {
  id: string;
  sourcePageId: string;
  sourceMessageId: string | null;
  targetPageId: string;
  label: string;
  kind: 'created' | 'reference' | 'return';
  createdAt: string;
};

export type Message = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  createdAt: string;
  status: string;
  sourcePageId: string | null;
  sourceMessageId: string | null;
  references: PageReference[];
};

export type ProviderState = {
  configured: boolean;
  model: string | null;
  label: 'AI connected' | 'AI disconnected';
  detail: string;
  workspaceActions: boolean;
};

export type PendingAction = {
  actionId: string; turnId: string; toolName: string;
  arguments: Record<string, unknown>; argumentsTruncated: boolean; argumentsRedacted: boolean;
  approvalId: string | null; expiresAt: string | null; canResolve: boolean; blockedReason: string | null;
};
export type PageExecution = {
  pageId: string; sessionId: string | null; turnId: string | null;
  status: 'idle' | 'working' | 'needs_attention' | 'error' | 'stopped';
  canCancel: boolean; pendingActions: PendingAction[]; pendingActionsTruncated: boolean;
  recoveryRequired: boolean; historicalUncertainty: boolean; updatedAt: string | null;
};
export type Artifact = {
  id: string; pageId: string; path: string; sha256: string;
  version: string; beforeSha256: string | null; sessionId: string;
  turnId: string; actionId: string; changeId: string; checkpointId: string;
  operationId: string; createdAt: string; state: 'unchecked' | 'current' | 'changed' | 'unavailable';
};
export type ArtifactRead = {
  pageId: string; artifact: Artifact; state: 'current' | 'changed' | 'unavailable';
  content: string | null; observedSha256: string | null; bytes: number | null;
  redacted: boolean; truncated: boolean; reason: string | null;
  limitBytes: number; previewLimitBytes: number;
};

export type PageView = {
  page: PageSummary;
  messages: Message[];
  references: PageReference[];
  backlinks: PageReference[];
  breadcrumbs: { id: string; title: string }[];
  provider: ProviderState;
  execution: PageExecution; artifacts: Artifact[]; artifactsTruncated: boolean;
  hasMore: boolean;
  earlierSourceMessageId?: string | null;
};

export type ResponseLanguage = 'auto' | 'en' | 'zh-CN';
export type WorkspaceSettings = { responseLanguage: ResponseLanguage };

export type Bootstrap = {
  name: 'Per Pulse';
  version: string;
  workspaceId: string;
  example: boolean;
  homeId: 'home';
  provider: ProviderState;
  pages: PageSummary[];
  settings: WorkspaceSettings;
  postureEnabled: boolean;
};

export type LessonCondition = string | { all: LessonCondition[] } | { any: LessonCondition[] } | { not: LessonCondition };
export type PostureExperience = {
  id: string;
  revision: number;
  title: string;
  lesson: string;
  conditions: LessonCondition;
  counterexamples: string[];
  shift: { understanding: string; candidates: string; delivery: string };
  scope: 'context' | 'subject';
  active: boolean;
  canEdit: boolean;
  sourcePageId: string | null;
  record_type: 'observed_episode' | 'constructed_fixture';
  encounter?: { situation: string; action: string; outcome: string };
  source?: { source_message_id?: string; feedback_message_id?: string };
  feedback?: { verdict: string; evidence: string }[];
};
export type PostureView = { enabled: boolean; experiences: PostureExperience[] };

export type DraftReference = { targetPageId: string; label: string };
export type Draft = { text: string; references: DraftReference[] };
export type NavigationOrigin = { pageId: string; messageId?: string; referenceId?: string };

export type PulseConnection = {
  id: string;
  source_center_id: string | null;
  target_center_id: string | null;
  sourcePageId: string | null;
  targetPageId: string | null;
  weight: number;
  kind: 'excitatory' | 'inhibitory';
};

export type PulseActivation = {
  eventId: string;
  centerId: string | null;
  pageId: string | null;
  kind: string;
  status: string;
  createdAt: string;
  sourceEventId: string | null;
  sourceCenterId: string | null;
  sourcePageId: string | null;
  deferredReason: string | null;
};

export type PulseRetainedAttention = {
  center_id: string | null;
  pageId: string | null;
  note: string;
  drive: number;
  revision: number;
  sourceTurnId: string | null;
  sourceActionId: string | null;
  sourceEventId: string | null;
  sourcePageId: string | null;
  sourceMessageId: string | null;
  operatorEditedAt: string | null;
};

export type PulseState = {
  enabled: boolean;
  paused: boolean;
  heldContexts: { center_id: string | null; pageId: string | null }[];
  retainedAttention: PulseRetainedAttention[];
  running: boolean;
  providerConfigured: boolean;
  budget: { limit: number; used: number; remaining: number; windowSeconds: number; unit: string };
  queue: { queued: number; running: number; oldestQueuedAt: string | null };
  contexts: { center_id: string | null; pageId: string | null; inhibition: number }[];
  connections: PulseConnection[];
  recentActivations: PulseActivation[];
};
