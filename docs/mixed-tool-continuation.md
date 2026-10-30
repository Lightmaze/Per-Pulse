# Continue mixed tool calls after an approval

A native Work proposed an approved report write and an internal attention hold
in the same model response. The file was written successfully, but the later
call remained an unexecuted `intent`. Approval continuation treated that intent
as a tool result, and the store refused to deliver a non-terminal action. The
page retained a completed file effect with `invalid_transition` and no pending
approval. The original file was preserved; it was not replayed.

`TurnRunner.resume` now admits fresh `INTENT` actions through the existing
before-tool hook and guarded start path. A resumed `STARTING` action also runs
that hook. A `RUNNING` action still requires recovery, completed actions are
only observed, and every later mutable action keeps its own explicit approval.
No new scheduler, action authority, replay permission or store is introduced.

The native regression requests file write, source reading, attention hold,
second file write and another reading in one response. It checks that:

- Allowing or denying the first file continues the independent read and hold,
  then stops at the second file's own approval.
- Repeated decisions do not execute tools, hooks or model calls again; only
  terminal tool results reach the next model request in the original order.
- Attention remains staged while the second approval is pending and is admitted
  only after the exact World and Kernel turn settle.
- Stopping at that second approval preserves the first file, does not write the
  second file, and discards the staged attention.
- A before-tool hook can reject a later unapproved read without running that
  read or subsequent actions, while preserving the already approved file.

The first three cases failed on the prior implementation with the same native
continuation error observed during practice. Model replies are explicit
TEST_DOUBLEs; approvals, files, action history and World publication are real.
This change does not repair or replay previously interrupted turns. A new
request must inspect their actual effects and choose any further work explicitly.
