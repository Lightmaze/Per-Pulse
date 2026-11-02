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

## Explicit original-Work sharing and withdrawal

A completed adaptive collaboration result can receive an **operator-authored, revisable annotation** distinct from the immutable used/reworked/not-used outcome. Open the original Work's saved decision and its finished result; expand **Guidance from this result**. It is Work-local unless the person explicitly selects **This collaborator across my Works**, reviews a second confirmation and saves. Changing the scope or note creates a versioned revision. **Withdraw guidance** prevents new usage in future choices; it never rewrites older decisions or erases already-seen model messages.

The product uses the existing native World/Kernel/SQLite `CollaboratorFeedback` journal, no new reward engine or session identity. The local single-user API is:

- `GET /api/pages/{workId}/team/choices/{recordId}/feedback` returns the source-scoped native revision, current annotation and retained history, or 404 when the original completed contribution is not in that Work.
- `PUT /api/pages/{workId}/team/choices/{recordId}/feedback` accepts the strict JSON object `{"expectedRevision":0,"content":"...","scope":"work","withdrawn":false}`. `scope` can be `work` or `collaborator`; an active note needs nonblank content (up to 2048 characters). Withdrawal reuses the exact saved content/scope and sets `withdrawn:true`. Native atomic CAS/lease/source checks own authority. A conflicting or uncertain receipt requires re-reading server state, not blind resubmission.
- Home, foreign Work, missing, incomplete, or mismatched original results cannot create authorization. Saved results are source-bound to the actual original collaborator and owner; the other Work does not receive the original conversation transcript.

This **does not make an assessment itself a cross-Work grant**. Native RIM's cross-Work selector requires both a verified previously rated outcome and a separate active `collaborator`-scope annotation for that exact contribution. Model-produced `propose_feedback` is still merely a proposal. No provider calls, real model quality improvement, monetary budget or new approval is implied by this interface.

The real-World regression `tests/per_pulse/test_rim_native_integration.py` exercises local-only vs explicit cross-Work scope, next-choice effect, revision and withdrawal over synthetic HTTP transport. The HTTP contract is separately checked by `tests/per_pulse/test_collaborator_feedback_api.py`. The UI includes English and Simplified Chinese. Current Work draft navigation, source message anchors and future candidate sampling remain controlled by existing contracts.
