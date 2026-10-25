# Per Pulse API contract

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
type WorkspaceSettings = {
  responseLanguage: "auto" | "en" | "zh-CN";
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
  settings: WorkspaceSettings;
};
```

## Read

- `GET /api/bootstrap` -> `Bootstrap`.
- `GET /api/pages/{id}` -> `PageView` (latest bounded messages).
- `GET /api/health` -> `{status:"ok", providerConfigured:boolean}`.
- `GET /api/settings` -> `WorkspaceSettings`.

Scope browser drafts, scroll positions, and navigation history by `workspaceId`
as well as page ID. Ordinary and example workspaces have distinct World IDs,
even when they use the same server origin and the shared page ID `home`.

## Language settings

`GET /api/settings` returns the current workspace preference:

```json
{"responseLanguage":"auto"}
```

`PATCH /api/settings` accepts an explicit response-language preference and returns the saved `WorkspaceSettings`:

```json
{"responseLanguage":"zh-CN"}
```

Allowed values are `auto`, `en`, and `zh-CN`; new and existing workspaces without a saved choice default to `auto`. PATCH requires exactly the `responseLanguage` field. Missing or unknown fields, `null`, non-string values and unsupported values return `422` with error code `invalid_request`. The setting lives in the existing World component state and survives service restarts. It is scoped to the workspace, including a separate preference for the example workspace. `GET /api/bootstrap` includes the same value as `settings.responseLanguage`.

An explicit language instruction in the current task takes priority. Otherwise `en` requests English responses, `zh-CN` requests Simplified Chinese responses, and `auto` leaves language to the task and conversation. The preference is sampled at the first model boundary of each new turn. That turn's later tool rounds and approval continuation keep the same preference; changing settings applies to the next new turn. It does not restart an active request, queue a new conversational message, translate old history, or authorize an action. Changing the preference is valid while AI is disconnected and makes no provider request itself.

The interface preference (`system`, `en`, or `zh-CN`) is browser-local and is not an API setting. The browser localizes product labels using stable IDs, status values and error codes. API IDs, field names, status values, user-authored titles, message content and reference labels retain their original values. See [language support](language-support.md).

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

Errors use `{error:{code:string,message:string}}` with 400/404/409/422/503. Keep `code` stable across languages. The browser translates recognized error codes into the selected interface language and preserves the server message for unknown diagnostics. Never silently switch to an example or fabricated assistant response after an error.

## Explicit example mode

`per-pulse --example` uses a separate example data directory and visibly returns `example:true`. Sample content is authored example material, not AI output. Ordinary startup begins with the user's own data directory and does not seed examples.
