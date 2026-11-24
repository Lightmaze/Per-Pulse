# Verified original source for materials reused across Works

A workspace file can be explicitly attached in more than one Home/Work context.
Previously, the second native material receipt kept its own page, SHA-256 and
workspace-relative path, but did not identify the original uploaded material.
Opening that receipt showed the file bytes without a way to revisit its source.

When a user explicitly attaches a workspace file through Materials, the native
`reference_existing` path continues to require the currently observed file
digest, native World and service-instance identity. It now looks up an actual
`user_upload` material receipt for **the same path, digest and byte length**
in the existing World-backed material ledger. When such an upload exists,
the newly created reference records `originPageId` and `originMaterialId`.
Otherwise those fields are absent: a plain filesystem file does not acquire a
fictional Work of origin. Reattaching the same verified bytes in a third Work
continues to identify the original upload, not the intermediate Work.

Opening the attachment from another Work names the original upload's
Home/Work page and displays an **Open original material from its source page**
control (also in 简体中文). It opens the exact recorded original source
material through the existing source-scoped API. This does not
navigate away from the user's active Work or change its unsent draft. If the
original has been removed, has changed or belongs to another page, the
existing native source endpoint refuses it; no private conversation transcript
is automatically copied into a different Work. Already saved uploads and
references remain readable; they simply lack the optional new origin fields
unless a new explicit reference is created.

The original material contents remain outside the message footer. That footer
continues to identify only the recipient's selected id, path, digest, name
and byte length. It does not become a hidden instruction or an authorization
to perform physical actions. The same single World, Kernel, SQLite and
explicit approval scopes remain authoritative.

Acceptance uses `tests/per_pulse/test_materials.py` for native upload,
cross-Work reuse, third-Work propagation, source-scope refusal, message
privacy, Home return, changed-file rejection and restart; and the existing
`tests/browser/materials_flow.mjs` for the rendered source-opening
interaction. Disconnected execution is the test condition, not evidence of
real-provider learning or of the model actually reading a file.
