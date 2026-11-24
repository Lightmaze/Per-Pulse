# Local notices

In Home or a Work, ask Per Pulse to keep a small intention for later. Its native
`notice` tool is available by default, including when background Pulse is off.

- `record(content)` saves up to 2048 UTF-8 bytes as a pending notice.
- `list(status?, limit?, cursor?)` browses pending, closed, or all notices in
  the current context. Follow `next_cursor` for another page.
- `read(id, relative_message?, offset?, max_chars?)` reads the notice and its
  actual registration tool-call transcript. `relative_message` selects that
  message (`0`), its preceding public message (`-1`), or its following public
  message (`1`) in the original turn. Follow `next_offset` for remaining text.
- `close(id, expected_revision)` closes a pending notice using its current
  revision. Closed notices and their sources remain readable.

Pending notices appear again as attributed memory data in ordinary model
rounds, after large tool results and after older conversation leaves the
context window. The projection reserves at most 1024 estimated tokens and at
most half the configured context window. Long notes appear as explicit excerpts;
use `list` and `read` for the rest. The newest pending notice appears first;
remaining space includes the oldest pending notices without duplicates.
`total_pending` and `more_available` report coverage. List pagination stays in
stable creation order. The original conversation is retained.
Selected-history replay pauses this auxiliary memory for that turn.

Each notice keeps the original World, context, turn, action, registration
message identity, and timestamp. Its triggering user/event reference is separate
from the position at which the model registered it. Reads cover public messages
in the original registration turn; missing passages, redaction and previous
truncation are explicit. Private model reasoning and host instructions are
excluded from the neighborhood.

A successful registration or close is an immediate durable memory change.
It survives restart and remains recorded if its source turn later fails or is
cancelled. Source turn/action outcomes and receipt delivery are reported
separately. Retrying the same action does not duplicate or reapply its effect.
Notices stay within their Home or Work and the subject's committed lineage;
recording one does not dispatch work or adjust Pulse motivation.
