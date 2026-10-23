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
};
type Bootstrap = {
  name: "Per Pulse";
  version: string;
  example: boolean;
  workspaceId: string; // native World ID, stable across restarts
  homeId: "home";
  provider: ProviderState;
  pages: PageSummary[];
  postureEnabled: boolean;
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

## Experience learning

Start with `--posture` or `PER_PULSE_POSTURE=1`. All experience routes share the
same origin, JSON-only mutation boundary, World storage, and page ownership as
the conversation API. Example mode never enables learning.

- `GET /api/pages/{id}/posture` returns `{enabled:boolean, experiences:[]}`.
- `POST /api/pages/{id}/posture/experiences` creates an attributed lesson and returns `201 {experience}`.
- `PATCH /api/pages/{id}/posture/experiences/{experienceId}` revises a lesson and returns `{experience}`.
- `POST /api/pages/{id}/posture/experiences/{experienceId}/feedback` records operator feedback and returns `{experience}`.

Creation requires `sourceMessageId` identifying a settled native assistant
result and `feedbackMessageId` identifying a later user stimulus in the same
page. The host captures the original encounter and provider evidence class.
The body also contains `title`, `lesson`, `conditions`, `counterexamples`, and
`shift: {understanding,candidates,delivery}`. `conditions` is text or a recursive
`{all:[...]}`, `{any:[...]}`, or `{not:...}` expression. The optional `scope` is
`context` by default; `subject` explicitly shares the abstraction across pages.
The model's native tool always creates context-local records.

A lesson includes `id`, `revision`, `active`, `scope`, its content,
`record_type`, `canEdit`, `sourcePageId`, and timestamps. At its source page it
also includes `encounter`, `source`, and `feedback`. A transferred projection
omits those raw fields. A test transport source stays `constructed_fixture`.
Source attribution does not verify that a user follow-up is semantically a
correction or prove an improvement.

Revision bodies use `expectedRevision` and `changes`. Editable fields are
`title`, `lesson`, `conditions`, `counterexamples`, `shift`, `scope`, and
`active`. Provenance and the original encounter cannot be overwritten.

```json
{"expectedRevision":1,"changes":{"active":false}}
```

Feedback bodies use `expectedRevision`, `verdict` (`useful`, `not_useful`,
`harmful`, or `withdrawn`), and concrete `evidence`. Harmful or withdrawn
feedback pauses the lesson. Every successful mutation increments the revision;
stale updates return `409 experience_revision_conflict`. Shared lessons can be
changed only from their source page. Disabled learning returns
`409 posture_disabled` for writes, while reads return an empty disabled view.

See [posture engineering](posture-engineering.md) for the native model-tool
lifecycle and the boundary between pausing new activations and retaining
already-existing conversation history.

## Explicit example mode

`per-pulse --example` uses a separate example data directory and visibly returns `example:true`. Sample content is authored example material, not AI output. Ordinary startup begins with the user's own data directory and does not seed examples.
