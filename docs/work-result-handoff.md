# Exact selected-result handoff between existing Works

This is a user-initiated handoff in the **same World / Kernel / SQLite**. It is
not an automatic Pulse delivery, a second agent, or a claim that the model
learned from another task.

## What someone can actually do

In an existing Work containing a **settled assistant result**, select a literal
passage (or use the entire result), choose **Use in another Work**, pick an
existing destination Work and describe what that Work should do with it.
Only the selected exact text and the new request become a normal user event in
the receiving Work. If a highlighted Markdown passage does not match a
contiguous portion of the stored original text, the dialog starts **empty**:
the UI must not silently replace the requested passage with the entire reply.
The user can copy a literal passage from the raw source preview or explicitly
choose **Use entire source result**. The dialog and native API both reject an
edited or fabricated quote. A click with **no** highlighted text still proposes
the full result, visibly in the editable dialog, before confirmation. The original message, its Work and the receiving Work's
own history are not copied.

The receiving message has a **native-origin link** to the exact original
message. Both Works' unsent browser drafts persist, and the normal Back/source
navigation can return to the previous position. Reopening the same World after
a normal restart preserves the source anchor. The action requires explicit user
input and does not approve any Kernel tool or file mutation.

## Source and permission contract

`POST /api/pages/{source_work_id}/transfer` accepts
`targetPageId`, `messageId`, `excerpt` and `instruction`.

- Source and destination must be two distinct existing Works in the same
  native World; neither can be Home.
- The original source must be a **completed assistant message**, verified
  against the full causal ledger and source context, not only the recent
  500-message projection. The excerpt must be literally contained in that
  original message. Edited or fabricated quoted text is rejected, rather
  than receiving a false exact-source claim.
- The message is injected through the existing
  `RuntimeService.send_task_front_message`, and its source-page/message
  anchor plus `transfer` reference are persisted with the current page
  component state. No private conversation, source material attachment or
  original assistant role is copied into the destination.
- The receiving native Kernel is responsible for further model calls and any
  existing Harness approvals, budgets and persistence. The selected text is
  labeled reference material, not authoritative instructions.
- This is a **user-approved quote handoff**. It does not imply inferred
  relevance, automatic learning, semantic evidence or provider outcome gains.

A target's normal model processing can begin after an authorized handoff. If
the model is unconfigured, the native message remains queued, exactly like
ordinary user messages. No credentials, external model requests or effects are
required to exercise the disconnected API path.

## Verification and remaining checks

`tests/per_pulse/test_work_result_transfer.py` exercises actual native
World/Kernel/SQLite causality with an explicit synthetic HTTP provider,
separate Work histories, same-message provenance, restart, forged excerpt,
foreign source, uncompleted source and invalid destinations. The
`learning_artifact_flow.mjs` installed-wheel Chromium journey exercises
selecting a settled source reply, explicitly choosing another Work, issuing a
new real user event, opening the exact source and preserving both drafts. These
tests do not prove real provider learning or usefulness.

This feature deliberately does not transfer files, all of Work A's history,
automatically select a relevant result, or change the existing Pulse
propagation rules. The separate user journey Home → Work → correction →
later Work's natural choices and finished artifact remains the real-provider
quality gate in [Issue #51](https://github.com/Lightmaze/Per-Pulse/issues/51).
