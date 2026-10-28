# Per Pulse API contract (English v0.1)

Endpoints are same-origin under `/api` and return JSON. Writes use JSON except for the bounded raw file upload described below. No client-supplied model credentials. Poll page views while their status is `queued` or `working`; no fake/model output is generated offline. Session data is the inherited Pulse World/Kernel state, with lightweight page-reference metadata in the same World store.

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
  historyBeforeMessageId: string | null; // oldest recent message, when older history exists
  earlierSourceMessageId: string | null; // one older selected source, separate from recent messages
};
type MessageHistory = {
  pageId: string;
  messages: Message[]; // at most 100, in native chronological sequence
  hasMore: boolean;
  beforeMessageId: string | null; // pass this to read the next older range
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
- `GET /api/pages/{id}/messages?beforeMessageId=<native-event-id>` ->
  `MessageHistory`, containing up to 100 messages strictly before that message.
  Start with `PageView.historyBeforeMessageId`, then use each returned
  `beforeMessageId` until it is null. The native sequence cursor remains stable
  when newer messages arrive; timestamps and offsets are not pagination keys.
  The cursor must itself be a visible message in the same page's authorized
  World/Center/committed-Engram lineage. Missing or foreign messages give 404;
  an omitted or empty cursor gives 422. The earliest message returns an empty
  history range. Every read retains current statuses, references and source
  identities, without admitting a turn or changing the main page's status.
- `GET /api/pages/{id}/messages/search?q=<text>&beforeMessageId=<optional-id>`
  returns the same bounded shape plus `query`, with up to 100 matches **newest
  first**. The trimmed query is 1–200 characters. Matching is a literal Unicode
  case-insensitive substring; `%`, `_`, backslashes and quotes have no wildcard
  or SQL meaning. Recent and older conversation share the same native
  World/Center/committed-lineage predicates. Older ranges use the last returned
  `beforeMessageId`; foreign/missing cursors give 404. No match returns an empty
  list, and reading never starts a model or creates a session. The browser's
  **Find a message** opens each result at its original message and retains drafts.
- `GET /api/pages/{id}?sourceMessageId=<native-event-id>` -> the same recent bounded
  window, plus one selected older message if it is in this page's authorized
  World/Center/committed-Engram lineage. This is a read-only source anchor, not
  whole-history pagination: `earlierSourceMessageId` names the extra item,
  or is null if the source was already recent. A foreign, missing, or wrong-page
  message returns 404. Intervening messages are not implied or copied.
  Home/Work deep links pass this native message ID without changing identity,
  stored drafts, route, or the user's return position.
  A pinned older source does not change `historyBeforeMessageId`: history
  browsing still begins before the recent window, so intervening messages
  remain reachable.
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

Schema 10 adds `(world_id, center_id, seq)` to the existing causal ledger for
these bounded recent/older reads. The normal migration boundary backs up an
older database before applying this index-only upgrade; message rows, turn
receipts and identities are unchanged. Versions 7, 8 and 9 are covered by
preservation and idempotent-reopen tests. Downgrading the application to a
version that only understands schema 9 is not supported.

The optional `python tests/per_pulse/benchmark_message_history.py` compares the
actual native page queries with and without this index in a disposable
100,000-row fixture. On the development host, median bootstrap time fell from
237 ms to 7 ms, and a 100-message older range from 18 ms to 1 ms (20 measured
reads after five warmups). These are local synthetic measurements, not a
latency guarantee for other hardware or histories.

Message search scans matching-context native text; it is not a separate search
index or transcript copy. `python tests/per_pulse/benchmark_message_search.py`
measures common, old-only and absent queries in a disposable dense 100,000-row
fixture. An absent query must inspect the whole selected context, so its cost
grows with conversation size even though the returned range is bounded.

## Report an outcome of a lesson

`POST /api/pages/{page_id}/posture/experiences/{experience_id}/feedback`

```json
{
  "expectedRevision": 2,
  "verdict": "useful",
  "evidence": "The first interaction let me finish the task without optional setup.",
  "resultMessageId": "<completed-native-result-in-this-page>"
}
```

`resultMessageId` is optional; omit it or send `null` for an observation with
no linked result. A selected message must be a settled native assistant result
in the reporting page's World, context and committed subject lineage, backed
by its accepted, settled Kernel turn. A missing or foreign-page message gives
404; a user input or a result without completed native provenance gives 422.
These failures do not change the lesson. Old results outside the most recent
500 messages remain valid. Stale `expectedRevision` gives 409.

The source page may report its own lesson. Another page may report only an
active, explicitly shared lesson of the same subject. Useful feedback leaves
it active; harmful or withdrawn feedback pauses it. Editing, sharing and
reactivating remain source-page operations.

The response is `{experience: ...}` projected for the reporting page. Only
the source page's private feedback history includes the report's result ID,
native turn ID, SHA-256 of the result's UTF-8 text and provider evidence class.
Result text is never copied into that history. Other pages receive no original
encounter, source record or feedback history, including in the write response.
The report retains `kind: "operator_report"`: linking a real result does not
prove that the model used the lesson, that the lesson caused the outcome, or
that a test transport was a real provider.

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

## Hold or resume one page's background admission

`PATCH /api/pulse/contexts/{page_id}` with `{"paused":true}` holds
new *background* turns for only the selected Home or Work context;
`{"paused":false}` resumes them. The page must belong to the current
continuing subject, and Pulse must be enabled. Only JSON booleans are
accepted. The response is `{pulse:PulseState}`.

The existing Pulse controller stores `held_contexts` alongside the
global pause, and `GET /api/pulse` projects `heldContexts` with exact
`center_id` and `pageId` identities (no missing Work is renamed Home).
Queued inbound activity reports `attention_held`. Ordinary foreground
input, settled outward influence, the admission quota, and other local
contexts remain unaffected. Holds survive restart; releasing a hold
continues the original queue without creating a new engine.

This **operator** control does not pretend to implement the model's
action/turn-gated attention tool or retained-thought store. Those still
require settlement and correction ordering, and monetary budgets remain
the Harness line's responsibility.

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

## Workspace materials

All three material routes require `X-Per-Pulse-World` and
`X-Per-Pulse-Service-Instance` from the current bootstrap. Missing, partial or
stale identity returns `409 workspace_changed` before file access. Responses
include both identities. Refresh bootstrap after a service restart; never
silently retry a write against a different World.

- `GET /api/workspace/files?path=` lists a bounded native workspace folder.
  The response includes `path`, `entries` (`name`, `path`, `kind`, `bytes`),
  `truncated`, `limit_bytes` and `scope: "shared_native_workspace"`.
- `GET /api/workspace/files/content?path=uploads/example.txt` reads a single
  regular UTF-8 file up to 1 MiB, returning `path`, `content`, `bytes` and
  `sha256`. This is current file content, not a historical artifact receipt.
- `POST /api/workspace/files?name=notes.md` accepts raw bytes with
  `Content-Type: application/octet-stream`. Its streamed body is limited to
  1 MiB, independent of Content-Length. Success is `201` with `path`, original
  `name`, `bytes`, `sha256` and `source: "user_upload"`. Empty, binary,
  oversized, protected-path and credential-shaped uploads are rejected before
  publication. A unique native `uploads/` file is created without replacing
  an existing file. Short extensions survive long Unicode file names.

These operations reuse `WorkspaceFiles`; they create no Kernel sessions,
turns, model read receipts or artifacts. Explicit user upload is separate
from granting model mutation permissions. Uploaded files are shared across
pages; page conversations remain scoped. The browser's **Add to draft** action
uses a native attachment receipt, preserving existing draft text and page links.
Sending verifies that selected files still match their recorded bytes and
digests. This reference is not a model read receipt: native read tools return
their own observations when a configured model actually opens the file.

### Native message attachments

- `GET /api/pages/{id}/materials` lists that page's latest 100 receipts,
  with `truncated` and `limitBytes`. Older message attachments remain readable.
- `POST /api/pages/{id}/materials?name=notes.md` creates a file and its page
  receipt from original octet-stream bytes. It requires `X-Per-Pulse-World`,
  `X-Per-Pulse-Upload-Id` (32 lowercase hex characters) and
  `X-Per-Pulse-File-SHA256`. First-party callers also send the current service
  instance; older World-only native callers remain supported. An exact retry
  returns the same receipt, while changed bytes, name or owner conflict.
- `POST /api/pages/{id}/materials/reference`, with the current World/instance
  headers and `{"path":"uploads/notes.md","sha256":"<64 hex characters>"}`,
  verifies an existing file and creates or reuses that page's own receipt.
  It copies neither file bytes nor another page's conversation.
- `GET /api/pages/{id}/materials/{materialId}` verifies ownership and current
  version, then returns `material`, bounded `content`, `redacted`, `truncated`
  and `previewLimitBytes`. A changed or missing source is refused. The preview
  limit is 64 KiB, independently of the 1 MiB original-file limit.

Upload and reference responses contain `material` and `repeated`; first-party
responses also carry World/instance identity. A material has `id`, `pageId`,
`name`, `path`, `bytes`, `sha256`, `source` and `createdAt`. The workspace retains
at most 1,024 receipts. File publication and receipt publication are separate
durable effects, so an unconfirmed upload must be inspected before retrying.

`POST /api/pages/{id}/messages` accepts up to five distinct receipt IDs in
`materials`, alongside ordinary `content` and `references`. A file-only input
may have empty content. The canonical input records an ASCII JSON source
footer; message projection exposes `materials` and `materialSuffixLength` so
the UI can show source chips without changing the original input. An explicit
`includeMaterials:true` on selected Return Home retains access through the
original source page; it does not transfer upload ownership.

The browser never executes file HTML. It limits previews to the first 64,000
characters and labels shortened previews. An upload whose response is lost
can have completed; inspect the existing uploads before choosing to repeat it.
Markdown uses a lazy reading view with a source-text toggle. Raw HTML stays
escaped, images become descriptions without remote requests, and only explicit
absolute HTTP(S) links become external navigation. Relative and executable URLs
remain text. The same reading component presents confirmed Markdown artifacts.

## Native execution and file results (opt-in)

`GET /api/pages/{id}/execution` observes a native turn with exact-action approvals. `POST /api/pages/{id}/execution/stop` takes `{turnId}`; `POST /api/pages/{id}/execution/actions/{actionId}` takes `{decision: 'allow_once'|'deny'|'cancel'}`. Each returns a source-bound receipt and current page view. Scope is derived from the page server-side. Completed effects are preserved separately from a failed continuation.

Execution also includes `uncertainActions`, the most recent 32 native
`UNCERTAIN` action records in this page's current and verified committed
predecessor Kernel sessions, newest generation and action first.
Each contains `actionId`, `turnId`, `toolName`, `status: 'uncertain'`, bounded
and redacted `arguments`, `argumentsTruncated`, and `argumentsRedacted`.
`uncertainActionsTruncated` explicitly signals older omitted records. Empty
histories return an empty array without creating a session or runner. These are saved
outcome records, not fresh approvals or confirmed file results. They remain
visible after an explicit Stop, later successful work, and native generation
succession in the same Work; Stop cannot undo
an effect. The UI exposes them in **Actions with unconfirmed outcomes** only
on their originating page, without a retry or approval button.

`GET /api/pages/{id}/artifacts` projects bounded successful native `mutate_file` receipts; `GET /api/pages/{id}/artifacts/{artifactId}` verifies original action/page, regular text file and content SHA-256 before presenting a bounded redacted preview. A changed or missing file has no substitute preview. Page view includes `execution`, `artifacts`, `artifactsTruncated`; provider includes `workspaceActions`. See [Harness foundation](harness-foundation.md) for exact contracts.

`GET /api/pages/{id}/artifacts/{artifactId}/download` repeats that same exact
action/page and file-version verification, then returns complete captured bytes
as `application/octet-stream` with attachment `Content-Disposition` and
`X-Per-Pulse-Content-SHA256`. It never gives the HTTP server a mutable file path
to reopen after checking. The existing 1 MiB read ceiling and native mutation
durable-content limit remain unchanged. Download does not create a model turn.

An overwritten file returns `409 artifact_version_changed`. Unavailable,
binary, oversized or protected files preserve the native file error code and
status. Files needing redaction or containing stored redaction/truncation
markers return `422 artifact_download_redacted`. Wrong-page and unconfirmed
action IDs remain `404 artifact_not_found`. The response also carries the exact
digest as an ETag; the browser checks the received bytes before saving them.

File and uncertainty observation follows only the native committed generation
lineage in the same World and page context. Prepared or ambiguous transitions,
other pages, and delegated team histories grant no access. List bounds apply
across the whole lineage. Unverifiable ancestry returns
`execution_history_unavailable` (503). Stop and approval controls still require
the current session; reading an old result never reactivates its action.
