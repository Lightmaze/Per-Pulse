# Deliver the original finished Work file to Home

A Work can finish a real file in the native workspace and report it to Home
without losing the actual result behind an assistant's description. This is
**one World, one Kernel, one SQLite history**: the file action and its original
Work remain the source of truth.

## User journey

1. In Work B, a normal authorized `mutate_file` action writes the deliverable.
   The returned native result includes its `action_request_id`.
2. In the **same successful Work turn**, `work.report(kind, note,
   artifact_id=<action_request_id>)` attaches that exact completed file to the
   selected Home report. The action may also cite a verified earlier Work A
   passage using the existing source tuple. No separate model call is created.
3. When the Work turn settles, the existing outbox delivers the one report to
   Home. Home receives a historical file-action receipt (path, digest and
   action ID) as attributed source data, not a new user command or permission.
4. Home shows **File delivered from Work** with a direct **Open verified file**
   reader, **Download file**, and a link back to the producing Work. The preview
   uses the existing page-scoped `/api/pages/{pageId}/artifacts/{artifactId}`
   endpoint; the download uses that exact Work's existing version-verified
   `/download` endpoint and checks the original digest again. The original Work
   still owns Undo.
5. After restart, an old report links the same native action. If another
   writer changed the path, or Undo revoked the original change, Home refuses
   to display changed or unavailable bytes as the old result. The historical
   report remains available for audit. Closing the Home preview cancels an
   in-flight download before it can start in the browser.

The optional `artifact_id` must identify a **successful `mutate_file` action
in the exact reporting Work turn**. A guessed, failed, read-only, earlier-turn,
foreign-Work or other-subject action cannot be reported as this turn's file.
The outbox rechecks the original native receipt at delivery. The Home message
projection also validates the original settled Work turn and the same native
file action, rather than trusting JSON-shaped text or mutable metadata.

Reporting does **not** approve file changes. The existing auto-apply/approval
profile controls the original `mutate_file`. Any uncertain or denied effect
remains governed by the Kernel and cannot become a completed file receipt.

## Evidence and limitations

`tests/per_pulse/test_native_report_sources.py` uses synthetic HTTP tool
choices but real file bytes, World, Kernel actions, outbox, restart, provenance,
forged-ID rejection and Undo. The installed-wheel Chromium
`tests/browser/report_source_flow.mjs` exercises the rendered Home preview,
verified original-byte download, source return, draft retention, direct download
after offline restart, and invalidation of preview and download after Undo
against the same native runtime.

These checks establish a *usable delivered artifact and original action
identity*. They do not establish that a real provider naturally chose the
right tool, remembered Work A, or improved its result. Those outcomes still
belong to the authorized, blinded real-provider gate in
[issue #51](https://github.com/Lightmaze/Per-Pulse/issues/51).
