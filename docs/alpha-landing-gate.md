# First runnable alpha — landing gate

Per Pulse's default `main` still consists of a title-only README. The
integrated Home/Work application is currently developed as a stack of review
branches, not a usable default checkout. This candidate deliberately targets
`main` so users can obtain one runnable product instead of learning a chain
of thirty pull requests. **It is a Draft review candidate, not an alpha
release, deployment, or request to publish a package.**

## What must land together

The source tree inherited from the green five-route integration candidate
contains one native Context Body with local activations and computational
connections; Home and distinct Work contexts; exact-source navigation and
return-to-draft; attributed and revisable experience; native action approval
and provenance-checked text artifacts; separate English/简体中文 UI and reply
language controls; conservative restart-stable background token **admission**
(not a dollar or per-request provider hard cap). Earlier owner work is
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
