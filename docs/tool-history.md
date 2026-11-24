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
Tool-result message identities follow the configured persisted payload bound,
including longer original provider call IDs. Discussion identities retain their
256-character limit.

Omitting `role` reads saved user messages and final assistant discussion as
before. `role: "user"` and `role: "assistant"` narrow that discussion. Assistant
tool-call envelopes remain outside these readers. A search excludes the current turn to prevent recursive discovery of recall
receipts. A direct `read_history` with `role: "tool"` and the exact saved
`message_id` may read a terminal, delivered tool result from the current
turn. Current-turn user/assistant discussion remains excluded; selected-history
replay retains its discussion-only source selection.

When a *current* tool result by itself is too large for the model request, the
Harness projects only that result's text into a small JSON reference with its
saved `message_id`, real tool status, and instructions for paging it with
`read_history`. Once a result belongs to an earlier completed exchange, a
large saved result can also be paged in the request projection instead of
forcing the surrounding user correction and finished reply out of the model's
context. The current exchange keeps its ordinary full result when it fits.
Each original tool-call/result pair stays together, and the full saved result
remains unchanged. The model can follow the reference with `read_history`;
neither projection reruns the tool. An unaddressable result is never silently
replaced.

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
