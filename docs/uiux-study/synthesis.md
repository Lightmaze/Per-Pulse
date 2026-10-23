# Per Pulse core UI/UX — research synthesis

Research window begins at 2026-10-23 15:25:43 UTC. Implementation must not begin before 15:55:43 UTC. This note is research and design planning, not a claim that changes have shipped.

## Product and existing strengths

Per Pulse is one continuous intelligence with Home and Work contexts. The meaningful loop is a Home thought, an embedded reference, a focused conversation, a return to the original reading position, and an intentionally selected result brought Home with its source. The existing React product already has the right light reading surface, serif page identity, inline page links, persistent per-page drafts, and a real backend. Preserve those strengths.

## Decisions grounded in the study

| Studied mechanism | Decision for Per Pulse | Verification |
|---|---|---|
| Linear's quieter navigation and consistent view header | Keep the warm sidebar and white prose plane; use a small contextual state row rather than adding dashboard metrics | Inspect Home, Work, narrow and long-title screens |
| Notion's links, anchors and backlinks | Every source link carries its message anchor; a Page context surface exposes incoming and outgoing references without changing page identity | Open source in the same tab and a fresh tab; return to the original message |
| Raycast's primary action and discoverable action bar | Show useful message actions at rest; distinguish Back from Bring to Home | Mouse, keyboard and touch checks |
| Granola's location-defined chat scope | The composer names the current page and retains its draft when changing context | Switch Home/Work while unsent text exists |
| shadcn Message Scroller's reader-intent model | Polling must not steal scroll or focus; offscreen replies get an explicit jump action | Simulate new messages while reading above the latest turn |
| Radix and WAI-ARIA dialog/combobox behavior | Editing/search opens with the useful field focused; Arrow keys and Enter select results; Escape restores focus | Reproduce baseline Cmd+K/Enter failure, then verify corrected behavior |
| Spectrum/Geist status semantics and Carbon inline loading | Saved, queued, working, needs attention, interrupted and stale are different states; motion only denotes current real work | Backend state fixtures and interrupted polling |
| Fluent/Carbon notification placement | Persistent connection failures stay next to the conversation with recovery; confirmations may use a toast | Abort reads and writes separately; preserve the draft |
| CommonMark/react-markdown's syntax-tree rendering | Render real assistant Markdown safely and preserve embedded session references outside code and existing links | Lists, emphasis, fenced code, tables, source links and unsafe URL/HTML fixtures |

## Baseline evidence from the actual application

- At 1440×1000 and 390×844, the reading surface is coherent, but operational text/actions are much smaller and fainter than prose. The mobile Bring to Home action looks close to disabled.
- Home and Work drafts survive ordinary navigation in an actual browser. Keep the existing workspace/page storage boundary.
- After entering and returning from a page, browser focus is BODY. This breaks keyboard continuity.
- Opening the native Bring to Home dialog initially focuses Close. Cmd+K also opens on Close, so Enter closes the picker instead of entering a page.
- Backend `needs_attention` and blocked message fixtures have no visible status in the current UI.
- A failed polling request leaves an old Working label and no error message.
- Source hrefs contain the page but omit the specific message, so normal browser new-tab behavior loses provenance.
- The current message renderer splits paragraphs only; real Markdown syntax would be shown literally.

These are local synthetic/example workspace observations, not findings about a user's private history or a real connected model run.

## Proposed implementation inventory (some items deferred)

1. Semantic typography/color/spacing states; readable utility text; visible focus and usable touch targets.
2. True page/message activity and recoverable stale-state feedback.
3. Page context sheet: origin, incoming links, linked pages; ordinary links remain normal browser anchors.
4. Message Markdown and source-target highlighting; discoverable copy/new-page/link/bring actions.
5. Scoped composer, keyboard page picker, source/destination return preview and useful dialog autofocus.
6. Navigation-only focus restoration, direct source hashes, per-page in-flight sends and no background focus stealing.
7. Browser regression proof of the actual persistent core loop, plus explicitly synthetic exceptional-state fixtures.

## Reference sources

- https://linear.app/now/behind-the-latest-design-refresh
- https://linear.app/now/how-we-redesigned-the-linear-ui
- https://linear.app/docs/linear-agent
- https://www.notion.com/help/create-links-and-backlinks
- https://www.notion.com/help/views-filters-and-sorts
- https://manual.raycast.com/ai/ai-chat
- https://www.raycast.com/blog/a-fresh-look-and-feel
- https://help.granola.ai/article/chatting-with-your-meetings
- https://culturedcode.com/things/features/
- https://www.radix-ui.com/primitives/docs/components/dialog
- https://www.radix-ui.com/primitives/docs/components/hover-card
- https://vercel.com/geist/command-menu
- https://vercel.com/geist/context-card
- https://ui.shadcn.com/docs/components/base/message-scroller
- https://spectrum.adobe.com/web/rsp/components/status-light
- https://developer.apple.com/design/human-interface-guidelines/layout
- https://www.untitledui.com/components/empty-states
- https://www.carbondesignsystem.com/building-blocks/core/patterns/notifications
- https://www.carbondesignsystem.com/building-blocks/core/components/inline-loading/guidelines
- https://fluent2.microsoft.design/components/web/react/core/drawer/usage
- https://fluent2.microsoft.design/components/web/react/core/toast/usage
- https://www.w3.org/WAI/ARIA/apg/patterns/combobox/
- https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html
- https://github.com/remarkjs/react-markdown/blob/main/readme.md

The detailed product, design-system, and experience-audit notes distinguish downloaded images, written documentation, historical reports, and inaccessible sources. Mobbin required a paid plan; no Mobbin screen was inspected. Craft/Granola imagery that failed to download is not represented as visual evidence.
