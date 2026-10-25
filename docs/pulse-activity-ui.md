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

This is **global operator pause**, not PR #6's complete per-page
attention inspect / hold / release tool. That still requires exact
action settlement, user correction precedence and controller reuse.
