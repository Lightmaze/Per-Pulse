# Workspace actions and file results

`per-pulse` lets the model propose workspace changes by default; turn this off with `--no-workspace-actions`. Each proposal needs action-specific approval, without creating a second runner. A Work's pending approval and exact-turn Stop remain in its original page; completed text-file changes can be opened from **Files from this page**, with original-action provenance and digest checks. Example mode keeps model requests and model file changes disabled. See [Core Harness](harness-foundation.md) for authority, recovery, and limits. Persistent monetary reservations and native browser tools are not yet included.

**Download file** takes the complete verified text result out of the workspace.
It checks the file again when requested and verifies the received bytes before
starting the browser download. A later overwrite, protected content, or missing
file produces an explanation; **Refresh file preview** checks its current state.
Downloads preserve UTF-8 text, line endings and the original filename, including
non-ASCII names. Redacted previews cannot be downloaded as original files.
This works for existing results while disconnected and after restart. File
access remains limited to text up to 1 MiB; the native write path currently
has a separate 64 KiB output bound. It does not restore older overwritten bytes.

The **Materials** flow accepts UTF-8 text, Markdown, CSV, JSON or code in
Home or Work. **Upload file** saves the original bytes; **Add to draft** selects
the inspected version; only **Send** submits it to the conversation.
Text and file-only drafts survive browser refresh. Each
message can include up to five files of at most 1 MiB each. PDF, images and
other binary files are not supported. A source chip opens its original text
preview; **Bring to Home** lets you include the source files with your selected
message while preserving their original page links.

The native input carries paths and recorded digests, not a claim that a model
read the files. `reading.open` can check that expected version before returning
its first passage, and continuation verifies the same source. Changed or
missing selected files are refused before sending; replacement bytes are not
silently substituted. Upload receipts and files persist across restart and
the existing stopped Pulse Image transfer. The native source API lists the latest
100 receipts per page, with a workspace limit of 1,024 receipts; older message
attachments remain accessible. Source-chip previews are bounded to 64 KiB and
redact sensitive values. The workspace library previews up to 64,000 characters.
Files are shared native workspace material, not isolated
per-page filesystem permissions.

To locate a specific phrase in a long source, the native `reading.find` operation
searches literal text in the redacted view and returns up to eight short excerpts
with exact source coordinates and digests. It checks an optional expected file
version, identifies when more matches remain, and reports when its search stopped
early. Finding a passage is distinct from reading or understanding the whole file.
To read more at a selected hit, use `reading.follow(receipt_id, match_index)`
with the find result's receipt and zero-based match index. It starts a bounded
passage at that original match and supplies a normal continuation receipt.
Follow rechecks the complete source bytes, redacted view and workspace root;
it accepts neither an invented offset nor another Work's private receipt.
After relocating a workspace, run find again before following a hit. The
coordinate unit is Python Unicode characters in the redacted view, not raw
UTF-8 bytes or JavaScript UTF-16 indices.

For expansion-aware Unicode matching, explicitly pass `casefold=true` to
`reading.find`: `STRASSE` can locate the original `Straße`, and `FFI` can
locate `ﬃ`. Matches cover whole original characters; `fi` does not match
part of `ﬃ`. The returned `case_mode` records the selected semantics, and
follow/continue still use the original source coordinates. The default
remains Python regex case-insensitive matching; `case_sensitive=true` selects
exact case and cannot be combined with `casefold=true`. Casefold adds neither
accent normalization nor locale rules. See [Unicode source search](unicode-source-search.md)
for examples and the actual prototype experiment.

A lost upload response is shown as unconfirmed and never retried automatically.
Open **Materials → uploads/** to inspect the saved file and add it to the draft.
The native file creation
and receipt storage are separate durable effects: a process or storage failure
between them can leave an unlisted file in `workspace/uploads`. Inspect that
directory before manually uploading again when no receipt can be recovered.
