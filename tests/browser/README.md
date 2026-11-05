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

Run `node tests/browser/work_learning_flow.mjs` with the same environment to
check native Work connection learning, spectrum slots, source-only content
delivery, bilingual Activity labels, operator removal, restart and explicit
reconnection. Its histories come from accepted native turns over an explicitly
synthetic HTTP model transport.
`PER_PULSE_SCREENSHOTS` selects an optional output directory for desktop and
mobile screenshots. Test data and images should stay outside the repository.

Run `node tests/browser/work_discovery_flow.mjs` with the same environment to
check that a fast native `work.create` appears in the sidebar through Home's
existing workspace read, without manual refresh or another model call. It
preserves the draft and focus, holds a stale read across a newer Home turn,
checks failed-read recovery and fences a real service restart before explicit
refresh. An overlapping Home observation also preserves an explicit reconnect.
`PER_PULSE_WORK_DISCOVERY_SCREENSHOTS` records desktop and phone views.
Only the HTTP model choices are synthetic; native Work and persistence are real.

## Native report source navigation

Run `node tests/browser/report_source_flow.mjs` with `PLAYWRIGHT_MODULE` and
`PER_PULSE_PYTHON` as above. A synthetic HTTP model calls the real native Work
report tool; Home opens that report's exact settled Work result and returns to
the original report position and focus while preserving both drafts. The flow
also checks ordinary report-shaped human text, offline restart, and a phone
viewport. `PER_PULSE_REPORT_SOURCE_SCREENSHOTS` selects optional screenshots.

## Native approvals and file results

To check ordinary Home conversation organizing Work while file actions remain
disabled, run:

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  node tests/browser/work_organization_flow.mjs
```

The explicit synthetic HTTP transport selects real native `work` create,
list/send and report calls. The browser stays in Home, refreshes the workspace
through Settings, opens the same Work through the existing navigation, and
checks its reports against the actual persisted outbox and source turns.
Offline restart retains both pages, results and drafts on desktop and phone. Set
`PER_PULSE_WORK_ORGANIZATION_SCREENSHOTS` for screenshots. The existing CI
verify job runs this flow with workspace file actions disabled.

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

The saved-command flow uses the same dependency:

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  node tests/browser/command_output_flow.mjs
```

It approves commands through the actual World and Kernel, captures real OS pipe
bytes, follows both streams in the model and browser, verifies downloads,
rejects damaged responses, and cancels obsolete reads and pending saves. It also
checks a later turn, Home isolation, capture limits, an explicit storage failure,
and offline restart without a model or command executor. Docker control and
model choices are explicitly synthetic. Set `PER_PULSE_COMMAND_SCREENSHOTS` for
desktop and phone screenshots. The existing CI verify job runs this flow.

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

## Recall an original saved tool result

Run `node tests/browser/tool_history_flow.mjs` with the same Playwright and
Python environment. `PER_PULSE_TOOL_HISTORY_SCREENSHOTS` optionally records
desktop and phone views. The explicit synthetic HTTP fixture chooses tools;
the World, Kernel, file read, saved transcript and history reads are real.

The browser reads a file once, replaces its physical bytes, sends multiple
long turns and verifies that the original result has actually left the model
request. The private old marker appears only in the original tool result,
with no earlier discussion summary supplying it. `search_history(role="tool")`
and paginated `read_history(role="tool")` then recover the exact saved JSON,
including Unicode text and the original action, call, message and status.
The file stays changed, the original transcript row stays unchanged, and the
original file action still has exactly one execution. The flow repeats recall
after a process restart, rejects another Work's search and direct read, checks
that default discussion search excludes tool records, and opens the original
instruction on a phone while preserving the draft and source across reload.

## Local Notice continuity

Run `node tests/browser/notice_flow.mjs` with the same Playwright and Python
environment to check local Notice registration through the real chat interface.
Its explicit synthetic HTTP fixture records a Notice, reads a large source,
checks the very next model round and a later clipped context, reads the original
native registration, and rejects sibling Work access and a stale close revision.
The browser finds the original instruction, preserves its draft, closes the
Notice, and verifies pending then closed state across two process restarts.
`PER_PULSE_NOTICE_SCREENSHOTS` optionally records desktop and phone source views.

## Source material to continued work

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  PER_PULSE_MATERIAL_SCREENSHOTS=/tmp/per-pulse-material-screenshots \
  node tests/browser/materials_flow.mjs
```

This actual disconnected browser flow uploads authored text only after an
explicit action, previews it without executing HTML, and attaches its exact
version while preserving an existing draft and page link. It reuses the
same file in another Work, checks specific rejected-upload errors, delayed
reads after navigation, process restart, phone preview focus, English/Chinese
controls and a selected sourced return Home. It runs against the real local
World and filesystem with no provider and no invented assistant response.
`PER_PULSE_PYTHON` can also select an isolated wheel-installed interpreter.

## Read and save a confirmed document

Run `node tests/browser/documents_flow.mjs` with `PLAYWRIGHT_MODULE` and
`PER_PULSE_PYTHON` as above. Optional `PER_PULSE_DOCUMENT_SCREENSHOTS` records
desktop and phone views. It approves a real native Markdown write from an
explicit synthetic HTTP model, reads headings/tables/code, toggles exact source,
confirms inert HTML and no remote image requests, and saves actual browser
downloads with the original Unicode name and SHA-256. It repeats after restart
without a model call, checks keyboard scrolling and Chinese controls, then lets
another Work replace the file while the first preview stays open: saving the old
result must fail without exposing the later Work's content. This is a mechanism
and rendered-use check, not an assessment of a real model's writing quality.

It also carries the approved file into an **unsent Home draft** using the
existing native, exact-version workspace reference. The same Chromium flow
verifies source attribution, preservation of both drafts, Home reload and
browser Back, no extra model call, English/Chinese controls, and a stale-file
negative control after another Work changes the original bytes. This does not
create another World or automatically authorize a tool or provider request.

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

## Retain an example and inspect its later feedback

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  node tests/browser/example_flow.mjs
```

The concrete-example journey keeps an exact passage, explicitly shares it,
reports counterevidence, and exercises native example capture and refinement.
Later feedback includes an actual uploaded source: its readable projection
opens the original message, retains the draft and downloads the complete file.
Restart preserves these links. Phone layout and the Chinese interface retain
the original English text. `PER_PULSE_EXAMPLE_SCREENSHOTS` records images.
Semantic choices are an explicit synthetic HTTP fixture; persistence, source
verification and browser actions are real.

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

## Supply a file and take a sourced report away

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  PER_PULSE_MATERIAL_SCREENSHOTS=/tmp/per-pulse-material-screenshots \
  node tests/browser/material_flow.mjs
```

This flow selects a real BOM/CRLF/Unicode CSV in the browser, preserves its
original bytes, restores its draft, links another page, and submits its native
file reference. A deliberately scripted provider follows actual `reading`
passage receipts and computes the report from those returned CSV rows. The
browser approves the native file write and downloads the exact result. It also
downloads the original source with its upload name, rejects damaged response
bytes and cancels a pending source save when its dialog closes. It checks
raw message copying, explicit source-file return to Home, recovery of a
dropped successful upload response, unsupported files, changed-version refusal,
offline restart, file-only messages, and Chinese phone source download. Upload, reading,
approval and filesystem effects are real; semantic choices and arithmetic are
fixture code and do not establish real-model performance.

The same flow saves a concrete example of the report, opens its source
experience, and returns to the actual attached-file input with the draft intact.
The rendered episode hides only a server-verified file-reference envelope;
the stored encounter stays unchanged and does not travel with shared guidance.

`workspace_status_flow.mjs` holds a real native Work after an approved file
effect, navigates to idle Home and releases its explicit provider fixture.
The sidebar must reflect completion without leaving Home, losing the draft
or focus, or making another model request. A held Home response also checks
that finishing navigation does not interrupt a draft already being edited.
It checks ordinary foreground Work
observations; it does not activate background Pulse or schedule a new turn.

## Continue a page with /tick

```sh
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  node tests/browser/tick_flow.mjs
```

Types `/tick 3` into Home and checks that exactly three quiet steps follow from
the page's own history, that **Stop** ends a longer run before its next step,
and that your own message ends a run. Model replies are a synthetic fixture.
