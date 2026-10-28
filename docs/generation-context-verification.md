# Native generation context boundary

The native Kernel gives Home and each Work their own transcript within one
continuing subject. A generation transition must preserve that distinction.
This change fixes two paths found while testing a live transition with several
independent Work contexts:

- The internal summary call omitted its execution context. Its causal event
  inherited the triggering Work's center, so Kernel ran in that Work, while
  the manager assembled input from the subject-wide message cursor. Other
  Work messages could enter the summary request.
- The successor's first input in each context imported the subject-wide
  summary seed. A summary of one Work could therefore enter another Work.

The Runtime now supplies its existing native center-to-context mapping to the
manager. Every native durable admission, including an internal summary, derives
its context from the original event before claiming the turn. A conflicting
explicit context fails before any model request or Kernel session is created.
Non-native adapters keep their existing input and bootstrap behavior.

Native successors retain a summary seed only when the committed generation,
settled summary turn, source/result events, exact content and durable execution
context all prove that it belongs to the receiving context. This preserves the
summarized conversation's first-turn continuity without copying its summary
into other Work. Old unscoped summary records provide no such proof and are not
automatically imported. Original transcripts remain in the original Kernel sessions. The existing
`search_history` and `read_history` tools can retrieve them through verified
committed ancestry in the same context. This neither copies a transcript nor
creates a second history store. It does not automatically summarize every
other Work or claim that a real model will always choose to recall history.

## Regression check

```sh
python -m pytest tests/per_pulse/test_generation_context.py -q
```

The six journey combinations trigger a real native summary from Home or Work,
then choose Home, the original Work, or a sibling as the first successor input.
All three contexts continue and then restart. The summarized context receives
its own verified seed; the others do not. The synthetic HTTP provider
actually invokes the existing history tools; it checks the original predecessor
message and source identity, while every captured request excludes foreign
conversation markers. Another case rejects a conflicting internal context
without claiming its queued event, then admits it using the durable mapping.
A compatibility fixture also confirms that an older unscoped summary does not
become a trusted native bootstrap merely because its result still exists.

All generation transitions, World/Kernel persistence and tool reads are real.
Provider decisions and responses are explicitly synthetic. The test establishes
input isolation and addressable original history, not model quality or automatic
context management for an arbitrarily long conversation.
