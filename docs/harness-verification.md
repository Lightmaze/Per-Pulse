# Harness phase verification — 2026-10-23

Baseline: `bde8aa888ed4415b33e3f1f9414cd8ac99e7440b`. Scope: the local English Per Pulse product and its native Kernel/workspace integration. Three research/implementation agents covered Duet/dot and artifacts, Muse/Grok Bot and shared resources, and native harness controls/recovery. The parent integrated the API/UI, reviewed their changes, and ran the complete checks below.

## Results

| Check | Result | What it establishes |
| --- | --- | --- |
| `python -m pytest -q` | **52 passed in 24.28s** | All repository product tests, including 21 execution, 10 artifact, 10 concurrency, 3 new API and 8 existing page tests. |
| `npm --prefix web run build` | **Passed** | TypeScript project build and Vite production bundle, 41 modules. |
| `python -m build --no-isolation` | **Passed** | Source distribution and wheel built, including compiled web assets. |
| `node tests/browser/harness_smoke.mjs` | **10 checks passed** | Real Chromium UI at 1440×1000 and 390×844 against the actual local app/World/Kernel. |
| `git diff --check` | **Passed** | No whitespace errors in the proposed source changes. |

Browser checks: nonblank Home and identity; no file before approval; actual readable artifact after approval; no file after decline; Stop enabled during pending approval continuation; exact continuing-turn cancellation; Home still usable; a succeeded file effect plus failed continuation displayed truthfully with sensitive preview content hidden; mobile layout without horizontal overflow; no page errors or relevant console errors. Desktop/mobile screenshots were inspected. The initial cloud-browser localhost attempt could not reach the isolated server, so Chromium and the fixture server ran in the same process environment.

## Reproduce the browser flow

Build the frontend and install the project's Python development dependencies first. The browser check additionally requires Playwright and a Chromium executable or Playwright's installed Chromium. It creates a temporary synthetic workspace; it does not use an existing user workspace or remote model credentials.

```sh
PER_PULSE_TEST_PYTHON=.venv/bin/python \
PER_PULSE_PLAYWRIGHT_PATH=/absolute/path/to/playwright \
PER_PULSE_CHROMIUM_PATH=/absolute/path/to/chromium \
node tests/browser/harness_smoke.mjs
```

The optional `PER_PULSE_QA_OUTPUT` chooses the screenshot/JSON output directory; the default is a temporary directory. `PER_PULSE_QA_PORT` changes the local test port from 8146. If Playwright is available through ordinary Node module resolution, omit `PER_PULSE_PLAYWRIGHT_PATH`. If using Playwright's installed browser, omit `PER_PULSE_CHROMIUM_PATH`. Test-only fixture routes exist only in `tests/browser/fixture_server.py`, not in the production app.

## Failure cases exercised

- Cross-page action/artifact IDs, stale completed turn IDs, expired or detached approvals, repeated exact decisions, denied/cancelled effects and incomplete approval previews.
- Offline recovered approval and effect-start windows: Stop constructs no model/provider/reviewer/advisor, preserves uncertain effects, closes its temporary control runner, and allows later configured work.
- Successful physical write followed by `redaction_required` during continuation: the action remains succeeded and the response explains the separate continuation failure. No core redaction check was relaxed.
- Shared stale-preimage writers: exactly one succeeds. File reads continue while another writer holds the lane; queued writer/command cancellation starts no effect; restore shares the same lane.
- Command partial effects and unconfirmed cleanup: retain native recovery semantics, quarantine cooperating writes, keep reads/unrelated workspaces usable, retain the fence across provider replacement, and release only after confirmed cleanup. Docker subprocess/control I/O is synthetic; a real Docker daemon was not run.
- Artifact overwrite, restart, event retention, leaf/root symlink, root-directory replacement, hardlink, UTF-8, size and credential boundaries. Changed/unavailable versions expose no content.
- At this verification snapshot, large successful file receipts could exceed the native payload limit and lose source metadata. Such results were omitted with an incomplete-list indicator, not attributed from file existence alone. The later [bounded native file receipt fix](harness-foundation.md#bounded-native-file-receipts) preserves metadata for new results; it does not reconstruct these older truncated receipts.

The complete run initially caught a product regression: a blocked/recoverable provider failure overrode the existing page error state. The projection now preserves the causal error while exposing recovery controls separately. The complete suite passed after that fix. Earlier test-harness synchronization issues were corrected by waiting for native approval eligibility and causal delivery, without relaxing production authority or timing checks.

## Limits and next validation

Model choices use explicit synthetic HTTP transports, and command lifecycle tests use explicit local fixtures. Actual SQLite/World/Kernel state, approval controls and physical file operations are exercised. These tests do not certify real-model task performance, external account permissions, Docker isolation or long-duration reliability. The shortened phase did not run an endurance test.

The writer lane and unresolved-command quarantine coordinate participating writers in one process. They are not cross-process filesystem fences or a durable external-container inventory. Immutable artifact history, binary/download delivery, semantic mid-turn steering, durable spending reservations and native browser/connector admission remain recorded next-phase work in [the foundation plan](harness-foundation.md).
