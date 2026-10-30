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
