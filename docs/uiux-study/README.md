# Core UI/UX stage handoff

This is a focused follow-up to `feat/english-session-pages`, not a complete redesign. Three research tracks studied product workflows, design systems, and the actual experience from 15:25:43 to at least 15:55:43 UTC on 23 October 2026. Implementation began only after that 30-minute window. The requested 90-minute session was subsequently shortened to a stage handoff by 16:06 UTC.

## Delivered

- Useful initial focus in dialogs; keyboard search and linking with Arrow keys, Enter, Escape, and IME protection.
- Explicit composer scope, saved/disconnected feedback, and editable source-to-Home return preview.
- Source URLs preserve message anchors across a new tab or reload. Navigation restores the originating link and reading position; per-page drafts remain intact.
- Distinct saved, queued, working, attention, interrupted, and stale states. Failed polling exposes a persistent retry action; refresh keeps the saved conversation.
- More readable supporting text, visible message actions, keyboard focus, and mobile touch targets. Stale or disconnected activity does not show a working sidebar dot.
- Read-request sequencing and mutation versions prevent stale page responses from overwriting newer results.

## Validation

- Production TypeScript/Vite build passed.
- Eight frontend state/navigation tests passed (`npm --prefix web test`).
- Eight existing native page-adapter tests passed (`pytest tests/per_pulse/test_pages.py -q`).
- Thirteen integrated Chromium checks passed: keyboard entry, source anchors, draft/focus restoration, keyboard picker/link insertion, edited result persistence, fresh-tab source targeting, attention state, failed polling/recovery, mobile action size, 320px overflow, and runtime errors. Separate component checks covered IME, no-match results, and dialog focus.
- Desktop and 390px/320px screenshots were visually inspected. No browser runtime errors were observed.

Browser checks used the real local example backend and explicitly injected attention/failure fixtures. They do not establish behavior with external AI-provider credentials. The connected browser surface could not reach the local service, so same-process local Chromium was used for these checks.

## Deferred

Markdown rendering, a page-context/backlink surface, jump-to-latest behavior, older-message pagination, background page-index freshness, and a broader visual redesign remain future work. The research synthesis includes these proposals; they are not claims of implemented functionality. An unavailable older source is reported honestly rather than silently treated as found.

## Study

- [Research synthesis](synthesis.md)
- [Product workflow references](product-research.md)
- [Design-system references](design-system.md)
- [Experience audit and verification](experience-audit.md)
- [Component acceptance notes](component-acceptance.md)

Public official documentation and product imagery informed the study. Mobbin was inaccessible behind a paid-plan requirement; no Mobbin screens are claimed as reviewed. Research reference images and local test data are intentionally excluded from this source change.
