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
  earlierSourceMessageId: string | null;
};

export type Bootstrap = {
  name: 'Per Pulse';
  version: string;
  workspaceId: string;
  example: boolean;
  homeId: 'home';
  provider: ProviderState;
  pages: PageSummary[];
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
