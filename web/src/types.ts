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
};

export type PageView = {
  page: PageSummary;
  messages: Message[];
  references: PageReference[];
  backlinks: PageReference[];
  breadcrumbs: { id: string; title: string }[];
  provider: ProviderState;
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
};

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

export type PulseState = {
  enabled: boolean;
  paused: boolean;
  heldContexts: { center_id: string | null; pageId: string | null }[];
  running: boolean;
  providerConfigured: boolean;
  budget: { limit: number; used: number; remaining: number; windowSeconds: number; unit: string };
  queue: { queued: number; running: number; oldestQueuedAt: string | null };
  contexts: { center_id: string | null; pageId: string | null; inhibition: number }[];
  connections: PulseConnection[];
  recentActivations: PulseActivation[];
};
