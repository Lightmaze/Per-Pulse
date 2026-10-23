# Per Pulse API contract (English v0.1)

All endpoints are same-origin JSON under `/api`, with `Cache-Control: no-store`. No client-supplied model credentials. The visible client refreshes the current page and bootstrap summaries every two seconds, including while the current page is idle, so background decisions remain discoverable. No fake/model output is generated offline. Session data is the inherited Pulse World/Kernel state, with lightweight page-reference metadata in the same World store.

## Types

```ts
type PageSummary = {
  id: string; // literal "home" or the real TaskFront id
  kind: "home" | "work";
  title: string;
  status: "idle" | "queued" | "working" | "needs_attention" | "error";
  createdAt: string;
  updatedAt: string;
  sourcePageId: string | null;
  sourceMessageId: string | null;
  description: string;
};
type PageReference = {
  id: string;
  sourcePageId: string;
  sourceMessageId: string | null;
  targetPageId: string;
  label: string;
  kind: "created" | "reference" | "return";
  createdAt: string;
};
type Message = {
  id: string; // real causal event id
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: string;
  status: string; // real causal status, e.g. queued / running / settled / failed
  sourcePageId: string | null;
  sourceMessageId: string | null;
  references: PageReference[];
};
type ProviderState = {
  configured: boolean;
  model: string | null;
  label: "AI connected" | "AI disconnected";
  detail: string;
  workspaceActions: boolean;
};
type PageView = {
  page: PageSummary;
  messages: Message[];
  references: PageReference[]; // outgoing, including references with a null sourceMessageId
  backlinks: PageReference[]; // incoming
  breadcrumbs: { id: string; title: string }[];
  provider: ProviderState;
  hasMore: boolean;
  execution: PageExecution;
  artifacts: Artifact[];
  artifactsTruncated: boolean;
};
type Bootstrap = {
  name: "Per Pulse";
  version: string;
  example: boolean;
  workspaceId: string; // native World ID, stable across restarts
  homeId: "home";
  provider: ProviderState;
  pages: PageSummary[];
};
```

## Read

- `GET /api/bootstrap` -> `Bootstrap`.
- `GET /api/pages/{id}` -> `PageView` (latest bounded messages).
- `GET /api/health` -> `{status:"ok", providerConfigured:boolean}`.

Scope browser drafts, scroll positions, and navigation history by `workspaceId`
as well as page ID. Ordinary and example workspaces have distinct World IDs,
even when they use the same server origin and the shared page ID `home`.

## Create a page from the current conversation

`POST /api/pages`

```json
{"title":"Session page design","instruction":"Explore inline links between focused conversations.","sourcePageId":"home","sourceMessageId":null}
```

`title` and `instruction` are required. `sourcePageId` defaults to `home`. `sourceMessageId` may be an existing message in that source page; when omitted/null, the reference is page-level. Response `201`:

```ts
{ page: PageSummary; reference: PageReference; initialMessageId: string; view: PageView }
```

The page is the original continuing subject's real Work; it is not a new agent. Creating a reference never starts extra model computation. The initial Work instruction is a real user request and queues normal inference only when a model is configured.

## Add a message

`POST /api/pages/{id}/messages`

```json
{"content":"Keep the page links lightweight.","references":[{"targetPageId":"existing-work-id","label":"Session page design"}]}
```

`references` is optional. Response `202`: `{messageId:string, view:PageView}`. In offline mode the real message remains saved and explicitly queued, with no assistant response.

## Reference an existing page

`POST /api/pages/{id}/references`

```json
{"targetPageId":"existing-work-id","sourceMessageId":"optional-existing-message-id","label":"Session page design"}
```

Response `201`: `{reference:PageReference, view:PageView}`. The same target can be referenced from many pages. References are navigation only and do not copy histories, run a model, or approve actions.

## Bring selected work back to Home

`POST /api/pages/{id}/return`

```json
{"content":"Use an inline page link at the point a topic appears.","messageId":"optional-source-message-id"}
```

Response `201`: `{messageId:string, reference:PageReference, view:PageView}`. `view` is Home. This is an explicit user-authored return with preserved source, not an invented assistant report. The source Work remains open. Home may respond through its ordinary model path when configured.

## Rename

`PATCH /api/pages/{id}` with `{"title":"New title"}` -> `PageView` (Work only).

## Errors

Errors use `{error:{code:string,message:string}}` with 400/404/409/422/503. Show the message in English. Never silently switch to an example or fabricated assistant response after an error.

## Execution and exact-action controls

`GET /api/pages/{id}/execution` observes existing native state without creating a runner or model request. The server derives the native scope from the page; the browser cannot provide an owner or session override.

```ts
type PageExecution = {
  pageId: string;
  sessionId: string | null;
  turnId: string | null;
  status: "idle" | "working" | "needs_attention" | "error" | "stopped";
  canCancel: boolean;
  recoveryRequired: boolean;
  historicalUncertainty: boolean;
  updatedAt: string | null;
  pendingActionsTruncated: boolean;
  pendingActions: {
    actionId: string; turnId: string; toolName: string;
    arguments: Record<string, unknown>;
    argumentsTruncated: boolean; argumentsRedacted: boolean;
    approvalId: string | null; expiresAt: string | null;
    canResolve: boolean; blockedReason: string | null;
  }[];
};
```

`POST /api/pages/{id}/execution/stop` with `{"turnId":"exact-existing-turn"}` returns `{accepted, repeated, ...receipt, view}`. It never selects a newer turn on a stale request. Stopping an already cancelled turn is observation. Recovered turns can be stopped while offline, using the same Kernel authority without constructing a model or effect-capable provider. Completed effects are not undone; later queued input is not erased.

`POST /api/pages/{id}/execution/actions/{actionId}` with `{"decision":"allow_once"}` accepts `allow_once`, `deny`, or `cancel`. The action must belong to this page and remain eligible under the original native approval/turn attachment. Expired, detached or incomplete approval previews cannot grant execution. Retrying an identical completed decision returns observation only; changing it conflicts.

The decision response includes `accepted`, `repeated`, `actionStatus`, `actionCompleted`, `continuationError` and a fresh `view`. `actionCompleted` is true only for a durable succeeded action. `continuationError` is null or `{code,message}`. A completed write followed by a failed model continuation is a committed decision with an explicit continuation error, not an instruction to repeat the write.

## File results

`GET /api/pages/{id}/artifacts` returns `{pageId, artifacts, truncated, scanLimit, limitBytes, previewLimitBytes}`. It projects successful native `mutate_file` receipts, scanning a bounded recent action set. It does not read file contents. Missing/truncated source metadata is omitted and marks the list incomplete.

```ts
type Artifact = {
  id: string; // original action id, not a caller-controlled path
  pageId: string; path: string;
  sha256: string; version: string; beforeSha256: string | null;
  sessionId: string; turnId: string; actionId: string;
  changeId: string; checkpointId: string; operationId: string;
  createdAt: string;
  state: "unchecked" | "current" | "changed" | "unavailable";
};
```

`GET /api/pages/{id}/artifacts/{artifactId}` returns `{pageId, artifact, state, content, observedSha256, bytes, redacted, truncated, reason, limitBytes, previewLimitBytes}`. Only `state: "current"` includes text. The original workspace and page/action scope, regular-file identity, UTF-8 encoding and digest are checked. Files are limited to 1 MiB and previews to 64 KiB before redaction. A changed file returns no replacement content. Unknown or cross-page artifact IDs return 404.

This endpoint does not accept an arbitrary path, expose workspace files as static content, retain every old version, or serve downloads/binary/command-created artifacts. Version verification happens when opened; an already displayed preview is a snapshot.

## Explicit example mode

`per-pulse --example` uses a separate example data directory and visibly returns `example:true`. Sample content is authored example material, not AI output. Ordinary startup begins with the user's own data directory and does not seed examples.
