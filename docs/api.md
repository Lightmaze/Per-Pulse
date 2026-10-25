# Per Pulse API contract (English v0.1)

All endpoints are same-origin JSON under `/api`. No client-supplied model credentials. Poll page views while their status is `queued` or `working`; no fake/model output is generated offline. Session data is the inherited Pulse World/Kernel state, with lightweight page-reference metadata in the same World store.

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
  activation: "propagation" | null;
};
type ProviderState = {
  configured: boolean;
  model: string | null;
  label: "AI connected" | "AI disconnected";
  detail: string;
};
type PageView = {
  page: PageSummary;
  messages: Message[];
  references: PageReference[]; // outgoing, including references with a null sourceMessageId
  backlinks: PageReference[]; // incoming
  breadcrumbs: { id: string; title: string }[];
  provider: ProviderState;
  hasMore: boolean;
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
- `GET /api/pulse` -> local pulse settings, rolling admission budget, context
  activity, directed connections, full durable `queue` totals, and the most
  recent 20 background activations. `queue` contains `queued`, `running` and
  `oldestQueuedAt` (null when empty). Queued recent activations include
  `deferredReason` from the real context admission gate; null does not promise
  an immediately available worker. See [pulse-core.md](pulse-core.md#inspect-retained-work).
  Each recent activation now also includes sourceEventId, sourceCenterId
  and sourcePageId derived from its actual parent causal event (when present).
  A missing source has null identity fields; a real null Home source resolves
  to "home". The source and receiver are different fields and must not be
  conflated in UI, memory or provenance reports.
  Contexts, directed connections and activations retain their native center
  identifiers. A `pageId`, `sourcePageId` or `targetPageId` is `"home"`
  **only** for the actual null (Home) center; when a historical Work center
  has no current page projection, its page ID is `null`. Consumers must not
  navigate such an entry to Home or silently assign it Home provenance.

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

## Pause or resume native background activity

`PATCH /api/pulse` with `{"paused":true}` holds background admission;
`{"paused":false}` resumes it. This only applies when `--pulse` is enabled
(otherwise 409) and the field must be an actual JSON boolean. The response
is `{pulse:PulseState}`. Its `paused` flag is persisted in the existing
ContextPulse checkpoint. Queued native background events report
`operator_paused` as their `deferredReason` during a hold. Foreground
messages and already-running model turns are unaffected.

The hold does not discard results, move the observation cursors or reset
the rolling allowance. A restart of a held World remains held until
released; the same causal graph, per-page histories and queued influence
then resume. This is not PR #6's full per-page attention/held-thought tool
nor a token or currency budget.

## Connect local pulse contexts

`POST /api/pulse/connections`

```json
{"sourcePageId":"home","targetPageId":"existing-work-id","weight":0.6,"kind":"excitatory"}
```

Response `201`: `{connection, pulse}`. `weight` is finite and in `[0,1]`;
`kind` is `excitatory` (default) or `inhibitory`. The endpoints must be distinct
contexts of the same continuing subject. These computational connections are
separate from navigation references. They act only on results settled after
the connection was created; no prior conversation is replayed.

`DELETE /api/pulse/connections` accepts
`{"sourcePageId":"home","targetPageId":"existing-work-id"}` and returns
`{removed:boolean, pulse}`. Deleting or changing an edge cancels its queued
deliveries, including an unsent fused pulse that contains that edge. Running
and settled turns retain their original causal evidence.

Background computation is opt-in through `per-pulse --pulse`; it requires a
configured provider. The budget counts native background admissions over a
rolling hour, including failed or cancelled admissions. It is not a token or
currency limit. Restart does not refill it. Foreground user requests bypass
the background budget and local inhibition. Disabled pulse mode holds queued
background deliveries for a later enabled run.

Propagated result messages have `role:"system"`, `activation:"propagation"`,
and the exact source page/message. Multiple sources can converge into one
target turn while retaining separate source rows. See [pulse-core.md](pulse-core.md)
for the causal, restart, inhibition, and convergence contracts.

## Errors

Errors use `{error:{code:string,message:string}}` with 400/404/409/422/503. Show the message in English. Never silently switch to an example or fabricated assistant response after an error.

## Explicit example mode

`per-pulse --example` uses a separate example data directory and visibly returns `example:true`. Sample content is authored example material, not AI output. Ordinary startup begins with the user's own data directory and does not seed examples.
