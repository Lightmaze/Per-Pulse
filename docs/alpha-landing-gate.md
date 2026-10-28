# First runnable alpha — landing gate

Per Pulse's default `main` now contains a navigation README that links to
Draft PR #31, but it does **not** contain the runnable Home/Work application.
The integrated product is still developed as a stack of review branches,
not a usable default checkout. This candidate deliberately targets
`main` so users can obtain one runnable product instead of learning a chain
of thirty pull requests. **It is a Draft review candidate, not an alpha
release, deployment, or request to publish a package.**

## What must land together

The source tree inherited from the green five-route integration candidate
contains one native Context Body with local activations and computational
connections; Home and distinct Work contexts; exact-source navigation and
return-to-draft; attributed and revisable experience; native action approval
and provenance-checked text artifacts; separate English/简体中文 UI and reply
language controls; conservative restart-stable background token **admission** and bounded primary
Kernel model rounds per local activation (neither is a provider billing cap). Earlier owner work is
preserved as Git history, not copied into parallel stores or runners.

The exact dependency is the #30 integration head, including #1/#2/#7 and
successive integration branches through #29. The independent feature-owner
PRs are not independently merged here. Keep their original credit and review
history. Review the complete base-to-head change, not only the last feature.

## Additional delivery gate

`tests/release/installed_wheel_smoke.py` is invoked by the existing CI
*after* building the distributable wheel. It starts a fresh CLI process
outside the source checkout, reads the compiled client and JS asset from the
installed distribution, verifies native Home/Work APIs, restarts the authored
example, and checks that the sample World cannot leak into ordinary
disconnected Home. Fake provider variables in example mode must never make
the product appear connected. This closes a different failure class than
editable-install unit tests, source-tree Chromium scripts, or a wheel build
that is never installed.

The smoke uses an ephemeral local SQLite World and **no external model
provider**. It does not prove a provider improves after correction, that a
multi-day Context Body functions unattended, or that hourly token
reservations are an exact money limit. Workspace actions remain opt-in.
The example has no model calls.

## Gate before leaving Draft

- Verify both existing CI jobs and the *new installed-wheel gate* complete
  successfully on **this precise new commit**, not the parent #30.
- Inspect failure logs and fix the product or smoke rather than weaken
  source, model isolation or restart assertions.
- Confirm README setup from a fresh clone *after* landing. Until then users
  should read the PR head or checkout the candidate branch explicitly.
- Keep the external-provider controlled ordinary-correction trial and
  per-request hard cost boundary separate, visible next milestones.

A green synthetic browser journey is mechanism evidence, not a general claim
that the deployed model acquired skills. The alpha is local, single-user,
loopback-only. No remote hosting, account security, PyPI upload or model
credential provisioning is part of this landing.

## Two distinct shutdown/restart outcomes

A cooperative native Kernel/Pulse shutdown should drain physical owners,
release the database lease, and allow immediate same-World reentry. If the
process exits without proving that all physical workers left, retaining the
active lease is intentional: a successor is rejected until the prior epoch
expires. Expiry permits a new fenced epoch, not replay of the old worker's
actions or retroactive proof that its output was delivered.

`tests/per_pulse/test_runtime_lease_fallback.py` exercises this distinction
against the real SQLite lease transactions with a controlled UTC clock. It
asserts live-owner conflict, stopped-but-unreleased conflict, expiry takeover,
stale-epoch rejection and immediate recovery after clean release. The
low-level test is not a multi-process forced-kill experiment and does not
supersede the installed Chromium Work restart journey or the outstanding
non-cooperative native owner acceptance in issue #32. Neither path supplies
real-provider outcome or spending evidence.

## Physical death of the lease owner

`tests/per_pulse/test_runtime_lease_sigkill.py` starts a distinct Python
process holding the real SQLite lease, confirms a simultaneous successor is
rejected, kills the owner process without any Python cleanup, and checks that
its durable lease remains active. It waits for the **observed** expiry,
requires a new fenced epoch, rejects stale-owner release/ownership and checks
that a subsequently clean close permits immediate entry. This complements
the controlled-clock fallback test and makes the operating-system failure
boundary observable in the standard Python CI run.

This is still a **lease-only** process-kill test: the child does not own an
in-flight Kernel turn or a live local Context Body activation. The end-to-end
forced termination and native abort-before-turn scenario in issue #32 remains
open; no lost action is claimed delivered or replayed. The existing Chromium
restart journey and real-provider tests remain distinct evidence.

## Physical death with an active Kernel turn

`tests/per_pulse/test_runtime_inflight_sigkill.py` starts a full native
Per Pulse HTTP/World/Kernel/Pulse service in a separate OS process. A real Work
reaches an intentionally **blocked synthetic model transport**, then the
operating system kills the server before that in-flight request completes.
After the observed durable lease expires, a new server opens the same World
and checks that the original user Work survives without a fabricated answer
or an automatic duplicate model request. A different Work must still complete
a new, explicitly submitted foreground task. The separate fixture request
journal distinguishes old replay from legitimate new input.

This checks physical mid-turn crash/reentry, unlike the earlier lease-only
process death test. It does **not** claim graceful shutdown, tool side-effect
exactly-once delivery, paid provider behavior, or uninterrupted process
activity; the pre-create race has its own regression. The browser journey is
still a separate acceptance check. Keep issue #32 open until its entire
lifecycle and effect/reentry acceptance is reviewed together.

## Physical death after a file effect, before its receipt

`test_runtime_effect_sigkill.py` and `tests/browser/crash_recovery_flow.mjs`
terminate a full native service after an approved atomic file replacement
but before its durable operation receipt. The test independently confirms
the file contents and native RUNNING action before the kill. After confirmed
process death and observed lease expiry, the same World recovers the action
as UNCERTAIN. Repeating the old approval only reads its existing receipt;
it cannot execute again. An explicit Stop retains uncertainty and the file,
and a new human instruction continues the **original Work** without another
write. No unconfirmed effect is promoted to a successful artifact.

The interface now exposes these retained native records, with redacted and
bounded details, under **Actions with unconfirmed outcomes** in their original
Work. Later successful work does not erase that evidence. The Chromium flow
also checks bilingual controls, drafts, Home isolation and mobile overflow.
This is one tested local file crash boundary, not a blanket exactly-once claim
for commands, external services or arbitrary operating-system failures.

## Cooperative pre-create close and delayed cancellation callers

The native World-level cases in `test_kernel_precreate_abort.py` complement
the earlier isolated Kernel regression. A turn claimed before its durable
row exists must cancel without a model call, drain the Pulse/Kernel owners,
release its lease and immediately reopen the same Work for explicitly new
input. A separate held abort callback proves that worker completion alone
cannot release shared storage or admit another owner: the callback must also
physically leave. The deferred finalizer then releases the original epoch.

`PulseEngine.wait_for_shutdown_drain` includes these cancellation callers as
well as executor workers. Its abort report describes Python callback threads;
backend process-tree evidence remains the independent Harness report. This
fixes the previous false unknown-process report without weakening a live
owner's lease fencing or the late-publication guard.
