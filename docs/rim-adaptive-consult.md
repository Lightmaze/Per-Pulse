# Outcome-conditioned native collaboration (RIM first slice)

This is an **opt-in** execution preference in PerPulse's existing native `team`
tool, not a second agent, persistent runtime, or RIM platform. It adapts the
collaborator-selection mechanism from upstream
[Lightmaze/P-Pulse RIM draft PR #44](https://github.com/Lightmaze/P-Pulse/pull/44),
pinned at `0c696b015c9a94961ffddc6ae87c3bd9e56b6506`.
That upstream 45-file draft is **not merged here**. This implementation has
no dependency on its `pulse_rim` package and does not import its other modules.

Set `P_PULSE_RIM_POLICY=1` before starting a normal configured PerPulse
workspace. Off is the default. Invalid flag values fail configuration.
When an owned Work has two or more active known collaborators, the model can
ask for a single native consultation using:

```json
{
  "op": "consult_adaptive",
  "candidates": {"existing-collaborator-a": 0.6, "existing-collaborator-b": 0.4},
  "instruction": "Investigate the actual user work."
}
```

An adaptive consultation may also include `contributions` containing 1–8
exact `record_id`/`excerpt` pairs from completed, owner-accessible collaborator
results, including another Work. The existing native source reader captures
those selected quotes before saving the request and re-verifies the original
result and scope before first TUNNEL dispatch. Only the sampled collaborator
receives the selected historical excerpts, not source transcripts, other
contributors' histories, or private feedback. The delivered contribution
retains this exact source selection and remains inspectable after restart.
The quotes **do not** themselves change RIM candidate probabilities; only
settled, operator-rated outcomes from the calling Work may do that. An
explicit single-recipient `consult` still avoids probabilistic sharing.

Candidate weights are the model's relative starting preferences, **not**
provider-token probabilities or a learned optimal policy. The host reads at
most 128 existing caller delegations, accepts only completed contributions
with explicitly recorded `adopted`, `revised` or `discarded` outcomes,
and verifies each outcome's settled native causal event, matching result
parent, same owner and **same original Work**, target and task. It ignores
invalid sources. Repeated identical candidate/task/contract combinations
contribute at most one outcome.

The current instruction and historical task use the host's existing
deterministic 256-dimensional lexical features. The local estimate uses a
relevance threshold, a seven-day half-life, and shrinkage toward zero.
Its probability adjustment is mixed with the reference and constrained to
KL <= 0.15. With no applicable evidence, probabilities equal the reference
exactly. It is an *operator-adoption proxy*, **not** an independently proven
causal quality or learning measure.

Exactly one selection, its draw, probabilities and native source event IDs are
stored in the original team's durable request **before** a TUNNEL dispatch.
The existing Kernel action and original source turn must both succeed.
The selected collaborator must still be active at dispatch. Restart or
recovery reuses the saved target, never draws a second one. No new tool
permission, budget, participant, identity, model invocation, or automatic
approval is introduced. Direct `consult` remains unchanged.

## Work-visible choice and original sources

The Work's quiet **How this Work chose collaborators** disclosure reads
`GET /api/pages/{work_id}/team/choices` only when opened. This is an
authenticated-local-browser, **read-only** projection through the original
`NativeTeam` and the existing SQLite/Kernel/TUNNEL records, not another
decision engine. It shows the originally selected collaborator, reference
weight versus persisted selection weight, eligible rated source results,
and the original completed result. Links are offered only for native events
that pass this **same Work**'s original message/source check. Missing or
foreign source links remain unavailable, never guessed. The endpoint returns
an empty list for Home or an unrelated Work and rejects unknown pages.

The projection rechecks delegation ownership, Work, matched request, delivery,
and saved preference identity before display. It excludes raw correction
transcripts and private feedback text, does not recalculate the old choice
from later feedback, and never schedules a new collaborator, model turn or
physical action. The read is bounded to 128 recent native requests and
16 decisions; absence is **not** evidence that RIM was never active.
This offers human source inspection of a decision, **not** proof that choosing
that collaborator improved a delivered work artifact.

## Known limits and next acceptance

The feature does not silently form habits when the model does not select
`consult_adaptive`, and it does not teach the provider's weights. A completed
contribution without explicit operator outcome is not a reward. Outcomes are
currently immutable and the current Work is the evidence scope; shared,
cross-Work preferences, withdrawal and broader cognitive action choices
require a separate authorized interface design.

First verify the exact head: configuration/schema/validation, candidate
choice bounds, same-Work source privacy, native prepared-request persistence,
successful source-turn admission, actual TUNNEL delivery, restart without
resampling, and refusal of unknown candidates or cancelled turns. Unit
fixtures are not evidence of real-provider behavior. Then test one ordinary
Work A to later Work selection with explicit operator outcomes, plus a
different-Work negative control, and retain the actual delivered artifact and
source. Genuine provider behavior/quality and costs remain blocked by
[PerPulse #51](https://github.com/Lightmaze/Per-Pulse/issues/51).

Relationship: [PerPulse RIM integration #72](https://github.com/Lightmaze/Per-Pulse/issues/72).
Keep this downstream code distinct from upstream PR #44 until separately
reconciled and accepted.
