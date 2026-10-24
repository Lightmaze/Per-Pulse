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
  attention: AttentionState | null;
};
type Bootstrap = {
  name: "Per Pulse";
  version: string;
  example: boolean;
  workspaceId: string; // native World ID, stable across restarts
  homeId: "home";
  provider: ProviderState;
  pages: PageSummary[];
  pulse: PulseState;
};
type AttentionState = {
  pageId: string;
  title: string;
  available: boolean;
  note: string;
  drive: number;
  excitation: number;
  inhibition: number;
  readiness: number;
  revision: number;
  pulseCount: number;
  resultCount: number;
  lastPulseAt: string | null;
  pendingEventId: string | null;
  lastResultId: string | null;
  evidence: { eventId: string; pageId: string }[];
};
type PulseState = {
  enabled: boolean;
  remaining: number;
  configured: boolean;
  contexts: AttentionState[];
  connections: { sourcePageId: string; targetPageId: string; weight: number }[];
  sequence: number;
  derivedCount: number;
  lastAdvancedAt: string;
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

## Local attention and continuation

All attention writes return `PulseState`. Unknown fields are rejected. Numeric inputs must be finite numbers, not strings or booleans.

| Endpoint | JSON body | Effect |
| --- | --- | --- |
| `GET /api/pulse` | None | Read retained fields, connections, and remaining allowance without advancing them. |
| `PATCH /api/pages/{id}/attention` | `{"note":"Explore the unresolved tradeoff.","drive":1}` | Retain a thought of 1–4096 characters; drive is in `[0,1]`. Drive `0` lets it rest while retaining the note. Cancels that page's queued local activation. |
| `POST /api/pulse/run` | `{"maxPulses":6}` | Replace the remaining shared allowance with 1–100 continuation turns; default is 6. Requires a configured model outside example mode. |
| `POST /api/pulse/pause` | `{}` | Set remaining allowance to zero and cancel queued local activations and their admitted native Work descendants. Running turns may finish. |
| `POST /api/pulse/connections` | `{"sourcePageId":"home","targetPageId":"work-id","weight":1}` | Upsert a directed connection between two available, distinct pages. Weight is in `[-1,1]`; zero removes the connection. Maximum 256 connections. |

The allowance counts native continuation admissions, including derived Work starts and reports, rather than HTTP requests or tokens. Ordinary user requests are not charged to it. A prepared native Work request without allowance stays deferred until an explicit new allowance permits delivery. Admission and debit are atomic; retries cannot charge the same request twice.

Restart retains field state and pauses the allowance. Polling does not run a model. Saving a thought and editing connections work disconnected; `run` returns `409 provider_unconfigured`. Navigation references do not automatically become attention connections.

## Errors

Errors use `{error:{code:string,message:string}}` with 400/404/409/422/503. Show the message in English. Never silently switch to an example or fabricated assistant response after an error.

## Explicit example mode

`per-pulse --example` uses a separate example data directory and visibly returns `example:true`. Sample content is authored example material, not AI output. Ordinary startup begins with the user's own data directory and does not seed examples.
