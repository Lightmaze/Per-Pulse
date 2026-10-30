# Refine when an experience applies

A lesson could retain explicit `all`, `any`, and `not` conditions, and the
native journal could revise them, but the source-page editor exposed only its
conclusion, exceptions and decision shifts. A person could describe a narrower
lesson while leaving its old applicability unchanged.

The existing Refine lesson editor now lets the person revise applicability in
the same version-checked mutation. Simple conditions remain one text field.
Groups preserve all/any relationships and exclusions. Adding, removing or
excluding a condition is explicit; saving another field does not flatten the
existing structure. The editor reports empty passages and native size/structure
bounds before saving. The server remains authoritative, including credential
checks. There is no new evaluator, experience store or permission path.

Condition drafts use the revision opened for editing. Refreshing a changed
experience preserves the draft and prevents saving until the person explicitly
reloads the current version. Original source, encounter, sharing scope and
activation remain unchanged. Shared Works receive the revised abstraction;
private source feedback stays at its origin. Concrete examples continue to use
their note editor without lesson conditions.

## Developer expedition, 2026-10-29

The practical question was how a later encounter can narrow an earlier
generalization. Reading the existing `PageLearning.revise`, `_conditions` and
`LearningDialog` exposed the gap above. The native schema also supplied a
counterexample to a single free-text editor: replacing a nested condition with
its displayed prose would destroy a relationship already retained by the
subject. No new native capability was needed.

Two primary sources informed the implementation:

- [W3C WAI: grouping controls](https://www.w3.org/WAI/tutorials/forms/grouping/)
  led to actual fieldsets and legends, with labeled group selectors. Visual
  indentation alone would not expose those groups to assistive technology.
- [React: updating objects in state](https://react.dev/learn/updating-objects-in-state)
  informed copying the edited ancestor path instead of mutating nested objects
  shared with the original experience and the version-bound draft.

The resulting experiment preserved a nested exclusion when editing a different
leaf, rejected an oversized multibyte passage, and retained an unsaved condition
through a concurrent revision. A second representation supplied the return
test: the complete example workflow still shares exact excerpts, revises notes,
and handles later counterevidence without gaining lesson fields. These are
developer learning and product-mechanics observations, not evidence of provider
learning or improved model judgment.

## Verification

On the implementation tree based on `519205bc`:

- Production TypeScript/Vite build and 62 frontend tests passed.
- All 13 native posture-page tests passed. The extended refinement case checks
  nested applicability, private evidence, cross-Work projection and restart.
- The complete posture Chromium journey passed at desktop 1280×900 and phone
  390×844, including English/Chinese controls, draft conflict/reload, condition-only
  edits, all/any/exclusion controls, explicit removal, cancel and restart.
- The complete example Chromium journey passed all eight checks with no page
  errors. Desktop and phone condition screenshots were visually inspected.

The model transport is explicitly synthetic; World, Kernel, SQLite, source
identity, version checks and browser interaction are real. Browser plugin tools
were unavailable, so the repository Playwright workflow used the installed
Chromium. An initial run used a nonexistent Python path and timed out before
server readiness; rerunning with the actual runtime executable passed. The
existing Vite bundle-size warning remains. Screen-reader behavior was not
manually tested.

The parallel material-example route owns shared LearningDialog/catalogue files
until it releases them. Keep this candidate separate and integrate sequentially,
then validate the combined tree. Its branch checks do not certify that combined
candidate or the current main.
