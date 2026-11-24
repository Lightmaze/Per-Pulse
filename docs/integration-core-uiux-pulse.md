# Core UIUX + native Pulse integration (2026-10-25)

This stacked increment ports [Core UIUX PR #4](https://github.com/Lightmaze/Per-Pulse/pull/4) onto [native Pulse Activity PR #14](https://github.com/Lightmaze/Per-Pulse/pull/14), descending from #13/#12/#11/#10/#7/#2/#1. It uses the same World, Kernel, ContextPulseController and causal graph.

## Integrated behavior
- Source-message deep links and workspace-scoped browser history survive copied URLs and reloads. Returning to a source restores reading position, focus and drafts when available.
- Workspace and message statuses distinguish disconnected saved input, stale polling, active work, and uncertain/failed outcomes. Out-of-order refreshes cannot overwrite newer local messages.
- Search/rename/create dialogs receive keyboard/focus restoration and mobile interaction improvements; Bring to Home names the source and destination.
- The Activity panel and its global/page holds, causal source identity, request-order fences and connection controls remain in the same topbar, app and CSS. No second pulse coordinator or scheduling database is introduced.

## Conflict resolution
The original UIUX App/CSS hunks overlapped the Activity controls in #14. The combined App explicitly preserves Activity alongside restored UIUX source, focus and recovery behavior. CSS preserves original Activity styling and adds UIUX selectors. The original UIUX research files preserve their provenance; they are historical material, not proof that this combined checkout was browser-tested.

## Verification scope
The GitHub Actions workflow runs the UIUX state and Pulse request-order tests together, TypeScript/Vite, native Python suite and package build on this **single combined tree**. A green CI result is not a Chromium session, real model, or five-line product acceptance. Remaining separate work: Harness #3 persistent budget/approval; model attention #6; posture #8; locale #9; and end-to-end Home → Work → multi-source influence → correction → real tool effect → source-linked return.
