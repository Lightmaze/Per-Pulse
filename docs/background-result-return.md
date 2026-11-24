# Reopen the result of a background continuation

A spontaneous Pulse input can have no message content while its native turn
still produces a normal assistant answer. Previously the Home continuation
list required the input itself to be a displayable message, so that answer
was omitted. Activity could open the Work but could not select its result.

The Pulse projection now supplies an optional `resultMessageId` separately
from `eventId` and `messageId`. It follows the exact input's latest native
Harness turn, requires an accepted settled turn in the same context, verifies
the answer's parent, World, subject, center, causal chain, settled state and
ordinary origin, and checks the complete committed page lineage. Missing or
inconsistent evidence yields no result link. It never substitutes the Work's
latest foreground answer or invents content for an empty activation.

Home and Activity use that verified answer when the activation has no
displayable message of its own. Existing input and original source links keep
their meanings and destinations. Older servers
without the new field retain their existing navigation behavior. The read-only
Pulse observer records this distinct answer identity and can count it as a
navigable Work result; it still makes no claim about model usefulness, complete
history coverage or provider cost.

Regression checks use real World/Kernel admission with explicitly synthetic
transport replies. They cover an actual contentless spontaneous input, a later
foreground answer, no model request while opening the old answer, mismatched
native records, read-only observation and disconnected reopen. The frontend
check preserves the original causal source and visited-activation behavior.


## Browse older completed results from Home

The live Home preview only contains the most recent twenty Pulse activations.
Choose **Browse saved results** to read earlier finished Work answers from the
original native World ledger, in pages of twenty. **Load earlier results**
continues from the ledger sequence cursor. Reading never starts a model turn
or repeats an action. This works after process restart, with Pulse paused,
or when the model is disconnected.

Every offered result is checked against the exact completed native Kernel
turn and the original Work's durable message lineage. Unverified, failed,
foreign-World or unopenable outputs are not returned. Source links are shown
only when their own originating message can be opened. The Home client
additionally compares the current World, service instance and fresh Work list
before exposing a navigation link.

`GET /api/pulse/results?limit=20&beforeSeq=<sequence>` returns a
`nextBeforeSeq` cursor for further inspection. Sparse batches are possible
when a completed activation has no source-verified displayable answer.
Native tests cover the original result identity, cursor, offline reopen,
tampered causal facts and absence of extra model requests.
