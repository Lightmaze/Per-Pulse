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
permission, budget, participant, identity, API endpoint, model invocation,
or automatic approval is introduced. Direct `consult` remains unchanged.

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
