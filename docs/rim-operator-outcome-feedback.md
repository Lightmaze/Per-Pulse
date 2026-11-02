# Operator-rated collaborator outcomes in the original Work

This is a PerPulse G3 delivery path, not a new learning engine. With the
existing native RIM policy enabled, a completed `team.consult_adaptive`
persists its selected collaborator, original Work, source evidence, weights,
and a durable TUNNEL result. In that Work's “How this Work chose
collaborators” section, the operator can open the actual result and explicitly
record **used**, **reworked**, or **not used**.

The original selection is historical and never resampled. Rating uses
the existing `RuntimeService.record_delegation_outcome`:
`adopted` / `revised` / `discarded`. The native immutable causal outcome
is recorded once; subsequent adaptive decisions may consult eligible rated
source outcomes. Ratings are not completion status, generated model
confidence, or evidence of a better model. This interface does not rate
nonadaptive or incomplete delegation requests, or another Work's result.

The explicit operator confirmation precedes the write. Re-submitting the
same rating is idempotent; changing it is a 409 conflict. The Work-scoped
endpoint verifies the original Work, persisted adaptive choice, native
durable result, and source message visibility before calling the existing
native outcome API. Home, unrelated Works, unverified records, and
unfinished results cannot authorize learning signals. Original source text,
private transcripts, and credentials are not copied.

API: `POST /api/pages/{work_id}/team/choices/{record_id}/outcome`,
JSON `{"outcome":"adopted"}` (or `revised` / `discarded`).
The GET projection includes immutable `outcome` for bilingual display.

When a later Work uses an eligible original rating, its historical RIM choice
already retains the exact source Work, source result event and the native
operator adoption proxy (1 = used, 0 = reworked, -1 = not used). The Work's
existing explanation now labels each verified source result and identifies
the original Work by its existing title, with a link to the original message.
If the source cannot be displayed, the link remains unavailable; if a saved
proxy is unrecognized, no rating is inferred. These labels inspect the
**selection-time snapshot**, not a fresh RIM choice or a live evaluation.
No model call, sharing consent, approval, stored weights or outcome is changed.
The rating is a human utilization proxy, never a measured quality improvement.

Native regression uses a real World, Kernel, and SQLite with explicitly
scripted HTTP transport. It checks wrong Work, Home, invalid outcome,
second-rating conflict, idempotent replay, same original result, and
subsequent RIM selection weight change. This is not a real-provider model
learning experiment; Issue #51 still requires authorized model calls and
independently blind-scored actual Work artifacts. Issue #80 thread-free
approval parking is not modified.

Review the exact-head verify, Chromium, and real Docker recovery on one
combined tree, then require independent main CI after merging. Success means
a human can finish a collaborator task and record an outcome that affects
a later choice—not merely inspect another statistics panel.
