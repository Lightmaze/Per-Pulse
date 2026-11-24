# Continue from a command's output

An approved command keeps a short stdout/stderr preview in its original Kernel
action and saves up to **1 MiB of original bytes per stream** in the existing
checkpoint directory. A nonzero application exit still has readable output.
The preview remains at most **4 KiB per stream** and may shrink further to fit
the configured durable result allowance. Counts distinguish emitted bytes from
saved bytes; `complete: false` means capture stopped before the stream ended.

The model can call:

```json
{"action_id":"<output_action_id from run_command>","stream":"stderr","offset":0,"limit":4000}
```

with `read_command_output`. Use its `next_offset` for the following passage;
`null` means the saved text has ended. Offsets count Unicode characters in the
existing redacted text view, with invalid UTF-8 replaced for display. The raw
download retains the captured bytes exactly. A partial capture remains partial
at the final passage. The complete result envelope, including escaped text and
source metadata, fits the Kernel's configured payload allowance.

In the original Home or Work, **Command output** lists up to eight recent
commands with their label, time and exit code. Open a stream, read subsequent
passages or download its saved bytes. The list scans bounded action metadata;
polling does not reopen output files. An older action remains addressable by
its ID after a newer command, restart, command configuration removal or a
committed subject succession in the same Work.

The original action receipt owns the file references and digests. Reads verify
both; a missing or changed capture has an explanation. A capture-storage failure
preserves the command's observed exit and preview. Existing uncertain effects
continue through command recovery and are never automatically repeated. The
World supplies only the same context's committed predecessor sessions to model
reads, excluding team history references. A later native Pulse activation uses
the same tool and action history to continue working from the observed output.

This follows the preview-plus-retained-output pattern documented in
[Anthropic's Managed Agents tools](https://platform.claude.com/docs/en/managed-agents/tools#available-tools)
(checked 2026-11-05): large results are retained outside the model's immediate
context and read when needed. Here the existing Kernel action is the source
reference, so no separate task or session store is introduced.

## Verification

`tests/per_pulse/test_command_output.py` uses the native World, Kernel,
SQLite, approvals and real subprocess pipes. Docker control and HTTP model
choices are explicit synthetic fixtures. It covers long dual streams,
Unicode pagination, raw bytes, capture completeness, small payload allowances,
foreign Work rejection, missing/changed captures, restart, read-only access,
same-Work succession and an original Pulse continuation.

The real-container long-output case lives in the existing
`test_command_docker_sigkill.py` and runs in the existing Docker CI job when
`PER_PULSE_DOCKER_IMAGE` is set. `tests/browser/command_output_flow.mjs` exercises
the actual browser path with the declared local fixtures.
