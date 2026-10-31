export type WorkspaceFileIdentity = { workspaceId: string; serviceInstanceId: string };
export type WorkspaceFileEntry = { name: string; path: string; kind: 'file' | 'directory'; bytes: number | null };
export type WorkspaceFileList = WorkspaceFileIdentity & {
  path: string; entries: WorkspaceFileEntry[]; truncated: boolean; limit_bytes: number; scope: 'shared_native_workspace';
};
export type WorkspaceFile = WorkspaceFileIdentity & { path: string; content: string; bytes: number; sha256: string };
export type WorkspaceUpload = WorkspaceFileIdentity & { path: string; name: string; bytes: number; sha256: string; source: 'user_upload' };

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
  kind: 'created' | 'reference' | 'return' | 'transfer';
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
  materials?: Material[];
  materialSuffixLength?: number;
  replay?: {
    sources: { pageId: string; messageId: string; role: 'user' | 'assistant' }[];
    unavailableSources: number; selected: boolean; contentOffset: number;
    focus?: string;
  } | null;
};

export type ProviderState = {
  configured: boolean;
  model: string | null;
  label: 'AI connected' | 'AI disconnected';
  detail: string;
  workspaceActions: boolean;
};

export type ActionDetails = {
  actionId: string; turnId: string; toolName: string;
  arguments: Record<string, unknown>; argumentsTruncated: boolean; argumentsRedacted: boolean;
};
export type UncertainAction = ActionDetails & { status: 'uncertain' };
export type PendingAction = ActionDetails & {
  approvalId: string | null; expiresAt: string | null; canResolve: boolean; blockedReason: string | null;
};
export type PageExecution = {
  pageId: string; sessionId: string | null; turnId: string | null;
  status: 'idle' | 'working' | 'needs_attention' | 'error' | 'stopped';
  canCancel: boolean; pendingActions: PendingAction[]; pendingActionsTruncated: boolean;
  recoveryRequired: boolean; historicalUncertainty: boolean; updatedAt: string | null;
  uncertainActions: UncertainAction[];
  uncertainActionsTruncated: boolean;
  replay?: { sourceCount: number; focus?: string } | null;
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

export type Material = {
  id: string; pageId: string; name: string; path: string; bytes: number; sha256: string;
  createdAt: string; source: 'user_upload' | 'workspace_reference';
};
export type MaterialList = { pageId: string; materials: Material[]; truncated: boolean; limitBytes: number };
export type MaterialRead = { material: Material; content: string; redacted: boolean; truncated: boolean; previewLimitBytes: number; downloadable: boolean };

export type PageView = {
  page: PageSummary;
  messages: Message[];
  references: PageReference[];
  backlinks: PageReference[];
  breadcrumbs: { id: string; title: string }[];
  provider: ProviderState;
  execution: PageExecution; artifacts: Artifact[]; artifactsTruncated: boolean;
  hasMore: boolean;
  historyBeforeMessageId: string | null;
  earlierSourceMessageId?: string | null;
};

export type MessageHistory = {
  pageId: string;
  messages: Message[];
  hasMore: boolean;
  beforeMessageId: string | null;
};

export type ResponseLanguage = 'auto' | 'en' | 'zh-CN';
export type WorkspaceSettings = { responseLanguage: ResponseLanguage };

export type Bootstrap = {
  name: 'Per Pulse';
  version: string;
  workspaceId: string;
  serviceInstanceId: string;
  example: boolean;
  homeId: 'home';
  provider: ProviderState;
  pages: PageSummary[];
  settings: WorkspaceSettings;
  postureEnabled: boolean;
};

export type LessonCondition = string | { all: LessonCondition[] } | { any: LessonCondition[] } | { not: LessonCondition };
export type LessonVerdict = 'useful' | 'not_useful' | 'harmful';
export type LessonShift = { understanding: string; candidates: string; delivery: string };
export type ExperienceMessageSource = {
  pageId: string; messageId: string; materials: Material[]; materialSuffixLength: number;
};
type ExperienceBase = {
  id: string;
  revision: number;
  title: string;
  scope: 'context' | 'subject';
  active: boolean;
  canEdit: boolean;
  canReport: boolean;
  sourcePageId: string | null;
  sourceRegime?: 'replay' | null;
  sourceKind?: 'reading';
  readingSource?: { path: string; digest: string; receiptId: string; observedAt: string };
  sourceAnchor?: { resultMessageId: string; feedbackMessageId?: string };
  record_type: 'observed_episode' | 'constructed_fixture';
  encounter?: { situation: string; action: string; outcome?: string };
  encounterSources?: Partial<Record<'situation' | 'action' | 'outcome', ExperienceMessageSource>>;
  source?: { source_message_id?: string; feedback_message_id?: string };
  feedback?: { verdict: string; evidence: string; evidenceSource?: ExperienceMessageSource; source?: {
    kind?: string; page_id?: string; relation?: string;
    result_message_id?: string; result_evidence_class?: string;
    result_execution_regime?: 'ordinary' | 'replay';
  } }[];
};
export type PostureLesson = ExperienceBase & {
  representation?: 'lesson';
  lesson: string;
  conditions: LessonCondition;
  counterexamples: string[];
  shift: LessonShift;
};
export type PostureExample = ExperienceBase & {
  representation: 'example';
  excerpt: string;
  note: string;
};
export type PostureExperience = PostureLesson | PostureExample;
export type PostureView = { enabled: boolean; experiences: PostureExperience[] };

export type DraftReference = { targetPageId: string; label: string };
export type Draft = { text: string; references: DraftReference[]; materials?: Pick<Material, 'id' | 'name'>[] };
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
  /** PageService-validated local message, not an inferred Kernel event. */
  messageId: string | null;
  /** Original settled answer, verified against this activation's native turn and page lineage. */
  resultMessageId?: string | null;
  kind: string;
  status: string;
  createdAt: string;
  sourceEventId: string | null;
  /** Original source message only when it belongs to the verified source page. */
  sourceMessageId: string | null;
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
  providerTokenBudget: {
    limit: number;
    charged: number;
    remaining: number;
    accountedTokens: number;
    unaccountedReservations: number;
    syntheticTokens: number;
    overrunTokens: number;
    trackedAdmissions: number;
    reservationPerAdmission: number;
    windowSeconds: number;
    unit: string;
    hardProviderSpendingCap: false;
  } | null;
  queue: { queued: number; running: number; oldestQueuedAt: string | null };
  // Derived from the same complete native queue, not the recent-events sample.
  queueByContext?: { center_id: string | null; pageId: string | null;
    queued: number; running: number; oldestQueuedAt: string | null;
    oldestQueuedDeferredReason?: string | null }[];
  contexts: { center_id: string | null; pageId: string | null; inhibition: number }[];
  connections: PulseConnection[];
  recentActivations: PulseActivation[];
};

/** One native Pulse read bound to the World and the serving process. */
export type PulseObservation = {
  workspaceId: string;
  serviceInstanceId: string;
  pulse: PulseState;
};
