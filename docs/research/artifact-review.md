# Per-Pulse integration review: Duet/dot contracts and file results

Review date: 2026-10-23. Review scope: the in-progress `Lightmaze/Per-Pulse` product adapters, execution controls, and file-result projection. This is a local working-tree review, not a review of a deployed release.

The implementation respects the key architectural boundary: pages retain the existing native Home/Work identities, the Kernel owns execution and approvals, and read-only product adapters expose the original durable state. No new agent runtime or authoritative session/artifact store was introduced for the visible workflow.

## Findings and disposition

| Finding | Disposition observed |
|---|---|
| A shared UI pending flag initially disabled Stop while an approval continuation was running. This prevented the browser from using the backend's independent cancellation path. | Fixed by separate approval and stop pending state in `ExecutionStatus.tsx`; Stop remains available during a pending approval continuation. |
| Historical uncertain model rounds could keep a page in an actionable recovery state even after later work succeeded. | Execution adapter now distinguishes historical uncertainty from an unresolved current turn. |
| A file path in a shared workspace is not proof that a particular page produced its current contents. | `PageArtifacts` requires a confirmed native write receipt and checks the current raw SHA-256 against its recorded version before returning content. A later writer produces `changed` with no content. |
| A successful file effect can survive a later turn/continuation failure. | Artifact inclusion is based on the durable action result, not a successful turn or assistant claim. |
| Internal file-access error codes should not become primary user-facing copy. | Fixed with plain-language preview reasons and verification-at-open copy; no additional runtime policy. |

## Implemented artifact contract

`src/per_pulse/artifacts.py` is a read-only projection. `snapshot(page_id)` reads a bounded set of recent successful native actions and does not open workspace files or instantiate a runner. Only supported `mutate_file` receipts with consistent action/operation IDs, structured-workspace provenance, change/checkpoint IDs, and valid content digests are admitted.

The artifact ID is the action ID, so `read(page_id, artifact_id)` can resolve exact durable ownership even after observer events expire or a result falls outside the recent listing. Version identity is the recorded raw SHA-256; turn, action, change, checkpoint, and operation IDs remain available as source evidence. The original ActionRecord and ChangeRecord path remain authoritative.

Preview reuses `WorkspaceFiles.read`: workspace-relative dirfd traversal, no symlink following, a single regular file, hardlink rejection, UTF-8 validation, a 1 MiB read ceiling, and credential checks. It then compares the original bytes' digest before redacting and bounding the visible text to 64 KiB. Unavailable or changed files return no content. This endpoint accepts no arbitrary path and performs no raw download or checkpoint restoration.

Listing is intentionally metadata-only. A preview verifies the version when opened; it is not a continuously refreshed filesystem view. Command-created files and binary formats are outside the current projection contract.

## Verification

Executed: `python -m pytest tests/per_pulse/test_artifacts.py -q` using the team's prepared Python environment. Final result: **10 passed in 5.77 seconds** after HTTP integration and adversarial boundary assertions were added.

The tests use explicitly synthetic HTTP model choices with the real World, Kernel, approval routing, persistence, and file effects. They cover observation without session creation; pending, denied, cancelled, and failed action exclusion; confirmed change provenance; cross-page denial; overwrite detection; restart without effect replay; leaf/root symlinks, workspace directory replacement, hardlinks, file-type/size/credential guards; redaction and preview bounds; retained identity after event retirement and list truncation; and HTTP list/read/PageView plus `Cache-Control: no-store`.

## Existing Kernel edges surfaced by the tests

A patch can successfully update a normal line in a file containing a short inline secret assignment. Its diff is correctly redacted and the file action is durably successful, but the later transcript continuation can reject the already-redacted tool result as `redaction_required`. The test confirms the successful artifact remains readable despite that continuation failure. The artifact adapter does not relax the Kernel's credential policy.

Large structured receipts can also be replaced by the Kernel's bounded payload wrapper. This was reproduced with a roughly 50,000-byte single-line file: a tiny exact patch writes successfully but produces a roughly 100,000-byte unified diff, and the bounded receipt loses its identity fields. If required metadata is absent, the current adapter omits the unprovable result and marks the listing incomplete. A future scope-checked public ChangeRecord reader would make projection independent of diff/result payload size while retaining the same authoritative store. No private SQL fallback was added in this change.

The file reader blocks root symlinks, and a replacement workspace directory containing different file bytes produces `changed` without content. It validates the recorded content digest, not a permanent filesystem object identity. An identical copy remains the same content version; the adapter does not promise physical inode identity or historical file restoration.
