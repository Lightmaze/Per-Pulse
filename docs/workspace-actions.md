# Workspace actions and file results

`per-pulse` lets the model change text files in its workspace by default, the
way current coding agents do: a change inside the workspace applies at once,
with no approval prompt. Each change appears under **Files from this page**,
where **Undo** puts the file back as it was before that change (a file the change
created is removed). Undo only applies while the file still has that change's
content; if the file changed again afterwards, undo the later change first.
Undo uses the checkpoint the Kernel records before every change.

Start with `--ask-before-edits` to approve each file change instead, or
`--no-workspace-actions` to let the model read files without changing them.
Home can still create and follow up in its Work pages, and Works can send their
selected reports back to Home while file actions are disabled.
Commands (available when `P_PULSE_COMMAND_IMAGE` names a container image) always
ask first. A pending approval and exact-turn Stop stay in their original page.
An approval waits for you for up to a week, and a turn you start has no time
limit: it ends when it finishes, when you press Stop, or after 256 tool steps.
Turns that run unattended (background Pulse, connections, `/tick` steps) stop
after ten minutes, so one cannot hold an execution slot indefinitely.
Example mode never calls a model or changes files. See
[Core Harness](harness-foundation.md) for authority, recovery and limits.

Completed text-file changes can be opened from **Files from this page**, with
original-action provenance and digest checks.

**Download file** takes the complete verified text result out of the workspace.
It checks the file again when requested and verifies the received bytes before
starting the browser download. A later overwrite or a missing file produces
an explanation; **Refresh file preview** checks its current state.
Downloads preserve UTF-8 text, line endings and the original filename, including
non-ASCII names. Previews and downloads show your file as it is, including any
keys or passwords in it; only a result holding a stored `[REDACTED]` or
truncation marker from a model reply is not offered as an original file.
This works for existing results while disconnected and after restart. File
access remains limited to text up to 1 MiB; the native write path currently
has a separate 64 KiB output bound. It does not restore older overwritten bytes.

The **Materials** flow accepts UTF-8 text, Markdown, CSV, JSON or code, and
PDFs (read as their text), in Home or Work. **Upload file** saves the original bytes; **Add to draft** selects
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
show the file as it is. The workspace library previews up to 64,000 characters.
Files are shared native workspace material, not isolated
per-page filesystem permissions.

Ordinary `read_file(path, offset=0, max_chars?, expected_digest?)` also returns a
bounded passage. Its `position` reports the start, end and total Unicode
character counts; `eof` means the passage reached the end of the available text.
Pass the returned `next` arguments to `read_file` to continue the same source
version. If that version changed, restart at zero without `expected_digest` to
inspect the current file. An offset without a digest can read any position in
the current file. Omitted `max_chars` uses the session's payload allowance;
the complete tool result, including its continuation, stays within that bound.
For PDFs, `eof` concerns the extracted text: `pdf_complete` and
`pdf_pages_without_selectable_text` separately describe extraction limits and
pages with no selectable text.

To locate a specific phrase in a long source, the native `reading.find` operation
searches literal text in the source and returns up to eight short excerpts
with exact source coordinates and digests. It checks an optional expected file
version, identifies when more matches remain, and reports when its search stopped
early. Finding a passage is distinct from reading or understanding the whole file.
To read more at a selected hit, use `reading.follow(receipt_id, match_index)`
with the find result's receipt and zero-based match index. It starts a bounded
passage at that original match and supplies a normal continuation receipt.
Follow rechecks the complete source bytes and workspace root;
it accepts neither an invented offset nor another Work's private receipt.
After relocating a workspace, run find again before following a hit. The
coordinate unit is Python Unicode characters in the source text, not raw
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
