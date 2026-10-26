# Per Pulse experience research and audit

Research window: 2026-10-23 15:25:43–15:55:43 UTC. Product implementation is intentionally untouched during this window.

## Product grounding

The checked-out project is `Lightmaze/Per-Pulse`, continuing the existing Pulse implementation. Per Pulse has one continuing subject. Home and Work pages are contexts of that subject; opening a page must not imply creating another agent. The primary path in `AGENTS.md` is Home conversation → inline session reference → work in that page → return to the original position → bring a selected result to Home with provenance.

Research is feeding that existing path. Reference products are not a replacement product model.

## References read and observed

| Reference | Evidence examined | Mechanism relevant here |
| --- | --- | --- |
| [Linear Agent Interaction Guidelines](https://linear.app/developers/aig) | Official text and the expanded agent-session screenshot in the browser | Work state is a compact, current row; actions use strong action labels and quieter parameters; details disclose inline rather than occupying the primary screen. |
| [Linear agent interaction lifecycle](https://linear.app/developers/agent-interaction) | Official lifecycle specification | Pending, active, waiting for input, complete, error, and stale mean different things. Lack of updates must not visually continue to assert that work is happening. |
| [Linear interaction best practices](https://linear.app/developers/agent-best-practices) | Official behavior specification | Acknowledge receipt quickly, distinguish durable activity from editable comments, and make the completion/need-for-input state explicit. |
| [Claude Projects introduction](https://www.anthropic.com/news/projects) | Official 2024 announcement, embedded artifact/workspace image actually viewed | The conversation and the enduring result have different visual roles. An artifact gets its own title and usable surface while the conversation continues alongside it. This is a historical reference, not a claim about the current Claude UI. |
| [Notion custom agents](https://www.notion.com/help/custom-agents) | Official documentation | Activity can remain inspectable and reversible without making the everyday page a control panel. Per Pulse should reuse only this inspectability principle, not the many-agents model. |
| [Notion sidebar](https://www.notion.com/help/navigate-with-the-sidebar) | Official navigation documentation | Stable anchors coexist with contextual recents and search. A user's return path must remain predictable as content grows. |
| [Radix accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility) | Official accessibility and focus behavior documentation | Behavior, labeling, and focus are part of a component, not finishing touches. Use native semantics where they already provide the behavior. |
| [Radix Dialog](https://www.radix-ui.com/primitives/docs/components/dialog) | Official anatomy and keyboard contract | Modal opening, Escape, focus containment, and focus return form one complete interaction. |
| [WAI dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) | Official interaction guidance | Restore the invoker when canceling, or move to the logical next point when the dialog creates a new context. |
| [WAI status message technique](https://www.w3.org/WAI/WCAG22/Techniques/aria/ARIA22) | Official status and announcement guidance | Announce a compact atomic status without moving focus; avoid treating the entire updating transcript as a live region. |

Mobbin search was attempted but its connector returned a paid-plan requirement. No Mobbin screenshots were inspected and no findings are attributed to Mobbin.

## Implementation evidence read

Read `AGENTS.md`, `README.md`, `web/src/App.tsx`, `useWorkspace.ts`, `storage.ts`, `api.ts`, `types.ts`, `Conversation.tsx`, `Composer.tsx`, `Sidebar.tsx`, `SessionLink.tsx`, `Dialog.tsx`, `WorkspaceDialogs.tsx`, `src/per_pulse/service.py`, and the first integration checks in `tests/per_pulse/test_pages.py`.

The implementation already has durable backend messages/links, page-scoped local drafts, scroll restoration, normal anchor semantics, native modal dialogs, IME-aware sending, and an explicitly disconnected example workspace. These are useful foundations to preserve.

## Current code findings, awaiting browser confirmation

1. **Some real backend states have no clear UI.** `PageService._status` returns `needs_attention` for `blocked`/`awaiting_approval`. The message status filter omits these, and the sidebar displays only `working`. A page can need input while appearing quiet.
2. **Polling failure leaves a misleading active state.** `useWorkspace` silently catches a failed poll. The last `working` state can remain visible indefinitely. Retain the transcript but show that connection freshness is unknown and offer a real refresh action.
3. **Connection refresh and active-page provider state diverge.** Settings refreshes bootstrap only. The polling decision uses `activeView.provider.configured`, so the current page can retain an outdated provider flag after the server has restarted with a provider.
4. **Source-message URLs are incomplete.** `SessionLink` sends `targetMessageId` only through SPA state. Its real `href` contains only the page path. Opening the source in a new tab, copying its URL, or bookmarking it loses the source message location.
5. **Keyboard focus does not follow page navigation.** Updating the React route restores scroll but leaves focus wherever the prior view left it. A newly opened page should have an intentional focus destination, and returning should make the source context apparent without discarding the draft.
6. **Backlinks exist in the API but are not displayed.** The README describes following backlinks, while the current conversation only shows outgoing references and a single creation source. A compact source/connected-context surface could make reuse visible.
7. **The sidebar can retain stale background page activity.** Only the active page is polled. After navigating away, its latest cached status can remain `working` even after work finishes. An activity indicator requires refresh on focus/online and bounded polling while work is known to be active.

## State language contract for the redesigned surface

| Evidence | Primary status | User action |
| --- | --- | --- |
| Message not yet saved | Saving message… | Keep composer intact; prevent duplicate submit. |
| Saved message, provider disconnected | Saved · AI disconnected | Open connection details; continue organizing pages. |
| Configured provider, queued turn | Queued | Continue reading or leave; preserve draft. |
| Backend reports active work | Working | Inspect current conversation; do not invent percent progress. |
| Backend reports needs attention | Needs attention | Show the current message and available context; do not invent an approval endpoint. |
| Backend reports a failed/uncertain turn | Turn interrupted | Keep saved message visible; provide connection check and a follow-up path. |
| Poll/network failed | Connection lost · showing saved work | Retry synchronization; never erase the loaded transcript. |
| No active turn | Ready to continue | Resume this context; do not call all goals complete. |

## Acceptance matrix to apply after implementation

| Flow | Evidence to collect |
| --- | --- |
| Home → inline page → back | Same original Home message/scroll neighborhood and preserved unsent draft. |
| Work → selected result → Home | Only selected content returns, with source page and message link; source transcript stays intact. |
| Source link opened through its URL | The destination message is targeted after direct load/reload, not only after SPA click. |
| Failed request | Input remains available; transcript stays; visible status distinguishes a failed connection from active work. |
| Provider reconnect | Connection refresh updates the active view and permits queued work status refresh. |
| Search/dialog keyboard | Shortcut opens; input receives focus; Escape closes; focus returns; no focus escapes modal. |
| Mobile navigation | Menu opens/closes; selected page is identifiable; all primary controls fit and have touch targets. |
| Status announcement | Small status region has meaningful atomic text; no entire-transcript live announcements. |
| Example mode | Clearly labeled sample workspace; no fabricated model output or active-work animation. |


## Additional reference study

| Reference | Evidence examined | Application to Per Pulse |
| --- | --- | --- |
| [Notion links and backlinks](https://www.notion.com/help/create-links-and-backlinks) and [guide](https://www.notion.com/help/guides/creating-links-and-backlinks) | Official block-anchor and backlink behavior | Source links need stable message anchors, and returning should land near the original text. Backlinks can stay compact around a page's title. |
| [Linear comments](https://linear.app/docs/comment-on-issues) | Official comment, draft, and thread behavior | Preserve unfinished contributions independently of navigation and allow a direct URL to specific context. |
| [Fluent Wait UX](https://fluent2.microsoft.design/wait-ux) | Official waiting and AI guidance | Use one relevant current indicator. Show measured progress only when the system can measure it. Preserve context during waits. |
| [Carbon notifications](https://www.carbondesignsystem.com/building-blocks/core/patterns/notifications) | Official notification placement guidance | Place task feedback near its object; keep connection feedback restrained and actionable. Errors must not disappear before recovery. |
| [Carbon for AI](https://www.carbondesignsystem.com/building-blocks/foundations/carbon-for-ai) | Official AI interaction foundation | Offer explanations on demand; decoration must not stand in for evidence of work. |
| [Spectrum status light](https://opensource.adobe.com/spectrum-web-components/components/status-light/) | Official status-light semantics | Text must accompany the colored signal. Distinguish neutral, working, attention, and error. |
| [VA.gov focus management](https://design.va.gov/accessibility/focus-management) | Official design-system focus guidance | A new SPA context receives deliberate heading focus; polling does not move focus. Returning restores the original interactive reference without changing saved scroll. |
| [React Effects](https://react.dev/reference/react/useEffect) | Official fetching and cleanup guidance | Ignore or abort obsolete responses; a delayed read must not overwrite a mutation that has already completed. |
| [Page Visibility API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API) | Official browser API guidance | Pause background polling while hidden and refresh on return; avoid relying on throttled timers to establish freshness. |
| [React Spectrum client routing](https://react-spectrum.adobe.com/v3/routing.html) | Official anchor and router integration contract | Keep real `href` values, source fragments, and modifier-click browser behavior in addition to SPA interception. |
| [Linear sync and offline](https://linear.app/docs/get-the-app) | Official description of local pending changes and syncing feedback | A visible sync state must correspond to an actual queue. Per Pulse has no offline write queue, so it must not promise automatic sending after reconnect. |
| [Notion offline pages](https://www.notion.com/help/use-pages-offline) | Official scope of offline support | Be precise about what is saved and where. A local unsent draft is different from a backend-saved message awaiting an AI connection. |

The Radix Dialog demo was also exercised directly in the browser: opening the dialog focused the first field; Shift+Tab wrapped to Close; Escape closed it and returned focus to its invoker. This supports preserving Per Pulse's native dialog foundation, with app-specific focus handling only when navigation creates a new context.

## Browser evidence from the unchanged baseline

Baseline desktop and mobile screenshots were inspected at `qa/baseline-home.png`, `qa/baseline-work.png`, and `qa/baseline-mobile.png` outside the repository. The main transcript is already readable and quiet. On mobile, message actions and the disconnected queue note are too faint to discover; on desktop, the return action at the far top-right has the visual weight of metadata.

A temporary Playwright audit (`qa/audit-baseline.mjs`, also outside the repository) exercised the existing interface using an isolated example workspace and explicitly mocked API responses for failure states. It did not modify the product implementation or imply live model activity.

| Baseline experiment | Observed result |
| --- | --- |
| Focus an inline Reading room reference and activate with Enter | Navigation succeeds; active element becomes BODY rather than the destination heading. |
| Inspect the work page's Home source link | Its href is `/`, with no message anchor. |
| Return API `needs_attention` and message `blocked` | Conversation displays no status text. |
| Return configured `working`, then abort the next poll | The UI continues showing Working with no connection alert. |

Screenshots `qa/baseline-attention.png` and `qa/baseline-stale.png` record the last two fixtures. These observations turn the earlier code findings into directly reproducible acceptance checks.

## Implementation boundary and agreed contracts

No implementation edits before 15:55:43 UTC. After the study window, this agent owns `useWorkspace.ts`, `storage.ts`, a new `status.ts`, and focused tests. App/component integration and styling remain with the parent and design agent.

Planned additions preserve the existing runtime/API: source-message fragments; validated per-workspace navigation state; safe return eligibility; return-reference focus metadata; visible synchronization failures; deduplicated read refresh; provider freshness shared by the active view and workspace index; bounded polling while actual work is active; refresh on visibility/focus/online without retrying writes.

The activity helper contract is `getPageActivity(status, configured, stale?)` and `getMessageActivity(status, configured, stale?)`, returning `{ label, detail, tone }`, where tone is `neutral`, `active`, `attention`, or `error`. Idle says Ready, not Complete. Disconnected queued work says Saved with a clear AI-disconnected qualifier. Missing approval operations are not fabricated.

## Final study decisions

- Native page/reference persistence checks passed: `python -m pytest tests/per_pulse/test_pages.py -q` reported 8 passed before implementation.
- The current API has no offline write queue, approval action, turn-stop action, or older-message pagination. The UI must not invent any of these capabilities. An anchor outside the latest 500 messages needs an honest unavailable-source notice.
- Runtime `uncertain` means an outcome cannot be confirmed. Its recovery copy should recommend reviewing the conversation, not blindly resending a mutation.
- Synchronization failure should say Updates paused, because an HTTP failure is not necessarily a loss of internet connectivity.
- The narrower delivery window requested during the study prioritizes source anchors, return/focus continuity, provider consistency, visible refresh errors, and focused state tests. Background-index refresh and broader polling changes are deferred.
- Applied the React Best Practices skill to the forthcoming hook work: independent reads remain parallel, subscriptions use primitive dependencies, and browser storage stays versioned and limited to drafts/positions.

## Implementation handoff

Implementation began only after a clock check at 15:55:55 UTC. By 15:59:11 UTC, the status/navigation helpers and hook changes passed eight focused frontend tests and strict TypeScript checking. The parent integrates the helpers and performs the full browser flow. Broader background page-index polling remains deferred under the shortened delivery window.

The helper tests cover direct source URL round trips (including encoded identities), malformed fragments, cross-workspace history rejection, return-focus scope, disconnected/stale status honesty, distinct blocked/failed/uncertain states, and draft isolation/restricted storage. They use the project's existing TypeScript compiler with the native Node test runner and add no package dependency.

## Integrated browser verification

The updated production bundle was exercised with the same isolated Playwright fixture as the baseline. All five checks passed:

1. Keyboard activation of an inline page link now focuses the destination H1 instead of BODY.
2. The Reading room source href contains its real Home message fragment.
3. A mocked backend blocked/needs-attention state renders visible Needs attention text.
4. Aborting a configured working page's next poll renders Updates paused and a persistent Refresh updates action. A successful refresh then removes the notice, preserves the transcript, and renders Ready.
5. Hard navigation to the real Home source URL focuses the exact source message article.

The before/after browser fixtures are explicitly synthetic API states; they do not claim live model execution. Updated attention/stale screenshots were visually inspected. The temporary audit script is `qa/audit-updated.mjs` outside the repository.

A test selector caveat was verified against [MDN's :target documentation](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Selectors/:target): pushState/replaceState do not update the native target element. Source-navigation assertions therefore inspect the hash identity and focused message, not only the CSS pseudo-class.
