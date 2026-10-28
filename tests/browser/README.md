# Browser flow check

This checks the built React interface against the actual local World and
Harness Kernel. The HTTP model transport is an explicit synthetic fixture;
every generated reply and stored experience is marked accordingly. It neither
contacts an external provider nor establishes learned model capability.

Build the client and install the Python development dependencies using the
main README. Install Playwright in a separate tooling directory, then run:

```sh
npm install --prefix /tmp/per-pulse-browser-tools playwright@1.62.1
node /tmp/per-pulse-browser-tools/node_modules/playwright/cli.js install chromium
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  node tests/browser/posture_flow.mjs
```

The script starts its own loopback server on an unused port and uses a fresh
temporary data directory, which is removed afterward. `PER_PULSE_PYTHON` can
select another Python environment; it defaults to `.venv/bin/python`.
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select an installed Chromium binary.
`PLAYWRIGHT_CHROMIUM_ARGS` accepts an optional JSON array of launch arguments.
`PER_PULSE_SCREENSHOTS` selects an optional output directory for desktop and
mobile screenshots. Test data and images should stay outside the repository.

## Native approvals and file results

```sh
PER_PULSE_PLAYWRIGHT_PATH=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_TEST_PYTHON=python \
  node tests/browser/harness_smoke.mjs
```

The Harness smoke flow also approves a small patch whose resulting diff exceeds
the native result limit. It opens the confirmed long report, checks successful
model continuation, exact action identity and draft retention after reload,
and previews the same file on a phone viewport. The model is explicitly
synthetic; approval, file replacement, receipt and digest verification are real.

A separate native attention-to-source flow uses the same Chromium dependency:
```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  node tests/browser/attention_flow.mjs
```
It runs a single durable World and Kernel against explicitly synthetic model
responses. It confirms that a successful original model action creates a
retained thought only in its Home context, that Work cannot see it, and that
the bilingual Activity panel can return to the verified original message
without losing a Work draft. It then restarts the service and tests a distinct
native model release. Its source link and persistence checks do not measure
real-provider judgment quality or token spending.

The flow sends an ordinary task followed by a correction, checks the resulting
lesson after refreshing a panel that was open during background learning,
checks its exact source, pauses it and restarts the service, resumes it, shares it with
a new page, verifies that the original encounter stays at the source, returns
to that source, resolves a revision conflict between two browser tabs, and
records harmful feedback. It also checks the mobile dialog, keyboard dismissal,
and browser runtime errors. After adding 505 queued fixture messages with the
provider disconnected, it opens the earlier lesson source and reloads that
location, checking that the original message and recent window remain distinct.
It adds a page link without losing that source, then deliberately delivers a
stale background-read response before a new navigation response in both
directions. The current source or recent view must win the race.

## Browse an older conversation

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  PER_PULSE_HISTORY_SCREENSHOTS=/tmp/per-pulse-history-screenshots \
  node tests/browser/history_flow.mjs
```

This offline flow seeds an explicitly authored long conversation in the native
World, with separate Home and sibling Work histories. It browses every older
message across stable ranges while new input arrives, retries a failed read,
opens an old message's original source, follows its reference and returns,
and verifies the draft after reload. It also checks phone layout, keyboard
focus, English/Chinese controls and a delayed response after Browser Back.
No model is configured; no assistant response or learning outcome is invented.

Run `node tests/browser/message_search_flow.mjs` with the same environment to
verify **Find a message** against this long native fixture. It covers failed-read
retry, old-source focus and reload, private-context isolation, stable ranges
while new input arrives, an obsolete query response, phone layout, bilingual
controls, Escape focus restoration, and Browser Back to another source within
the same Work while a search is pending. Queries remain local and read-only.

## Source material to continued work

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  PER_PULSE_MATERIAL_SCREENSHOTS=/tmp/per-pulse-material-screenshots \
  node tests/browser/materials_flow.mjs
```

This actual disconnected browser flow uploads authored text only after an
explicit action, previews it without executing HTML, and adds its exact path
and digest while preserving an existing draft and page link. It reuses the
same file in another Work, checks specific rejected-upload errors, delayed
reads after navigation, process restart, phone preview focus, English/Chinese
controls and a selected sourced return Home. It runs against the real local
World and filesystem with no provider and no invented assistant response.
`PER_PULSE_PYTHON` can also select an isolated wheel-installed interpreter.

## One rendered correction-to-artifact journey

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  node tests/browser/learning_artifact_flow.mjs
```

This additional integrated browser flow uses its own temporary World/Kernel/SQLite
and an **explicit synthetic HTTP model fixture**, not a paid model. It visits
the actual English-first UI: original Work result and ordinary user correction,
native attributable experience, negative control in an unshared Work (denied
intake file), explicit sharing, changed proposal in a later Work, native
approval and real local file, temporary Chinese UI/Pulse activity inspection,
selection of the completed message for a sourced Home return, exact source
navigation, preserved Work draft and restart. The fixture checks that only
the abstract lesson—not the raw source correction or original result—reaches
the later Work. This is mechanism and user-interface acceptance, **not**
evidence that a real model independently learned a new default behavior.
It does not establish a hard spending cap or long-duration autonomous Pulse.
`PER_PULSE_JOURNEY_SCREENSHOTS` optionally records an approval/artifact screenshot.

## Recover the original Work after a process dies during a file write

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  node tests/browser/crash_recovery_flow.mjs
```

This flow approves a native file mutation in Chromium, then kills the whole
server after its atomic file replacement and before the durable effect receipt.
It confirms physical exit and waits for the observed native lease expiry
(normally up to 30 seconds), without changing or removing the lease. The
successor opens the same World and Work. The browser inspects the original
unconfirmed action, stops the old turn, preserves the draft across language
and Home navigation, and submits a new instruction in that same Work. It then
commits a native generation succession and verifies that the original Work
still exposes the action and keeps its new draft. The original file is not
rewritten; no successful artifact is invented. Desktop
and mobile checks retain screenshots when `PER_PULSE_CRASH_SCREENSHOTS` is set.

The model and post-write barrier are explicitly synthetic. Actual native
approval, file mutation, Kernel recovery, World persistence and OS process
termination are exercised. This is delayed crash recovery, not proof of
graceful shutdown or a general exactly-once guarantee for external tools.
