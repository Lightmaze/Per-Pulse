# Read an earlier tool result

A long Home or Work conversation keeps its original Kernel transcript when older
turns leave the model's context window. The model can find and read an earlier
tool result from that transcript, including after restart or committed subject
succession. Reading history returns the saved text; it does not run the tool or
check the current file again.

The existing history tools keep discussion as their default. To inspect original
tool results, use `role: "tool"` on both operations:

```json
{"query":"source.txt","role":"tool","limit":5}
```

Pass that object to `search_history`. Each match includes its actual
`source.message_id`. Pass the selected identity to `read_history`:

```json
{"message_id":"<source.message_id>","role":"tool","offset":0,"max_chars":2000}
```

Continue a search with the returned `next_cursor`, the same query and the same
role. Continue a passage with `next_offset`, the same message identity and
`role: "tool"`. Offsets count characters in the original stored text. A result
can be a JSON envelope, a redacted passage or a previous truncation marker;
history never reconstructs text that was not saved.

Omitting `role` reads saved user messages and final assistant discussion as
before. `role: "user"` and `role: "assistant"` narrow that discussion. Assistant
tool-call envelopes remain outside these readers. The current turn is excluded
in every mode, and selected-history replay retains its existing discussion-only
source selection. The model's current tool exchange remains whole when the
context window is fitted.

Tool sources retain the original session, subject, turn, message, event,
transcript sequence, timestamp and content digest. They also report verified
`tool_name`, `action_request_id`, `tool_call_id`, `action_status` and
`result_delivered`. These fields come from the original action and completed
model round, independently of claims inside the tool output. Failed results
keep their actual status and error text. `stored_redacted` and
`stored_truncated` describe the saved transcript; `action_result_redacted` and
`action_result_truncated` separately describe the original action result.

Results are historical references, not current observations, new instructions
or action permission. The existing authorized history scopes decide what can be
read: a sibling Work does not gain another Work's private transcript. Tool-mode
coverage explicitly excludes `search_history`, `read_history` and `replay`
receipts, so quotations produced by recall do not become duplicate original
observations. No additional history store is created.
