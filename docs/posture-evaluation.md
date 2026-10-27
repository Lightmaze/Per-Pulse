# Evaluating default posture

The evaluator scores delivered artifacts and attributed observations. It does
not reward an assistant for saying “UX,” promising a review, or reporting its
own success. Synthetic transports test the mechanism and are excluded from
real-provider comparisons automatically.

The registered cases cover a first-use practice experience, a registration
utility, a patient-level scientific comparison, a customer update, exact SVG
geometry, and a small bug fix. The registration, SVG, and bug-fix cases are
negative controls: preserving an adequate simple approach is the desired
behavior. The scientific and writing cases exercise transfer without using UX
terminology in the task.

## Inspect the tasks

```sh
python -m per_pulse.posture_evaluation catalog
```

Each case contains an ordinary task prompt and a separate reviewer rubric.
Keep the rubric and check names out of the acting model's input. The five
conditions are `bare`, `explicit`, `notes`, `conditional`, and `withdrawn`.
Repeat and counterbalance them under the same model, provider, available tools,
and resource ceiling. The notes and conditional conditions must use the same
original attributed experience material. `experience_material_digest` binds
that input; it does not assert identical exposure in every pass. The host
records each actual provider request and its packed posture packet digest,
episode identities, and omission metadata in `provider_request_audit`. Ordinary
notes and conditional work can have different packing metadata, and conditional
work can see the material in several computations. Inspect these differences.
An optional `compute_only` condition runs the inherited generic expert
computation without posture memory. It helps diagnose an extra-computation
effect, but does not match the conditional mode's additional delivery stage.

## Run one isolated provider trial

Configure the native `P_PULSE_CHAT_ENDPOINT`, `P_PULSE_CHAT_MODEL`, and
credential environment described in the main README. Nothing is called when
the provider is unconfigured. This command is an explicit provider run:

```sh
python -m per_pulse.posture_evaluation run exact_svg \
  --condition bare --pair-id replication-1 \
  --output /tmp/per-pulse-posture/bare-svg

python -m per_pulse.posture_evaluation run exact_svg \
  --condition conditional --pair-id replication-1 \
  --experiences tests/per_pulse/fixtures/posture-experiences.json \
  --output /tmp/per-pulse-posture/conditional-svg
```

The output directory must be new. It contains the native Harness trace,
isolated World state, artifact files, `run-manifest.json`, `record.json`, and
`evaluation.json`. The host writes the configuration manifest before execution
and binds the exported record after completion. External review observations
can subsequently be appended without changing those host fields.
These are local experiment records; do not publish private workspaces or
provider transcripts. A generated `.gitignore` prevents accidental source
commits of the trial directory.

The runner exposes native `read_file` and `mutate_file` only. It approves writes
only to the case's declared deliverable paths, using the existing digest,
checkpoint, and approval mechanism. It never executes generated Python or
shell commands. Browser probes and Python tests run separately in the
appropriate test environment and supply their observations afterward.

`--max-provider-calls 8` sets a shared ceiling that includes posture side
computations. Provider-reported usage is retained separately; missing usage
stays unaccounted. A failed, blocked, or interrupted run is retained as such.
Equal ceilings do not mean identical realized computation. An advantage over
ordinary notes alone cannot separate experience effects from the extra model
passes; inspect actual usage and the optional control before making that claim.
The main response is not relabeled successful merely because an artifact file
exists. Pressing Ctrl-C records an interrupted trial where the runtime can
settle the interruption; abrupt process or machine termination may leave only
the durable native trace.

This isolated runner imports explicitly marked `constructed_fixture` teaching
examples. It does not certify an imported JSON episode as a native observed
experience. Experiments with real accumulated experience should export
attributed records from their original workspace and use the evaluator below.
The two included examples contain no benchmark answers and no private user
history.

## Evaluate actual work

```sh
python -m per_pulse.posture_evaluation evaluate /path/to/record.json \
  --artifacts /path/to/artifacts
```

The evaluator verifies each required artifact's SHA-256 and a corresponding
write receipt. Eligibility for a real-provider comparison also requires the
neighboring host manifest and read-only native SQLite trace. It reconciles the
complete action list, native scope, turn status, prompt digest, provider origin,
request count, and actual request digests. Relabeling a fixture or importing
self-written receipt JSON cannot establish this eligibility.

The SVG checker inspects the exact XML
geometry. The scientific checker verifies the declared held-out patient
population, patient macro averages, sample count, and selected model. Plausible
but incorrect geometry, scan weighting, and inclusion of training observations
fail these checks. The SVG check deliberately accepts a small transparent
subset of SVG: no CSS, definitions, transforms, extra paths, or external assets.
These are bounded correctness checks, not rendering, comprehensive design, or
scientific reviews.

The actual interaction and writing checks need a browser probe, test runner,
or human review. Add observations to `record.json` with this shape:

```json
{
  "check_id": "first_visit_has_playable_situation",
  "passed": true,
  "source": "browser_probe",
  "observer": "browser-flow-check-v1",
  "detail": "Opened the page with empty storage, chose a response, and observed the next scene.",
  "artifact_sha256": {"prototype.html": "<actual SHA-256 of the inspected file>"}
}
```

Place observations in the record's `observations` array. Use the case's check
ID and bind the file that was actually inspected. Self-reports from the acting
model are rejected. Missing observations remain `unreviewed`; contradictory
observations fail rather than allowing the last pass to win. An imported JSON
claim is not independently authenticated: the named reviewer remains
responsible for the observations. The native model has access only to the
isolated artifacts directory, so it cannot write the host manifest or evaluator
reviews through the trial tools. A person with filesystem access can replace
the manifest, record, and databases together. These are consistency checks
against trusted host files, not cryptographic attestation against their owner.

## Compare conditions

```sh
python -m per_pulse.posture_evaluation compare \
  /path/to/bare/evaluation.json /path/to/explicit/evaluation.json \
  /path/to/notes/evaluation.json /path/to/conditional/evaluation.json \
  /path/to/withdrawn/evaluation.json
```

The comparison keeps every case and negative control visible. It refuses to
produce paired deltas for fixtures, missing conditions, unreviewed trials,
different models/providers/budgets, or different notes-versus-conditional
source material. The CLI re-evaluates neighboring `record.json`, `artifacts/`,
and native traces; it never trusts a saved `eligible` flag or old passing score.
Each replication needs its own `pair_id`.

For isolated `withdrawn`, record the preceding teaching run, intervening task
IDs, and the absence of a reminder in `withdrawal_lineage`. The runner accepts
that record through `--withdrawal-lineage` and marks
`withdrawal_mode=attributed_retrieval_probe`. It reloads teaching material into
a fresh isolated environment. This probes retrieval without a current reminder;
it does not demonstrate a continuing subject retaining an observed lesson.
The attributed lineage is not independently certified by this isolated runner.

## Exercise a continuing native subject

```sh
python -m per_pulse.posture_evaluation transfer \
  --output /tmp/per-pulse-posture/continuing-trial \
  --training-calls 12 --interference-calls 4 --transfer-calls 8

python -m per_pulse.posture_evaluation audit-transfer \
  /tmp/per-pulse-posture/continuing-trial
```

`transfer` uses the existing PageService, World, and Kernel. It starts with a
fixed customer-writing task, sends fixed feedback that explicitly requests a
conditional lesson through the native `posture` tool, then sends one ordinary
interference task and one ordinary later writing task. `--interference-task`
and `--transfer-task` can replace those last two prompts. The actual prompt text
is declared in `transfer-manifest.json` before the run. No reviewer rubric or
check identifiers are supplied to the acting model.

All stages stay in Home. Experience is context-local; this command never
implicitly promotes it to other pages. Committed native generation succession
is allowed and recorded as a continuation of the same subject. Each stage
retains its actual subject, World event and turn IDs, Kernel scope and action
IDs, provider usage, and the delivered assistant response. Native page tools
do not permit file mutation in this path. The host exports the actual settled
response text to `teaching-response.md`, `correction-response.md`,
`interference-response.md`, and `transfer-response.md`, binding their bytes to
the source events rather than inventing file-write receipts.

The training ceiling is shared by the first task and correction; interference
and transfer have separate ceilings. All include posture computations. A
failed or timed-out stage is retained and later stages do not run. The native
workspace and traces remain under `native/`; `transfer.json` holds the audit
index. `audit-transfer` opens those databases read-only and checks the actual
input/result order, continuity or committed succession, native memory admission,
action receipts, request digests, provider origin, and exported response bytes.

This protocol uses **explicit teaching**. It does not show that an ordinary
correction spontaneously causes learning. The registered default later message
has no reminder. A custom `--transfer-task` remains unreviewed for reminders;
inspect its declared prompt before interpreting the trial. Earlier teaching
remains in the same context's history, so this trial alone cannot separate
memory effects from retained conversation. Its output is
always `unreviewed` and never a capability score. Review the actual later
message and compare against matched controls before claiming a behavior gain.
Unconfigured providers stop before creating a trial or making a request.

The current explicit-teaching prompt states a preference without assuming a
failure in the previous answer. Historical trials keep their registered
wording: `audit-transfer` reports either
`explicit_preference_clarification_v2` or
`explicit_directive_with_assumed_failure_v1`. Old trace evidence is not
reclassified as the newer teaching protocol.

## Observe ordinary feedback across fresh pages

```sh
python -m per_pulse.posture_evaluation ordinary-transfer \
  --output /tmp/per-pulse-posture/ordinary-trial \
  --training-calls 12 --trial-calls 8

python -m per_pulse.posture_evaluation audit-ordinary-transfer \
  /tmp/per-pulse-posture/ordinary-trial
```

This protocol keeps all work in one native World and subject while giving
each later trial its own new Work context. The first source task asks for a
customer incident update. A later ordinary message states a preference:
lead with what remains usable, then explain the disruption and next update;
provide the finished message unless an outline or plan is requested. It does
not mention the learning tool or accuse the previous answer of a defect.

The acting model chooses whether to remember an experience. Only a native
lesson attributed to that exact source result and later feedback is eligible.
If no such lesson is admitted, the trial records `capture_absent` and stops
before any transfer tests. This is a recorded observation rather than a reason
to add a host-authored lesson.

When capture succeeds, the protocol runs four new pages in a declared order:

| Stage | Experience exposure | Ordinary task |
| --- | --- | --- |
| Baseline transfer | Source lesson remains local and unavailable here | Write a new incident update |
| Baseline negative control | Source lesson remains local and unavailable here | Give an outline without drafting the message |
| Conditional transfer | Operator explicitly shares the actual learned abstraction | The same new incident-update prompt |
| Conditional negative control | The same shared abstraction | The same outline request |

Paired pages have neutral identical titles. Source conversation history is
kept in its original page. The shared lesson is promoted through the existing
operator revision API after both baseline tasks settle; this is an explicit
experimental action, not a claim that the model spontaneously shared it.

`ordinary-transfer.json` records native source/action identities, lesson and
promotion revisions, settled response artifacts, per-stage usage, and the
fixed baseline-first ordering. `provider-requests.json` preserves the actual
credential-free model requests, including posture computations, for exposure
inspection. Both files are private trial output under a generated `.gitignore`.

`audit-ordinary-transfer` reconciles the original World and Kernel traces,
request digests and multiplicity, per-stage scopes, budgets, response bytes,
and offered lesson versions. It checks that baseline requests did not receive
the teaching lesson and that conditional requests received its shared version.
It also checks for exact source text, a source-only reference, and raw private
episode fields. Those checks establish bounded exposure evidence, not the
absence of every possible semantic paraphrase.

All quality results remain `unreviewed`, including trials whose consistency
audit passes. Inspect the four actual later responses against their original
requests. The negative control should still deliver an outline. Keep failed
capture, interrupted runs, actual request counts, and unaccounted usage in the
report. Fixed ordering and extra posture computation remain comparison limits;
counterbalancing and a computation control are subsequent experiments. No
external request is made when a provider is not configured.

### Blind review of later ordinary work

The audit validates exposure and native settled work, not response quality.
After a completed real-provider ordinary-transfer trial, create a separate,
condition-blind review packet:

    python -m per_pulse.blinded_review prepare /tmp/per-pulse-posture/ordinary-trial --output /tmp/per-pulse-posture/review-001

Give the reviewer **only** the generated
`/tmp/per-pulse-posture/review-001/reviewer/` directory. It contains
`review-packet.json` and `ratings-template.json`, but **not** the mapping key.
Keep the parent `review-001/` directory private: its `private/condition-key.json`
can unblind every answer. Never upload or share the parent folder, even as a ZIP.
Responses are randomly assigned A/B within the finished-notice pair and C/D
within the outline pair; each packet contains the task, response and checks.
Review the finished notice, availability-first ordering, factual accuracy,
and the outline-only exception.

Copy `reviewer/ratings-template.json` into a completed ratings file, retaining
its `review_packet_sha256` and filling in a reviewer identifier and one boolean
per check. Ratings are bound to the **exact** review packet, so accidentally
grading responses with another trial's A/B key fails rather than inventing an
experience benefit. Preparation also requires matching task prompts inside
each blinded pair. Transfer only the completed ratings file back to the experimenter. With the private parent directory and
ratings file available, the experimenter reveals descriptive paired results:

    python -m per_pulse.blinded_review grade /tmp/per-pulse-posture/review-001 /tmp/per-pulse-posture/ratings-001.json

New packets use separate `reviewer/` and `private/` subdirectories, with
restricted local permissions and a parent .gitignore. Legacy flat-directory
packets can still be graded but must never be shared as a whole. Older ratings
templates without a packet digest require the explicit
`grade --allow-legacy-unbound` option; the resulting report marks
`ratings_packet_bound=false`, and such scores must not be silently mixed with
digest-bound reviews. Preparation
re-audits the native World/Kernel traces and response digests.
Synthetic transport is refused unless --allow-fixture is explicitly given for
QA, and synthetic origin remains visible in the report. Changed packets and
incomplete ratings are rejected. Do not publish private traces or supply the
condition key to reviewers.

The comparison reports human judgments and whether provider usage was accounted,
not a bill, statistically significant advantage, or independently authenticated
blinding. The baseline still precedes sharing and conditional stages can incur
additional computation. Counterbalanced replications and independently reviewed
outcomes are needed before attributing changes to learned experience.


## Reproduce the offline mechanism smoke

These commands use programmed `httpx.MockTransport` responses, not an external
model. Even if real provider variables are set, the fixture command uses only
the explicitly injected transport.

```sh
python -m per_pulse.posture_evaluation fixture-smoke \
  --experiences tests/per_pulse/fixtures/posture-experiences.json \
  --output /tmp/per-pulse-posture-fixture-001

python -m per_pulse.posture_evaluation compare \
  /tmp/per-pulse-posture-fixture-001/bare/evaluation.json \
  /tmp/per-pulse-posture-fixture-001/explicit/evaluation.json \
  /tmp/per-pulse-posture-fixture-001/notes/evaluation.json \
  /tmp/per-pulse-posture-fixture-001/conditional/evaluation.json \
  /tmp/per-pulse-posture-fixture-001/withdrawn/evaluation.json

python -m pytest tests/per_pulse/test_posture_evaluation.py -q
```

Expected smoke output: five `synthetic_fixture_runs`, zero
`real_provider_pairs`, and native write/trace verification. The passing SVG was
programmed into the transport. It proves the exporter, artifact checker, and
comparison exclusion work, not that a model learned anything. The test suite
also exercises the continuing native teaching and transfer path with explicitly
synthetic responses and checks source/action admission and budget failures.

The result to look for is an improvement over both the bare task and the same
ordinary notes, with preserved performance on the negative controls and
continued useful action after reminders are removed. Provider cost, failures,
and manual-review coverage belong beside that result. A full matrix or a
passing fixture is not evidence that expert ability has been learned.
