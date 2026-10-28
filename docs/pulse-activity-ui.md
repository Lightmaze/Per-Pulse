# Native Pulse activity in the session workspace

This increment is stacked on PR #10 (which itself preserves historical Work
source identity atop PR #7 and #2). It is the first UI bridge to the existing
Pulse controller, **not** an import of PR #6's separate activation coordinator.
There remains exactly one native World, causal ledger and context graph.

## User path

Open **Activity** from Home or a Work page. The dialog reads the actual
\`GET /api/pulse\` status and refreshes while open. It shows the rolling
admission count, durable queued/running counts and up to 12 recent activities.
The source Work page is navigable when the current page list contains it.
An unresolved historical Work is *not* labeled Home, and is not given a
fabricated link: its native center identifier remains visible.

From a selected page the user can connect it to another page using the
native excitatory or inhibitory edge endpoint, and remove outgoing edges.
The graph is causal, not the same thing as the clickable session references:
navigation alone does not open a background execution turn. An edge change
uses the existing permission and revocation path. It does not copy the
source Work's transcript or grant its tools to the receiver.

The UI is read-only for activity and allowance. Creating or removing a
connection is an explicit user operation. There is no new polling service,
scheduler, provider transport, model loop, session store or second budget
ledger. The panel does not claim continuous provider activity when Pulse
mode is disabled or a model is not configured.

## Acceptance and remaining integration

- The native HTTP regression checks connect/read/remove, exact causal edge
  identity, disabled-mode empty activity and rejection of missing targets.
  The PR #10 regression separately covers unresolved Work-to-Work provenance.
- The frontend build checks TS types and bundle integration. **Browser
  interaction testing, real-provider work and user quality are not implied by
  a passing build.** Record each distinctly.
- PR #6's staged attention **inspect/hold/release** and Keep thinking must be
  adapted into this *same* controller instead of creating a second graph.
  They must honor action/turn settlement, cancellation, operator precedence,
  paused influence and the PR #7 queue/fairness semantics.
- Receiving-cohort token/currency cost belongs to Core Harness and is not
  synonymous with the rolling number of background admissions.
- UIUX source/return behavior, posture experience links and EN/ZH localization
  remain separate PRs until a combined checkout verifies them together.

The central acceptance scenario remains: Home goal -> local Work contexts ->
cross-context evidence -> correction changes a later action -> an approved
real tool effect and verifiable source-linked artifact -> return to the
original reading position. This dialog supports inspection/control of one
edge in that experience, but does not complete the entire scenario.

## Native operator hold (next stacked increment)

`PATCH /api/pulse` persists an operator pause on the **same**
ContextPulseController. The Activity dialog shows the actual held state
and provides Pause background / Resume background if Pulse mode is on.
This is an admission gate, not process shutdown or a new scheduler.

Both durable result cursors and queued causal events remain intact.
Already-running turns can settle; their results begin cross-context
influence only after release. The one-hour admission quota is neither
reset nor replaced. The native test verifies foreground input during a
hold, restart while held, one-time source-attributed delivery and quota.

Global operator pause remains distinct from page-scoped control.

## Scoped operator hold on the same ContextPulseController

`PATCH /api/pulse/contexts/{page_id}` accepts only `{"paused": true|false}`.
A page must belong to the active continuing subject. The existing native
checkpoint persists the `held_contexts` set and projects `heldContexts`
in `GET /api/pulse`. Older checkpoints without the field still load.

The Activity panel can hold or resume the current Home/Work page separately
from the global pause. The native engine admits ordinary foreground messages
as before. Inbound background work reports `attention_held`; it retains
source event identity, queue position and admission quota. Already-running
turns may finish and their settled effects can still influence other local
contexts, but a held target does not start a new background turn. Restart
preserves the hold and release resumes the original queue in place.

This is the **operator** subset of PR #6's page-local attention work,
not the model's action/turn-gated inspect/hold/release tool, free-form
retained-thought store or Keep thinking activation. Model-originated changes
must still be applied only after the original action and turn settle,
must respect later user corrections, and must not introduce a second
controller or authority. Monetary admission remains with Core Harness.


## Reliable operator control while Activity is polling

Activity periodically reads the authoritative native Pulse status. A GET may
have started before an operator clicks Pause, Hold or Connect and only return
*after* the PATCH/POST/DELETE response. Rendering that old GET would show the
old control state even though the native write succeeded. Out-of-order GETs can
also replace a newer Activity snapshot.

The client now fences reads by a local request generation and accepts only
the newest GET issued outside a pending mutation. The mutation response
remains the visible authoritative state; duplicate commands are blocked
before the next React render. When a mutation response is lost, the UI says
the outcome is **unconfirmed** and re-reads the server after releasing the
write fence. It does not invent a rollback, re-send a potentially committed
operation, or create a second Pulse authority.

The Node regression for request ordering covers stale polling across an
operator write, overlapping GETs, duplicate clicks, and reading again after
an uncertain response. Native persistence, browser interaction and actual
model usefulness remain separate checks; this regression exercises only
the client-side ordering rule.

## Activity is bound to one World and one service instance

The first-party Activity dialog reads the atomic native `GET /api/pulse/observation`
response and checks both the persistent `workspaceId` and live
`serviceInstanceId` against the current workspace bootstrap before exposing
source navigation or operator controls. Mismatches offer a full workspace
reload; the current unsent Work draft remains in its browser-local scope.

First-party pause/resume and connection writes carry the
`X-Per-Pulse-World` and `X-Per-Pulse-Service-Instance` headers.
Partial or mismatched identities receive a typed HTTP 409
`pulse_workspace_changed` without editing the original Context Body.
Legacy native clients that omit *both* headers keep their existing contract.
This is a stale-tab safety fence, not authentication, a new scheduler, or an
expanded model permission boundary. It does not establish provider costs.
