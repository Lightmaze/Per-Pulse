# Per Pulse — product interaction research

Research started: 2026-10-23 15:25:43 UTC. Research-only phase ends no earlier than 15:55:43 UTC.

Product grounding received from parent: `Lightmaze/Per-Pulse` is distinct from P-Pulse. It represents one continuously developing intelligence. The current core loop is Home → embedded session page → independent conversation → return / Bring to Home. This is not an agent-team control room.

## Evidence and access

Mobbin screen and flow searches both returned a paid-plan requirement; no Mobbin screenshot was inspected. No design claim below is attributed to Mobbin.

The source manifest `reference-images/sources.json` records 24 inspected official screenshots and design diagrams. Arc's older official help pages/media were unavailable (404/410); Arc is a textual and historical interaction reference here, not an inspected current visual reference.

Official Linear product screenshots were downloaded and actually inspected:

- `reference-images/linear-inbox.png` — [official Inbox docs](https://linear.app/docs/inbox).
- `reference-images/linear-agent-tabs.png` — [official Linear Agent docs](https://linear.app/docs/linear-agent).
- `reference-images/linear-agent-history.png` — same source.

## First observations

### Linear Inbox: relevance before volume

Observed screenshot: a Priority/Other selector sits above a plain vertical list. Each row has one dominant object title, a quieter sentence explaining the event, a source avatar, and compact time/status cues. The selected category is stronger than decorative framing. The docs describe navigation within the list and issue context, snooze, unread, and contextual actions.

Transfer to Per Pulse: the Home item should first explain what changed and why it matters, then expose the independent session. A count of pulses, tokens, or tasks cannot substitute for the meaningful change. Use a readable title plus a short human sentence, with provenance on the second level.

Do not transfer: team assignments, priority sorting, or an issue-tracker taxonomy as the product's central meaning.

### Linear Agent: open threads remain spatially available

Observed screenshot: one focused conversation sits above a strip of named open chats, with a separate history entry. Its composer stays at the conversation's bottom; its title and close/expand controls anchor the top. A history screenshot groups named chats by recency and marks unread items with a small colored dot. Docs describe per-thread working/unread indicators.

Transfer to Per Pulse: users must be able to enter a session, return to Home, and later return to exactly that session. Keep title, draft, content, and origin stable. Separate closing the visible detail from deleting the session. A focused session can have its own composer without appearing to replace the continuous Home context.

Do not transfer: model/provider controls or a large strip of chat tabs by default. They would compete with the continuous subject and become generic chat UI.

### Raycast Action Panel: one obvious action, optional depth

Official source: https://manual.raycast.com/action-panel and https://manual.raycast.com/keyboard-shortcuts.

Documented mechanism: Enter invokes the selected item's primary action; the action panel exposes other context-specific actions and their shortcuts; Escape returns from a submenu or dismisses the panel. Related actions are grouped, while search flattens matching results.

Transfer to Per Pulse: each session card has one obvious entry action. Inside a session, “Bring to Home” is a deliberate content action, separate from ordinary Back. Put rename/archive/copy-link operations in a small contextual menu. Show shortcut labels where they teach a useful action; avoid a permanently visible keyboard manual.

Do not transfer: turning every Home interaction into a command palette. The product needs visible continuity and a readable home.

### Notion peek: keep the place while inspecting an object

Official source: https://www.notion.com/help/views-filters-and-sorts.

Documented mechanism: the same database pages can open in side peek, centered peek, or full-page mode. Views define their own layout, visible properties, filters, and grouping.

Transfer to Per Pulse: a session is a real, addressable object; viewing it must preserve where it was opened from. Desktop may preserve Home context beside a focused session, while narrow screens use a full page with explicit return. The close/back action should restore scroll/focus and never silently create another session.

Do not transfer: a large configuration surface for choosing every layout. The 90-minute increment should establish a dependable core interaction before adding preferences.

## Initial hypotheses to validate against the actual product

1. Home should communicate continuity through retained meaningful objects and ongoing context, rather than dashboards of implementation counters.
2. A session entry needs title, one meaningful preview, recency, and at most one status indicator.
3. “Back Home” and “Bring to Home” are different actions: navigation versus adding selected context/result to Home.
4. While the intelligence is working elsewhere, the interface should keep the user oriented with a restrained in-context status and a useful next action.
5. Empty states should offer one plausible starting move. There should be no setup form wall before users can talk.
6. The full core loop must be reachable by keyboard, with Escape returning, focus restored, and no draft loss.

These are design hypotheses, not an audit of the existing implementation.

## Second pass: scope, depth, and interruption

### Granola: the place determines the question's scope

Official source: https://docs.granola.ai/help-center/getting-more-from-your-notes/chatting-with-your-meetings.

The documentation distinguishes Home chat over all notes from a single meeting, a folder, or a selection of meetings. This is a useful precedent for a consistent composer whose context follows the user's location. Its screenshots were inaccessible (403), so this observation is textual research.

Per Pulse decision: use the same visual composer family on Home and inside a session. Make the scope legible through the page title and a short label, rather than a required setup form. Entering a session should feel like focusing the same intelligence on a local thread.

### Craft: embedded objects are entrances, not decorative cards

Official source: https://support.craft.do/en/introduction/documents.

Craft treats a block as both content and, when it contains nested material, an entry to a deeper page. Page/card presentation makes that navigability explicit. Moving blocks within a space preserves their links.

Per Pulse decision: the session embedded in Home should look and behave like an addressable page. Its title is the main link, with a useful excerpt; a small open affordance confirms that the item has depth. The surface should not suggest the whole session is a disposable toast.

### Linear agent states: say what the user can do next

Official sources: https://linear.app/developers/agent-interaction and https://linear.app/developers/agent-best-practices.

Linear exposes separate session states for starting, active work, waiting for input, error, completion, and staleness. Temporary activities can be replaced by the next activity, avoiding an ever-growing list of transient messages. The inspected entry screenshot uses one low-key “Working” line and an Open action.

Per Pulse decision: a busy indicator alone is insufficient when work needs a response or failed. Status should distinguish these conditions and give the matching recovery action. Do not represent every global interval without new foreground output as an error or a dead process: Per Pulse's broader continuous state is not the same thing as one session run.

### Raycast: intermediate depth must preserve the conversation

Official source: https://manual.raycast.com/ai/ai-chat.

Three inspected screenshots show a persistent title/composer, secondary settings in a separate panel, and long user messages collapsed with an explicit “Show more” control. The documented Quick AI→AI Chat transition carries history and attachments. Background completion can return users to the relevant conversation.

Per Pulse decision: disclosure may reduce the footprint of long history, but it must preserve text and reopening. Work-in-progress can remain available while the user returns Home. A generic completed toast is weaker than a link back to the exact ongoing context.

## Design review from the makers

- [Linear's 2026 refresh](https://linear.app/now/behind-the-latest-design-refresh): a consistent header system, reduced auxiliary visual weight, fewer excessive icons, and softer structural separators. **Our implication:** let content lead without making the current location or return action illegible. Warm neutral color is compatible with precision; it does not require a cold dashboard.
- [Linear's 2024 redesign](https://linear.app/now/how-we-redesigned-the-linear-ui): designers stress-tested whole views across platforms, appearance, and hierarchy before broad rollout. **Our implication:** test the Home→session→Home loop as a unit at desktop and phone widths; a beautiful isolated card cannot establish the core experience.
- [Raycast's action-bar redesign](https://www.raycast.com/blog/a-fresh-look-and-feel): a powerful feature had been too hidden behind a small button, so actions and feedback moved into a more discoverable common bar. **Our implication:** reducing chrome must not hide the only route into a session or obscure “Bring to Home.”

## Interaction contract for implementation review

| Situation | Expected user-visible behavior | Failure to catch |
|---|---|---|
| Open embedded session | The exact titled conversation opens, with origin and a dependable Back Home action | Opening a new blank conversation or losing context |
| Return without sending | Home restores its previous reading location; draft remains attached to its session | Losing text, jumping to top, treating Back as cancel |
| Continue a session | The existing conversation receives the message; composer state matches actual send state | Duplicate thread, double send, optimistic fake reply |
| Work continues in background | A restrained status tells users where meaningful activity is happening | A permanently animated decoration masquerading as progress |
| Session needs input | The required decision is inside the session, clearly actionable | A generic “Working…” spinner that never explains the block |
| Bring to Home | The exact selected content or session reference becomes visible in Home and can reopen its source | Silent transfer, unclear payload, repeated duplicate copies |
| Long history | Primary reading flow remains readable; expanded text can be collapsed again | Irretrievable truncation or composer covering the last message |
| Error while sending | Preserve input, explain failure, expose a useful retry | Clearing the draft or pretending the message succeeded |
| Mobile session | Full-width content, visible return, composer stays reachable above the keyboard | A desktop split pane squeezed to unreadable width |
| Keyboard | Logical tab order; labelled icon buttons; Enter and Escape do what the visible state promises | Focus behind a panel, no focus restoration, accidental sending during IME |

Implementation should honor existing API capabilities. These rows are review criteria, not authorization to invent backend behavior.


## Failure examples that sharpen the contract

These are individual historical public reports, not claims about the current state or prevalence of defects in those products.

- [Notion: opening a side peek jumps the underlying database to the top](https://www.reddit.com/r/Notion/comments/1jfvlu3/notion_sneek_peek_jumps_to_top_of_database/). The useful lesson is that a pane cannot promise retained context if the reader loses their position. Per Pulse acceptance: opening and returning to a session retains the previous Home reading location.
- [Arc: a new user cannot predict when a pinned link will open in Peek](https://www.reddit.com/r/ArcBrowser/comments/1kffmol/how_do_i_open_a_link_without_peek/). Per Pulse acceptance: the same page reference opens the same kind of session view; browser Back and the visible Back action are comprehensible and consistent.
- [Notion: familiar workspace/shared sections disappear after a sidebar change](https://www.reddit.com/r/Notion/comments/1sz7r5d/new_sidebar_workspaces_and_shared_pages_gone/). Per Pulse acceptance: quieter chrome still exposes Home, current page, and the route to an existing conversation. Hidden customization is not a substitute for a usable default.

## Baseline screenshot observations, 2026-10-23

Inspected `qa/baseline-home.png` (1440×1000), `qa/baseline-work.png` (1440×1000), and `qa/baseline-mobile.png` (390×844). These observations are limited to the visible frames; interaction behavior has not yet been tested.

- The warm white surface, restrained green selection, and serif page titles already support a calm personal workspace. Preserve this identity.
- Home and session composer placeholders both say “What would you like to work on?” The current page title communicates context at the top, but the far-away composer does not reinforce which conversation receives the next message.
- The mobile screenshot exposes message actions in very faint, small text. They are discoverable in principle but visually close to disabled controls. Core actions need a readable default state, especially without hover.
- The session already shows its Home origin. This is a strong foundation: strengthen the return behavior and the content transfer preview instead of adding a new hierarchy.
- “Saved · awaiting AI connection” can suggest automatic future processing. If there is no real retry queue, use wording that confirms the save and states the connection limitation without promising a future response.

The subsequently inspected `qa/baseline-return-dialog.png` already has an editable preview, Cancel, and Bring to Home. These are existing strengths to retain, not missing features to claim as additions. The parent’s baseline interaction test found initial focus on Close rather than the editable field, including the search dialog; this is a concrete keyboard defect to fix. A visible source/destination preview can clarify the existing return dialog further.

## Component design specification before implementation

### Page-link picker

A searchable page chooser follows the [WAI-ARIA combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/). Keep text focus in the search input; expose the active option through `aria-activedescendant`, the list through `aria-controls`, and the open state through `aria-expanded`. Arrow keys move the active option; Enter selects; Escape closes without changing the message. Option selection must remain distinct from sending the message.

Show useful pages before typing, in an existing stable order, instead of demanding an exact title from memory. A short count provides feedback after filtering. Distinguish no pages from no matches. Results are page titles with optional relevant context, not a second dashboard. Selection inserts the existing page reference and returns focus to the message composer. A selected option scrolled below the results viewport must be brought into view; browsers do not do this automatically for `aria-activedescendant` ([official example](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/examples/combobox-autocomplete-list/)).

### Create page

Name the action and resulting place clearly. The title field is the first editing step. If creating from a message, retain a source excerpt so the user knows which material is carried forward. The primary action describes the result, such as “Create page.” Do not introduce agent/persona/provider settings into this loop.

### Bring to Home

Show the source page and destination before committing. Provide an editable text preview of what will be brought back; a source link accompanies the result. Cancel and Escape leave the conversation unchanged. The action means adding meaningful material to Home, while Back Home only navigates. Do not use a generic confirmation without showing the payload.

### Dialog and keyboard behavior

The [WAI-ARIA modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) requires an accessible name, focus inside the open dialog, contained Tab traversal, a visible close control, Escape dismissal, and a logical focus return. Keep the existing primitive when practical; avoid rewriting an already-correct native dialog solely for style.

The [MDN `isComposing` reference](https://developer.mozilla.org/en-US/docs/Web/API/KeyboardEvent/isComposing) and [Google Chrome IME guidance](https://github.com/GoogleChrome/modern-web-guidance/blob/main/skills/modern-web-guidance/guides/forms/ime-safe-enter-submit.md) establish that Enter can confirm Chinese/Japanese/Korean composition. Custom Enter handling in the composer and chooser must not submit during composition. Preserve Shift+Enter newline behavior and a visible send button. Native form semantics should do the ordinary work where possible.


### Last-mile selection and references

[Linear Search](https://linear.app/docs/search) and [Raycast Search Bar](https://manual.raycast.com/search-bar) both make empty search useful through existing recent/favorite objects. In the inspected Linear search screenshot, the empty field has a short list of recent items underneath; the separate filter screenshot makes the current option visible with a whole-row background. Per Pulse should use an initial page list, then narrow it as the user types. It should not need a ranking engine or new favorites model for a small existing workspace.

[Notion links and backlinks](https://www.notion.com/help/create-links-and-backlinks) distinguish inline references from hierarchy-changing page links. The inspected backlink screenshot gives the originating page a readable named link near the title. Per Pulse should keep page identity stable: inserting a reference into a message must not unexpectedly reparent or duplicate the conversation. Bring to Home can carry a source reference without making a second independent session.


## Decision ledger for this increment

| Reference | Mechanism to carry forward | What stays out of scope |
|---|---|---|
| Linear Inbox / Agent | Named persistent sessions, meaningful event text, current truthful state | Issue priorities, teams, assignment, a monitoring dashboard |
| Linear design refresh | Stable location hierarchy, quieter surfaces, legible current selection | Copying dark appearance or making actions tiny |
| Raycast | Visible primary action, discoverable contextual actions, keyboard efficiency | Making the whole experience require commands or model presets |
| Notion / Craft | Addressable page references, readable source links, preserved parent context | A configurable database/editor product or changing hierarchy when linking |
| Arc | A quick excursion returns to the same place | Context-dependent surprise opening modes and disposable session semantics |
| Granola | A consistent composer whose scope follows location | A new workflow setup form before every question |
| WAI-ARIA / native dialog | Focus starts at useful input, active selection is exposed, cancellation preserves input | Hand-built modal mechanics when native behavior suffices |

Priority order for the available implementation hour:

1. Fix the demonstrated keyboard failure: initial dialog focus, picker navigation, IME-safe Enter, and dependable return focus.
2. Make context and consequences legible: composer scope, source→Home transfer preview, exact source link, honest disconnected copy.
3. Make the loop comfortable on phone widths: readable actions, reachable composer and close controls, no accidental message send while choosing a link.
4. Validate persistence and navigation with real state. Visual motion is secondary and must not cover a missing state transition.

Search-result counts and save feedback should be announced without stealing focus. [W3C status-message guidance](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html) explicitly distinguishes a brief result count from the results themselves and cautions against excessive live announcements. Use a small polite status region for the count; do not announce the entire conversation or every keystroke as an alert. [ARIA22](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22) supports a status role with atomic text so the count retains its meaning.


## Final mapping to the existing components

Read only the three assigned components and shared types at approximately 15:52 UTC, after the product and reference research. No implementation was changed during the research-only phase.

Existing strengths: `PagePickerDialog` already lists all pages for an empty query; `ReturnDialog` already edits and submits the selected text; `Composer` already protects composition and handles Shift+Enter. Preserve them. The increment adds keyboard active-option behavior/counts, explicitly useful focus after native `showModal()`, local composer scope, truthful provider-disconnected copy, and source/destination text in the existing return dialog.

The [MDN native dialog guidance](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/dialog) explains why initial focus must be deliberate. The current JSX has `autoFocus` inputs, but the parent’s baseline proves that this did not establish the intended focus after the dialog opened. Verify actual focused elements, not just the presence of an attribute.

The user shortened the remaining task at 15:51 to a stage wrap by 16:06. The research cutoff remains 15:55:43. Scope is now small, compatible component fixes, followed by a build and the parent's integrated browser validation.
