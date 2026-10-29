# Pulse continuity: retained influence and queue progress

This follow-up continues PR #2 at `aaf754cb25b7ad2d83accbd97522d813ecda0e39`.
Pulse remains local activity inside one continuing Context Body, using the
existing World, causal ledger, Dendrite, scheduler and Harness Kernel.

## The long-running failure addressed here

The native engine previously rebuilt its queue from the oldest 500 queued
events. The context admission filter ran later. Five hundred old deferred
inputs could therefore hide a newer user message or an eligible sibling
context on every refresh, even though neither was forbidden from executing.
The two queue reads also omitted the World filter.

A second issue occurred when a connected local context was paused. The
coordinator used active-only eligibility both to execute work and to preserve
completed influence. It skipped a paused endpoint and advanced its result
cursor. Resuming the context could not recover the missed influence.

The retention fix separates membership in the continuing body from immediate
execution eligibility. Active and paused subject-owned contexts can retain
canonical deliveries; only active targets can claim them. Quota, source proof,
edge revision, revocation and committed succession keep their existing
identities and boundaries. The same rule retains decaying inhibition at its
original event time.

The status endpoint now reports the whole durable queued/running backlog and
explains the context gate for recent queued activations. A clear context gate
does not promise immediate worker admission.

## Queue discovery and Dendrite formation

Queue discovery is scoped to this World and advances through bounded keyset
pages. It reads up to 500 new queued rows, separately discovers up to 64
foreground stimuli, and retains at most 500 members of unfinished native
cohorts. The cursor is a reconstructible read position, never a durable
acknowledgement or authority to drop an event. Reopening the runtime restarts
discovery from the ledger. Each sweep ends at the queue head captured when it
began; a producer continuously appending new work cannot prevent a resumed
older context from being revisited.

Unfinished cohorts keep their frozen policies and original arrival times
across discovery pages. A partial page cannot dispatch members individually
before exact-cohort validation succeeds. Newly formed aggregates are reread by
their IDs in the same refresh, rather than disappearing behind the next page
of backlog. After formation, the scheduling cache allocates at most 64 entries
per execution context and 500 in total. Foreground inputs, including native
external aggregates, get cache space before deferred background inputs from
that same context. The durable scheduler and claim
transaction still decide what actually runs.

Window validation examines the native prefix through the proposed close time,
not unrelated future windows. Existing aggregate roots and generation-summary
controls do not occupy raw-window capacity. The native maximum of 500 raw
members in one time cohort is unchanged. An oversized cohort is retained and
reported as a conflict, not silently split into independent model turns.
Such an oversized unsealed cohort can still hold later windows of the same
subject. Supporting it requires an explicit capacity-window contract and is
not claimed by the ordinary multi-window backlog fixes here.

## Integration boundaries

PR #6 (`feat/pulse-core-activation`, `6b8396c6d6f71b6fd704477f435b81371dd78702`)
contains useful attention intent and user-control work, but also introduces a
second local activation coordinator and a different `/api/pulse` contract.
Its source has been compared with this route; it is not merged here.

Continue with one authoritative coordinator and graph. Adapt the useful
interfaces to the existing runtime instead of scheduling both implementations:

| Goal | Next concrete work | Acceptance evidence |
| --- | --- | --- |
| Intent can guide retained local work | Adapt staged `attention inspect/hold/release` to the native controller, with source scope, action and turn receipts, and user revision precedence. | A cancelled turn cannot retain an intention; a later user edit wins; inspection retrieves exact source evidence. |
| Background capacity follows actual execution | Agree a receiving-cohort budget contract with Core Harness, including native Work descendants. | Two source deliveries fused into one turn settle execution once; influence admissions remain individually attributable. |
| Failure does not create free retries | Preserve consumed influence allowance, settle execution capacity once, and hold unknown provider usage until reconciled. | Restart/cancellation/failure tests preserve the original reservation and receipt identities. |
| Ongoing work produces useful outcomes | Run a bounded real-provider project with reading, experiment and synthesis contexts. | Record new usable evidence, changed decisions, source lineage, latency and measured provider cost over continued work. |
| People can inspect and steer activity | Reuse the UIUX source/return flow and adapt #6 controls to the shared state contract. | Opening activity leads to its source, return preserves position, and a local pause accurately displays retained inputs. |

Provider tokens/currency are not the existing rolling admission quota. Cost
integration belongs to the receiving execution/cohort, not each influence
edge. This change adds no second budget ledger or model loop.

## Verification and handoff

Final local validation on 2026-10-25:

| Check | Result |
| --- | --- |
| Complete Python regression suite | 117 passed in 24.82 seconds |
| Queue liveness regressions | 10 passed, including before/after failures for the original 500-row blockage, same-context cache blockage and an endlessly extended scan |
| Retained influence regressions | 12 passed; the six initial pause-retention cases failed before the fix |
| HTTP adapter/Kernel pause-restart-resume path | Passed; request counts 2 to 3, unchanged while paused and after restart, then exactly 4 after resume |
| Frontend TypeScript check and production build | Passed |
| Python wheel and sdist build | Passed |
| Installed wheel outside the source tree | Passed; bundled UI, schema 9 and both new indexes, queue status, retained World/edge/user-event identities across reopen |
| Whitespace/conflict-marker check | `git diff --check` passed |

Tests use real SQLite state, causal turns and the normal HTTP adapter/Kernel
with explicit synthetic test transports. No external model is configured in
this development environment. Model usefulness and paid provider cost remain
the next live-validation step.

Schema 9 adds indexes for bounded Dendrite time-window validation and
World-scoped queue discovery. Migration
tests reconstruct populated schema 7 and 8 databases, verify the upgrade backup,
retain all causal and turn rows, check the index plan and reopen idempotently.
The migration sequence here is based on PR #2; a later combined branch must
reconcile any independent schema additions before integration.

The example HTTP service responds to its local health check. The available
cloud browser cannot reach the executor's loopback address, and the local
browser extension is not attached in this session. Browser flow acceptance
therefore remains open; it is not inferred from an API test or frontend build.

Run the regression suite and builds from the repository root:

```sh
python -m pytest -q --tb=short
npm --prefix web run typecheck
npm --prefix web run build
python -m build --no-isolation
```

All committed content is source, English documentation or synthetic fixtures.

## Limit main model rounds per native local activation

`--pulse-main-rounds N` (default 4, 1..16) bounds primary Kernel model
rounds for causally verified spontaneous or propagated World turns. The
original TurnRunner applies this ceiling on resume/recovery; foreground
user turns keep their existing allowance. A single activation can still
perform several model/tool exchanges. This is not a new executor or a
cron/resume interpretation of Pulse.

This is **not a provider spending cap**. Any single request can overrun
estimated tokens; optional posture, expert and other side computations
are outside the primary-round ceiling. Provider usage may be delayed
or missing. Long-lived Context Body state and attributed local influence
continue across separately admitted activations.

## Explain why new background activity cannot currently enter

The same native Pulse status projection now gives Home/Work Activity a bilingual
admission explanation when either the rolling count is exhausted or the
remaining nominal token allowance cannot cover one more reservation. This is
only a read-only client projection of the existing native count and budget;
no new scheduler, quota, model call or permission is introduced. Existing
queued/running activations can still proceed, and human foreground turns are
not blocked by this admission envelope. Paused, disabled and disconnected
states keep their existing independent explanations.

Node regressions cover count exhaustion during already-running work, the
one-reservation token boundary, absent optional token envelope, and pause /
disconnect precedence. Neither a successful build nor synthetic HTTP
regressions prove a real provider charge limit or useful independent activity.


## Interrupted turns keep their completed-provider lower bound

An accepted native Kernel turn may finish one or more primary, CoverMe, or
expert provider requests and then fail on a later request/tool. It has no
settled assistant result, so the original background admission used to retain
only one nominal token reservation even when earlier completed usage already
exceeded that reservation. The original Kernel now carries the aggregate
completed-request *lower bound* through its typed Harness failure into the
same fenced World failure transaction. The existing Pulse admission read
charges the **maximum of its full unknown reservation and that observed
lower bound**; it never treats partial usage as a complete provider bill.

`providerTokenBudget.partialKnownTokens` reports the observed lower bounds
for unresolved failures, and `partialKnownExcessTokens` is the increment
beyond their original reservations. The Activity panel explains both values
in English and Chinese. These numbers do not include unreported/unfinished
provider requests; the reservation is not released. They also are not a
per-request, account-wide, or monetary hard cap. Failure before Kernel can
produce a durable completed-round receipt remains reservation-only.

The native World/Kernel/SQLite regression checks that this accounting remains
in force after restart and cannot consume the human's foreground allowance.
It uses synthetic fixtures; it does not establish real-provider token rates,
billing, or benefit from continued local thought.
