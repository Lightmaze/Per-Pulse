# Per Pulse — design system study

Research started 2026-10-23 15:26 UTC. Product implementation is held until the shared 15:55:43 UTC research boundary.

## Product constraint

Per Pulse is one developing intelligence. Home and Work pages are contexts; they are not agent identities. Preserve Home → inline session reference → independent page context → original position / selected result with source. The current React product is light, warm, green-accented, and reading-oriented. `src/pulse_system/product/life_assets/tokens.css` belongs to the older dark workspace and is not the design source for `web/src`.

## Evidence ledger

| Reference | What was actually studied | Transfer to Per Pulse |
|---|---|---|
| [Linear 2026 refresh](https://linear.app/now/behind-the-latest-design-refresh) | Full official article and downloaded/visually inspected before/after sidebar image. The new sidebar reduces inactive contrast and icon size while increasing grouping space. | Let Home's current conversation own attention; side navigation is orientation, not a competing dashboard. Use spacing and selection to establish grouping before adding boxes. |
| [Linear 2024 redesign](https://linear.app/now/how-we-redesigned-the-linear-ui) | Official process: view families, header structure, environment/appearance/hierarchy stress tests, semantic color aliases. | Exercise the same shell in Home, Work, empty, running, narrow, long-title and source-return states. A good homepage alone is insufficient. |
| [Radix color scale](https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale) | Semantic allocation across 12 steps; surfaces, interactive states, borders and text have separate jobs. | Create semantic variables for rest/hover/selection, border/control/focus, primary/secondary text. Do not reuse one green token for everything. |
| [Geist colors](https://vercel.com/geist/colors) | Background hierarchy, distinct interactive states, border intersections and alpha use. | Draw shared boundaries once; use neutral structure and reserve green for meaningful actions and source links. |
| [Geist typography](https://vercel.com/geist/typography) | Separate families for multiline prose, single-line labels, buttons and mono metadata. | Keep conversation prose comfortable; dense chrome needs a smaller and independent scale, not arbitrary per-selector sizes. |
| [Geist materials](https://vercel.com/geist/materials) | Material role encodes elevation, radius, border and shadow together. | Three surface roles suffice: canvas; resting reference/page; floating dialog. Avoid nested raised cards in the conversation. |
| [Radix accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility) | Roles, labels, expected keyboard behavior and focus movement form one contract. | Source navigation, return, dialog opening/closing and composer refocus must preserve a sensible next step. |
| [shadcn theming](https://ui.shadcn.com/docs/theming) | Semantic background/foreground pairs; global CSS variable contract. | Introduce meaningful tokens in existing styles rather than a framework migration. |
| [shadcn sidebar](https://ui.shadcn.com/docs/components/sidebar) | Header/content/group/footer composition, collapsible sidebar and mobile state. | Retain the current coherent shell; keep content scroll separate from fixed navigation utilities. |
| [Apple layout](https://developer.apple.com/design/human-interface-guidelines/layout) | Official Markdown source: hierarchy by alignment/indentation; progressive disclosure; adaptive layout based on available space. | Use a responsive content column and move secondary detail into a controlled surface on narrow screens; retain function at every size. |
| [Apple materials](https://developer.apple.com/design/human-interface-guidelines/materials) | Official Markdown source: materials distinguish controls from content, with restrained effects. | No decorative glass in the reading surface. If overlays blur, preserve text contrast and layer meaning. |
| [Apple motion](https://developer.apple.com/design/human-interface-guidelines/motion) | Official Markdown source: short purposeful feedback, cancellable motion, reduced-motion alternatives. | Opening and returning can subtly mark continuity. Do not animate the whole workspace or delay frequent actions. |
| [Apple writing](https://developer.apple.com/design/human-interface-guidelines/writing) | Official Markdown source: clear action words, consistent terms, localized error recovery. | Keep Home / page / source / return terms stable. Avoid runtime jargon and vague celebratory status text. |
| [Apple accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) | Official Markdown source: readable scaling, contrast, multiple state cues, adequate targets. | 44px touch targets for core controls; visible focus; state icon + text; usable 200% text sizing. |
| [Geist command menu](https://vercel.com/geist/command-menu) | Global shortcut, query preservation, focus trap/return, recent set and keyboard behavior. | Existing ⌘K page picker should be useful before input, announce counts and preserve expected Escape/Enter behavior. |
| [Geist empty state](https://vercel.com/geist/empty-state) | First-use, no-results, completed, denied and error states differ; one primary next step. | A quiet Home first-use prompt, search no-results reset, and failed request retry need distinct copy/actions. |

Mobbin was tried with Linear and Raycast screen searches. Both returned “requires a paid plan”; no Mobbin image was inspected and no visual claim is attributed to Mobbin.

## Initial proposed system (design decisions, not copied source values)

- Keep white reading canvas and a warmer navigation surface. Use one deep green accent with explicit text-on-accent, soft selection, hover, and focus variants.
- Keep serif only for page identity; use system sans for conversation and controls. Separate prose (16px, generous line height), labels (13–14px), and metadata (12px). Avoid 10px operational text.
- Use a 4/8/12/16/24/32/48 spacing progression. Make section breaks visible through space before adding rules.
- Resting content is flat. The composer can have one subtle border and small focus treatment. Dialogs receive the strongest elevation.
- Inline page links remain recognizable inside prose. Expanded context references can expose a title, one-line purpose, state and an entry affordance without becoming dashboards.
- Green indicates links and actions; running/ready/failed require explicit labels and do not rely on color alone.
- Every interactive state needs rest, hover, active/selected, keyboard focus, disabled/busy and error behavior. Busy controls retain width and labels.

## Source-code hypotheses to visually verify

- `return-to-thread` at 11px gives a central continuity action too little emphasis.
- `icon-button` at 30px and mobile message actions at 30px need larger effective hit areas.
- `message-actions` hidden by opacity can impair discovery; retain keyboard focus visibility and make the most important action persistent when useful.
- `composer-wrap` has 92px top padding; inspect long/short conversations and narrow screens before altering.
- Existing local drafts and scroll restoration already encode the right continuity behavior; design work should expose and preserve them.


## Second pass: continuity and interaction states

[shadcn Message Scroller](https://ui.shadcn.com/docs/components/base/message-scroller) provides an especially relevant model: keep the reader's location stable, treat input/selection/link opening as intent, show offscreen updates, and make returning to the current reply explicit. This is a behavior reference, not a recommendation to replace the existing scroll store or add a new state owner.

[Radix Hover Card](https://www.radix-ui.com/themes/docs/components/hover-card) is optional link preview; [Popover](https://www.radix-ui.com/themes/docs/components/popover) is button-triggered rich content. Essential source/return information must remain available without hovering. A preview may help assess a page before opening it, but cannot own the only entry or status representation.

[Adobe Spectrum attention hierarchy](https://spectrum.adobe.com/foundations/attention-hierarchy) extends hierarchy to shape, motion and elevation. For this product, conversation/current decision is the primary attention group. Navigation, provenance and background state should be easy to consult without presenting three equally bright focal points.

[Adobe status light](https://spectrum.adobe.com/fragments/components/status-light/usage-guidelines) requires a text label; use neutral label color beside the semantic signal. Per Pulse should distinguish queued, working, error and completed through words and/or recognizable shape, rather than inventing progress percentages.

### Color calculations from current React CSS

WCAG contrast ratios computed using relative luminance, not sampled from screenshots.

| Current pair | Ratio | Design implication |
|---|---:|---|
| Main text `#252923` / white | 14.79:1 | Excellent reading contrast; keep. |
| Muted `#747873` / white | 4.49:1 | Borderline for small text; darken slightly. |
| Muted `#747873` / sidebar `#f6f5f2` | 4.12:1 | Raise contrast for navigation/supporting labels. |
| Status `#8a8d85` / white | 3.37:1 | Too faint for current 10px size; use a stronger semantic foreground. |
| Hint `#8b9087` / white | 3.26:1 | Increase size and contrast. |
| Accent `#315447` / white | 8.43:1 | Suitable for readable links and primary button backgrounds. |
| Candidate secondary `#667065` / white | 5.16:1 | Better support-text default. |
| Candidate secondary `#667065` / sidebar | 4.73:1 | Retains hierarchy while readable. |

Selected/colored surfaces need their own contrast check; `#667065` on the existing selected surface is only 4.23:1. Use the primary/green foreground on selected navigation items, not a universal muted override.

### Component contract to carry into implementation

| Component | Must communicate | State behavior |
|---|---|---|
| Main navigation | Current place, available places | Selected state survives hover; focus is distinct; narrow view opens an accessible controlled surface. |
| Embedded page reference | Where it leads and why it belongs here | Normal link works without preview; title wraps safely; source origin retained; loading does not relocate surrounding text. |
| Return control | Return to exact originating context | Available before scrolling; distinguish ordinary back from bringing content Home; destination/source naming where space permits. |
| Composer | Which context receives the message | Stable input height; explicit link affordance; draft survives navigation; saving state cannot be mistaken for model progress. |
| Message actions | What can be done with this thought | Core action discoverable; focus reveals actions; touch controls meet useful hit area; text selection does not disappear before capture. |
| Search/picker | Search scope and next action | Useful empty query; keyboard highlight; count announced; no-results differs from no-pages; Escape returns focus. |
| Dialog | A focused, reversible decision | Labelled heading; focus enters and returns; Enter submits only valid data; errors appear beside form; busy submission preserves layout. |
| Source result | Content plus its origin | Source remains a real link; target message visually indicated after jump; provenance is quieter than content. |
| Page state | Queued/working/ready/error | Word + signal; subtle motion only while active; explicit recovery for failed request. |
| Empty view | Why it is empty and what to do | One clear primary next step; no invented metrics or fake runtime activity. |

### Visual references inspected from shared research

The following images were actually opened: Linear Pulse update/editorial digest, Linear Inbox, Raycast AI chat with settings, and Raycast clarification. Their URLs and provenance are recorded in `product-research.md`. Product UI colors are not copied. Relevant visual mechanisms: quiet source metadata, a readable primary prose plane, secondary controls tied to context, and compact but legible command groups.


## Third pass: baseline comparison and exact constraints

Baseline screenshots inspected: `qa/baseline-home.png`, `qa/baseline-work.png`, `qa/baseline-mobile.png`. The current identity is appropriate: warm white, readable unboxed conversation, green inline links, serif page title. The change should refine this identity, not replace it with generic dashboard tiles.

The most material visual failure is operational visibility. On mobile, “New page”, “Link page”, and “Bring to Home” are almost invisible; on desktop, the back action reads like tiny metadata. State text is also too small and faint to explain why a sent message has no reply. The first fix is a hierarchy of visible actions and honest state, before decorative styling.

Additional images inspected: Spectrum attention hierarchy (one colored primary action, neutral navigation selection), Spectrum readable paragraph width, Linear header-system diagram, Raycast action-panel screenshot. These support stable shell/view/content separation and an explicit primary action.

### Token specification

These are project decisions derived from the research, not purported official token values.

| Role | Value | Use |
|---|---|---|
| Canvas | `#ffffff` | Main reading plane and input surface. |
| Navigation | `#f6f5f2` | Sidebar and separate shell identity. |
| Quiet surface | `#f7f8f5` | Context/source disclosure, restrained hover. |
| Selected surface | `#e5eae2` | Current navigation item; pair with text or accent foreground. |
| Primary text | `#252923` | Conversation and title, 14.79:1 on white. |
| Secondary text | `#667065` | Metadata and helper copy, 5.16:1 on white / 4.73:1 on sidebar. |
| Accent | `#315447` | Links and primary action, 8.43:1 on white. |
| Accent hover | `#244335` | Interactive feedback, not a second accent family. |
| Attention | `#835925` | Explicit attention state, 6.14:1 on white. |
| Error | `#a34738` | Failed state and recovery, 5.97:1 on white. |
| Focus | `#446d59` | 2px visible outline with space from control. |
| Prose | 16px / 1.7 | Main conversation; 17px may be used on wide screens. |
| UI | 14px / 1.5 | Navigation and standard form controls. |
| Supporting UI | 13px / 1.5 | Return links, toolbar actions, descriptions. |
| Metadata | 12px / 1.5 | State, provenance, shortcuts. No operational 9–10px text. |
| Spacing | 4, 8, 12, 16, 24, 32, 48 | Repeated relationship scale, not forced into every dimension. |
| Radius | 6, 10, 14 | Small controls, composer/context, floating dialog. |
| Motion | 120–180ms | Hover/focus/overlay only; respect reduced motion. |

### Responsive and accessible behavior

- [W3C reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html): verify the conversation and controls at a 320 CSS px width. Code/table overflow belongs inside its own region rather than widening the entire page.
- [W3C text spacing](https://www.w3.org/WAI/WCAG22/Understanding/text-spacing.html): fixed heights must not clip when users increase line, paragraph, letter and word spacing. These test values are adaptability criteria, not required default typography.
- [W3C focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html): persistent headers/footers must not cover keyboard-focused controls. Keep composer in document flow unless a tested pinned approach can preserve visibility.
- [W3C target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html): 24px is the AA minimum with exceptions; 44px remains a deliberate touch usability target for common controls. Inline text links are not forced into button-sized blocks.
- [MDN safe area](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/env): account for device safe-area insets around bottom controls and overlays, with a fallback.
- [MDN reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion): remove nonessential movement while keeping the state visible.

### State presentation contract

| State | Tone | Presentation |
|---|---|---|
| Saved, AI disconnected | Neutral | Readable sentence; no spinner. |
| Working, live connection | Active | Small signal plus “Working”; no invented percentage. |
| Awaiting approval / blocked | Attention | Named reason and action when the runtime supplies it. |
| Failed | Error | Explicit failure with retry/recovery near the affected content. |
| Connection stale | Neutral or attention | Stop active animation; state connection problem, preserve existing content. |
| Completed / idle | Neutral | No permanent success celebration; stable current content. |

The exact labels and runtime mapping remain owned by the shared activity helper. CSS only styles those meanings.


## Fourth pass: what not to copy

A library convention is not automatically a product requirement.

- Apple’s current toolbar guide favors standard native Back/Close symbols and system grouping. Per Pulse is a custom web interface with a specific return-to-origin contract, so a readable “Back” label can improve clarity. Keep its navigation meaning distinct from the content-changing “Bring to Home” action.
- Geist’s Status Dot component is expressly scoped to deployment lifecycle vocabulary. Per Pulse should reuse the principle of text plus signal and stable state changes, while keeping the runtime’s own page/message meanings.
- Geist’s Sheet has its own nonmodal defaults. The existing Per Pulse native dialog can be styled laterally without claiming identical interaction behavior; focus containment and return need to match the actual implementation.
- Native Liquid Glass, desktop pointer-hover previews, and heavy command-panel density do not transfer wholesale to a mobile reading workspace.
- The Apple typography guide supports default serif/sans system families and discourages unnecessary font-family proliferation. Keep Georgia for page identity and the existing system sans stack for UI; do not download restricted platform fonts or introduce a performance-heavy font dependency.

### Additional source conclusions

[Geist Toast](https://vercel.com/geist/toast) supports a short acknowledgment after a user action; required recovery and primary navigation need a persistent surface. Do not narrate every async phase with a stack of toasts. For Per Pulse, “Added to Home” is suitable; the source link remains in the saved result itself.

[Geist Loading Dots](https://vercel.com/geist/loading-dots) distinguishes saving from completion and rejects multiple simultaneous indicators in the same line. Use movement only for actual ongoing work; the word “Saved” must never retain an ongoing-work animation.

[MDN forced colors](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/forced-colors) explains that shadows and some backgrounds disappear. Keyboard focus and current-page identification need an outline/border/text cue that survives high-contrast mode, not only a pale green fill.

The change should improve functional clarity while preserving an unboxed conversation. A new context surface is a supporting layer. It must not turn Home into a metrics dashboard or create multiple artificial agent identities.


## Fifth pass: reading continuity and verification plan

Additional actual image review: Linear’s border and tab comparisons, the finished 2024 light interface, Notion’s private-page pane, Linear Agent tabs/history, Raycast’s collapsed long message, and Linear’s individual Pulse update. The useful pattern is stable content identity with local supporting controls. Per Pulse does not need Linear’s multipane density or Raycast’s conversation bubbles to gain that clarity.

The following visual checks are required after implementation:

| Scenario | What must remain clear |
|---|---|
| Home with references | Main prose dominates; inline links keep a recognizable underline and retain their source meaning. |
| Short Work conversation | Context/status visible beneath the heading; composer stays naturally toward the bottom without a large fixed overlay. |
| Long Work conversation | Reader’s place remains stable; latest-content control is reachable without covering focused controls. |
| Returned Home result | Source label links to the exact message and the target highlight identifies the result without a full-screen transition. |
| 320px and 390px widths | Back, Bring to Home, send, link and close remain reachable; action groups wrap instead of clipping. |
| Long titles and URLs | Heading and source text wrap; navigation can truncate when opening reveals the full title. |
| Form/dialog at narrow width | Fields retain labels, controls remain visible, body scroll stays inside the dialog. |
| Keyboard picker | Focus stays recognizable; selected result has a distinct background and text; Escape restores the invoking control. |
| Reduced motion | Status words and selection remain; no essential information disappears with animation. |
| Forced colors | Outline/border identify focus and current selection even when shadows/backgrounds disappear. |
| Text at 200% | No fixed-height clipping; metadata/action groups wrap; no page-wide horizontal scroll. |
| Markdown code/table | Only the individual wide content region scrolls horizontally. |

References for continuity: [W3C consistent identification](https://www.w3.org/WAI/WCAG22/Understanding/consistent-identification.html) and [link purpose](https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context.html). A page link should agree with its destination title, and the same recurring control should retain a recognizable name and purpose. Keyboard focus alone must not navigate or open a new blocking context; activation expresses that intent.


## Stage scope after user steering

The user shortened the remaining work to a stage wrap ending at 16:06 UTC. The mandatory research boundary remains 15:55:43 UTC. This stage will therefore apply targeted improvements to the existing UI only: readable supporting type, visible actions, adequate mobile targets, clear Back wording, accessible focus, honest activity tones, picker selected state, and concise composer/return context. The larger context sheet, rich Markdown rendering, and structural redesign described above remain researched directions rather than delivered features.

Final implementation priorities:

1. Preserve the white reading canvas, warm sidebar, serif identity and green inline links.
2. Raise secondary foreground contrast; remove 9–10px operational labels.
3. Keep message actions visible and allow mobile wrapping; common touch controls reach 44px.
4. Distinguish source navigation from bringing content Home.
5. Make active, attention, error and disconnected state explicit using labels and semantic styling.
6. Keep search selection, focus and disabled states distinct.

No product source has been edited during this research interval.


Research interval completed at 15:55:52 UTC, after the shared 15:55:43 boundary. The first product edit was made after that timestamp. Targeted CSS implementation completed at 15:57:53 UTC. PostCSS parsing and whitespace/diff checks passed. Browser integration review remains to follow.
