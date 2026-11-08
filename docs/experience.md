# Keep examples and learn from corrections

`per-pulse` keeps examples and lessons by default; start it with
`--no-posture` to turn this off. The example workspace never keeps experience.

Use **Save example** on a completed reply to retain an exact passage, an optional
note, and optionally a later message from that page. Selecting text first fills
the passage editor when that selection matches the original text. An example
can stand on its own without a rule or evaluation. You can save one from a
previous reply even while the model is disconnected. The source passage stays
intact when you later edit its note.

**Save example** and `posture.remember_example` can also retain completed
background replies. When there was no new input text, the source records the
internal activation and its original identity. User feedback is attached only
when an actual follow-up is selected. Saved examples are available when later
foreground or background work chooses its approach, including after restart.

The model can refine its own example's title or note with
`posture.revise(experience_id, expected_revision, changes)`, including during a
quiet step. **Source experience** identifies Pulse's refinement and opens the
conversation where it happened. The original passage and source stay intact.
Revising a lesson or recording outcome feedback still uses a later user message
through `feedback_message_id`.

After reading saved command output, the model can use
`posture.remember_command(receipt_id, title, excerpt, note?)` to keep an exact
passage. Use the `receipt_id` returned by `read_command_output`.
The example retains the original command's exit code,
output range and capture completeness; selecting it never runs the command
again. The selected passage and note can inform later work, while the full
command and surrounding output stay in their source page.

When that experience began with an attached file, **Source experience** shows
the readable message and an **Open source message** link to its original file
references. The stored encounter retains its exact input. Shared experiences
keep that private input and its file references in the source page.
Later feedback used by a native refinement also links back to its exact
message and attached files. Its original evidence remains unchanged.

With a model connected, explain what worked or what should change after a reply.
The model may save a conditional lesson with its original result and your
correction. **Settings → View this page’s experience** lets you inspect that
source, refine the guidance and its exceptions, pause it, or keep it to this
page. Saved experience is available in your other pages by default. The model
can also retain a concrete example.
Model capture is optional: a correction does not guarantee that anything is saved.

In a later Work, **Give feedback** records your observation of a shared lesson or example.
You can optionally link a completed reply from that Work; the source page's
feedback history then opens the exact result, including older messages outside
the recent window. A described observation can also stand without a linked
reply. **No clear benefit** records a neutral observation and keeps the experience's
current active state. Harmful feedback pauses it; only its source page can
edit or reactivate it. A lesson's abstraction or an example's selected passage
and note can travel, while the surrounding conversation and private feedback
stay at the source.

Linking a result establishes where to inspect it, not that the experience caused
it. Saving an example does not certify its success or future applicability.
Real-provider learning benefits still need output comparison and review.
Example mode remains model-free and does not enable experience learning.

### Reading large experience details

The native `posture.get(experience_id)` tool keeps small records in its normal
structured response. For a record above the response budget, `detail` carries
an `experience_json_fragment` and `detail.next` contains the next get arguments.
Concatenate these fragments to read the original eligible record, including
all its conditions, exceptions and source fields. Every response stays within
16,000 UTF-8 bytes.

Offsets count Unicode characters. Continuation binds the record's digest;
if it changes, start get again without the old digest. Each page checks the
same active-record and context visibility rules as the first get.
