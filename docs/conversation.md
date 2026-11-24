# Reading and returning to conversations

The server stores pages, messages, and references in the existing Pulse persistence layer. Browser storage holds only local drafts and navigation positions, scoped to the persistent workspace and page. When no model is configured, the interface says **AI disconnected** and saves messages as queued input. It never substitutes a scripted answer for a model response.

Message formatting uses CommonMark with GitHub-style tables and task lists.
Raw HTML remains text, and image references open only when selected; reading a
message does not automatically fetch a remote image. Code stays literal, even
when it contains the name of a linked page. A reference that appears only in
code or across separate formatting spans keeps a separate page link below the
message. **Copy** preserves the original message text, including Markdown;
**Copy code** preserves the displayed code's spacing and line breaks.

The existing `replay` tool lets the model reconsider one to four saved messages
within its current turn. After its successful native entry, only the selected
history reader remains available for that turn; finishing restores ordinary
tools for the next turn. The interface shows this activity and provides verified
links to the original input or reply. The raw message keeps its full provenance
prefix when copied. Saved examples and lessons retain the replay origin even
when the selected passage omits that prefix; shared experience exposes its
tentative nature without copying the private source material. Bringing a
selected interpretation Home also preserves its nature in the actual input.
By default this bounded regime retains the original conversation. Optional
`replay.enter(message_ids, focus)` declares a question of up to 1,000 characters.
After its successful native entry, subsequent requests retain host system
instructions, that declared focus, selected source references and replay
exchanges. Other conversation, pre-entry reasoning/preambles and experience
advice are omitted; auxiliary expert and delivery computations pause for the
rest of that turn. Its final interpretation contains only text produced after
entry; earlier preambles remain in the native transcript. Normal context and
advice return next turn. The interface identifies this focused input in
the activity and source details. It does not erase earlier provider exposure,
create a separate identity, grant extra rounds, or establish sleep scheduling,
new external evidence or model learning.
