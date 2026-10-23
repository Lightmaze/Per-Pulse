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
  actionId: string;
  turnId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  argumentsTruncated: boolean;
  argumentsRedacted: boolean;
  approvalId: string | null;
  expiresAt: string | null;
  canResolve: boolean;
  blockedReason: string | null;
};

export type PageExecution = {
  pageId: string;
  sessionId: string | null;
  turnId: string | null;
  status: 'idle' | 'working' | 'needs_attention' | 'error' | 'stopped';
  canCancel: boolean;
  pendingActions: PendingAction[];
  pendingActionsTruncated: boolean;
  recoveryRequired: boolean;
  historicalUncertainty: boolean;
  updatedAt: string | null;
};

export type PageView = {
  page: PageSummary;
  messages: Message[];
  references: PageReference[];
  backlinks: PageReference[];
  breadcrumbs: { id: string; title: string }[];
  provider: ProviderState;
  hasMore: boolean;
  execution: PageExecution;
  artifacts: Artifact[];
  artifactsTruncated: boolean;
};

export type Artifact = {
  id: string;
  pageId: string;
  path: string;
  sha256: string;
  version: string;
  beforeSha256: string | null;
  sessionId: string;
  turnId: string;
  actionId: string;
  changeId: string;
  checkpointId: string;
  operationId: string;
  createdAt: string;
  state: 'unchecked' | 'current' | 'changed' | 'unavailable';
};

export type ArtifactRead = {
  pageId: string;
  artifact: Artifact;
  state: 'current' | 'changed' | 'unavailable';
  content: string | null;
  observedSha256: string | null;
  bytes: number | null;
  redacted: boolean;
  truncated: boolean;
  reason: string | null;
  limitBytes: number;
  previewLimitBytes: number;
};

export type Bootstrap = {
  name: 'Per Pulse';
  version: string;
  workspaceId: string;
  example: boolean;
  homeId: 'home';
  provider: ProviderState;
  pages: PageSummary[];
};

export type DraftReference = { targetPageId: string; label: string };
export type Draft = { text: string; references: DraftReference[] };
export type NavigationOrigin = { pageId: string; messageId?: string; referenceId?: string };
