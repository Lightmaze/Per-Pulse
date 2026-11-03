# Carry a stopped workspace to another directory (POSIX)

Per Pulse uses the **existing native cold Pulse Image** format, not a second
history store or a live agent clone. After cleanly stopping the server, save
a private archive outside the source directory:

```sh
per-pulse image save ./per-pulse-private.zip --data-dir ~/.local/share/per-pulse
per-pulse image inspect ./per-pulse-private.zip
per-pulse image restore ./per-pulse-private.zip --data-dir ./restored-per-pulse
per-pulse --data-dir ./restored-per-pulse
```

Restore requires a **new, nonexistent directory** and an archive made with
a compatible **Per Pulse** implementation; an upstream P-Pulse image alone
is not assumed to be interchangeable. Save refuses an active World or
unfinished Kernel turns/actions. Restore checks member digests and the
matching Python implementation before writing, preserves the native
World/Kernel identities and source links, and does not start any model.
Historical World turns marked uncertain after process recovery or a native
turn timeout block export by default. To deliberately keep that history in a
private copy, after recovering the original workspace and stopping any pending
turn, use:

```sh
per-pulse image save ./per-pulse-private.zip --data-dir ~/.local/share/per-pulse --include-recovered-history
```

This option requires a released World lease, finished Kernel turns/actions
and operations, and an exact matching Kernel turn for each recovered World
record. A retained native timeout must have accepted input, the original
settled uncertain event and its exact cancelled Kernel turn. A completed read
or file write can travel with that history; a write with an unknown outcome
still blocks export. The option cannot carry an uncertain file or other tool
effect. The archive and restore receipt list `retained_uncertain_turns`; those original messages
and records stay uncertain in both copies. Restore verifies that list against
the native history, and never retries an old request or action. A completed
Stop alone does not establish that every effect can be safely copied.
Native file checkpoints, including retained pre-write bytes, are carried with
their original workspace provenance. Moving those records does not authorize
an old write or checkpoint restore against the new workspace root.
If a restore fails, do not start its incomplete destination. The archive
contains **private conversations, histories and workspace files**: do not
publish or share it. Model credentials and external tool/resource bindings
must be configured deliberately after restore.

To continue a partly read source after moving the workspace, the native model
can use `reading.recent` to find its earlier passage, then explicitly call
`reading.resume(receipt_id)`. Resume checks the same relative path, complete
file bytes and redacted text view before reading from the saved position.
The new receipt retains its original receipt and workspace provenance; later
`reading.continue` calls use the new receipt. A changed file must be opened
deliberately from the beginning. This does not carry approvals, cross logical
session boundaries, or establish that the material was understood.

This is a **cold copy**: it does not transfer a running process, merge later
changes made to the original home, or copy browser-local unsent drafts and
scroll positions. The old and restored homes can diverge; treat the
restored copy as the continuation you choose to use, not a synchronized
second subject. Image save/restore currently support POSIX hosts only.
Compatibility is bound to current Python source code, not a guarantee of
semantic equivalence across other model/provider versions.
