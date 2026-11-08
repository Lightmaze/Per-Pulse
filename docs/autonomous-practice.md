# Autonomous practice

Connect a model, open **Activity**, choose a weekly request limit, and select
**Start autonomous practice**. A new workspace can begin without a first chat
message. Pulse receives an open-ended orientation, chooses a concern, and can
continue in an existing or new Work using the normal workspace tools.

Work can read public HTTPS HTML and plain-text pages with `read_web(url)`.
The observation keeps its URL, read time and extracted text in the original
Work history. **Web sources** opens those URLs; a source's live webpage may
have changed since it was read. History recall reads the saved observation.
Images, interactive content and page layout require opening the original page.
Each read observes up to 512 KiB of response bytes and returns up to 24,576
text characters, marking partial text explicitly. Long observations provide
`next` arguments for another `read_web` call: the requested URL, Unicode-character
offset, page size and observation digest. Each call fetches a fresh observation
and verifies that its extracted text and resolved URL still match before
returning the next range. A changed source requires starting again at offset 0.
`available_eof` identifies the end of observed text; `network_truncated` keeps
any original content beyond the network byte bound explicitly unknown.
Saved ranges remain in the original Work history without a fresh fetch.
The reader does not sign in, fetch local/private
addresses or write files. Ordinary workspace actions remain responsible for
saving any resulting work.

The first departure is recorded as SELF activity in the existing World.
Model-generated Work instructions are workspace activity too. Results retain
their original messages, file actions and links back to the producing Work.
Home observes new results without changing your route or unsent draft.

Each physical autonomous model request consumes one weekly slot. Retries,
delegated Work, experience computations and response reviews use their
originating allowance. A failed or uncertain dispatched request remains
counted. A person's messages and explicitly requested `/tick` steps use their
own pool. Weeks start on Monday at 00:00 UTC.

**Pause autonomous practice** stops new autonomous sends. Queued events,
consumed allowance and completed work remain saved. Requests already sent can
finish. Starting again retains the first departure's identity; it does not
repeat its original task. The native Pulse scheduler can continue retained
work within the available allowance and any existing background/page holds.

When the allowance runs out, unstarted events wait in their original queue.
Raise the limit or wait for the next week to continue. Restarting Per Pulse
retains the policy, usage, history, artifacts and pending events. Reopening
without a connected model keeps autonomous work waiting.

If the allowance ends between model calls, confirmed file actions stay saved.
A continuation waits in the same Work and uses its existing history; prior
actions are not executed again. Work with uncertain tool effects retains its
explicit recovery controls.

The CLI enables the native background profile by default. `--no-pulse` keeps
that profile off; start with `--pulse` to use autonomous practice. Ordinary
file edits and commands use the same workspace permissions, approvals and
Undo as other work.
