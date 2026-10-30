# Posture engineering

Current continuation plan: [ordinary correction, refinement, and transfer](posture-engineering-next.md).

Posture engineering makes prior experience available at the decisions that shape
the next piece of work: what the agent notices, which approaches it considers,
what it actually does, and when it considers the result ready. The user should
be able to give an ordinary task without also having to prescribe an expert
method on every turn.

An agent's **default posture** is its observable tendency under that ordinary
task instruction. A contextual intervention can change the default behavior of
the whole running system. This does not imply a change to the underlying model's
weights.

## Use it

Configure the provider as described in [the README](../README.md), then start:

```sh
per-pulse --posture
```

`PER_PULSE_POSTURE=1` enables the same feature. It is off by default, and example
mode keeps it off. The semantic implementation uses the configured provider;
it does not silently substitute scripted judgments. If the separate expert
module is configured, posture requires `P_PULSE_EXPERT_MODE=semantic`.

Continue normal work in Home or a session page. After an actual reply, explain
what worked or what needs to change. The model can use the native `posture`
tool to retain a conditional lesson from the original result and that later
feedback. The tool lets the model discover real message IDs; the model cannot
supply an invented original encounter. Learning remains optional: not every
message warrants a lesson.

Open **Settings → View this page’s experience** to inspect saved lessons and their
conditions, pause one, record what changed in the result, or explicitly make
the lesson available across pages. A lesson is local to its source page until
that transfer is requested. The shared abstraction can travel; the original
conversation and raw source feedback remain at the source. A receiving Work
can report whether shared guidance helped its own finished result. That Work
can revisit **its own** reports and its verified result message, including
after restart or withdrawal, without seeing other receiving Works' reports or
the original encounter. Such a report is attributed testimony, not proof that
the guidance caused the result.

**Save example** on a completed reply retains an exact selected passage without
requiring a conditional lesson. It can be captured before evaluation, with an
optional note and optional later user message. It is local until explicitly
shared. A shared example carries the selected passage and note; its surrounding
conversation and private feedback remain at the source. The original passage
cannot be edited in place. A later interpretation changes the note with the
same revision and feedback history used by lessons. Existing completed replies
can be captured while disconnected; generating replies and model-chosen learning
still require a provider.

For a later ordinary task, active eligible experiences enter the existing
semantic work path within its material budget before an action and at a bounded
delivery checkpoint. A model can return `PASS` when none adds useful work. With posture alone enabled
and no eligible experience, there is no extra posture side computation. Native
learning tool calls can still add ordinary model/tool rounds. With experience
available, the existing pre-action allowance is at most two computations per
turn, plus at most one delivery computation. Other enabled modules have their
own costs.

The native `list`/`find` tools and page journal retain latest-update order so
a fresh receiving-Work report remains discoverable. **Automatic bounded
pre-action and delivery work material** instead gives earlier placement to the
last substantive change in a lesson, example note, scope, or active state.
Appending `useful` or `not_useful` feedback without changing the experience
keeps the attributed report but does not make that unchanged advice displace
newer material at the prompt byte limit. The order is not a relevance score or
a claim that a report improved behavior: the configured model still evaluates
the actual task, structured conditions and counterexamples and may `PASS`.
Withdrawn and disabled experiences remain ineligible, including after restart.

Usage audit readers reconcile admitted receipts with the retained, fully
source-bound expert and CoverMe traces. A cancellation before receipt admission
does not erase a durable fork attempt or an earlier reviewer's completed usage;
unknown tokens remain explicitly unaccounted. The compatibility readers without
a supplied trace inspect admitted receipts only. This is trace reconciliation,
not a billing ledger: a cancelled World turn still returns an error without a
settled usage summary, and auditing its attempts requires the retained traces.

The delivery checkpoint can request one continuation when there is remaining
Kernel round budget. It does not certify the eventual artifact. Ordinary
optional-plugin failures retain the existing fail-open behavior; cancellation,
lost ownership, and unavailable durable state still stop the affected work.

## Learning through the native tool

| Operation | Input | Effect |
| --- | --- | --- |
| `list` | `op` | Return bounded current-scope experiences and recent original message IDs |
| `find` | `query` (2–160 printable characters), optional `after_experience_id` from a prior result | Case-insensitive literal search through eligible lesson content or example passages/notes, with bounded continuation via `next_after_experience_id`; not a semantic relevance judgment |
| `get` | `experience_id` | Retrieve one eligible active experience by its stable identity even if an earlier listing omitted it; inaccessible and unknown IDs are indistinguishable |
| `remember` | Original assistant result ID, later user feedback ID, title, lesson, conditions, counterexamples, and understanding/candidates/delivery shifts | Stage one context-local experience |
| `remember_example` | Earlier settled assistant result ID, title, exact excerpt; optional note and later user feedback ID | Stage a concrete context-local example without a required abstraction or evaluation |
| `revise` | Experience ID, expected revision, later user feedback ID, and changed lesson fields | Refine the existing experience in place, with attributed correction evidence |
| `feedback` | Experience ID, expected revision, later user feedback ID, and `not_useful`, `harmful`, or `withdrawn` | Stage attributed correction of an existing local experience |

Native `list` remains bounded, so an older experience may no longer appear among the first eight. `find` searches only the already-eligible, active records' title, lesson, conditions, counterexamples and decision shifts, with a literal Unicode case-folded substring. It returns at most eight matching experiences inside the same 16KB response bound. If `more_experiences` is true, call `find` again with the **same** `query` and the returned `next_after_experience_id` as `after_experience_id`; repeat until that continuation field is null. The cursor is an actual eligible match ID in newest-first journal order, never a client-supplied context. Unknown, private, inactive or nonmatching cursor IDs are rejected without revealing which case occurred. Each page stops before the first record that would exceed the response budget rather than silently skipping it; when a single matching record cannot fit at all, it is returned as an ID/title/revision-only `material_too_large` marker, **not** a usable or complete lesson. The order may change after concurrent revisions or deactivations, so a changed journal warrants restarting the search rather than assuming a snapshot. Its matching count is not a confidence or applicability score. `get` looks up an exact ID; neither operation searches unrelated Work encounters, grants sharing, changes eligibility, or copies raw evidence from a shared source Work. For a shared lesson the model receives only the same abstracted material already available through normal posture contribution. Both are read-only Kernel tool actions with no extra provider request or journal mutation of their own. Search and retrieval do not make posture intervention automatic; the model must still decide when an older conditional lesson matters. A full journal remains capacity-bounded as described below.

The host captures the source text and verifies the World, subject, context,
ordering, and settled original turn. Sources from a committed predecessor
generation remain available in the same context; the host's committed lineage
authorizes that access, and the source's actual subject identity is preserved.
An attempted generation change does not grant access. A staged write becomes
durable learning only after the originating Kernel action and turn succeed and
the World turn settles. A failed or cancelled turn does not teach a lesson.
Replaying the same action cannot apply it twice, including a crash between the
memory write and its completion receipt.

A normal committed generation change between successful settlement and journal
admission preserves that pending lesson. The original subject and source IDs
remain intact, and the active successor inherits it through the host lineage.
Stopping a later turn does not cancel an earlier completed turn's pending
learning. An unfinished or cancelled originating turn still cannot teach a
lesson, and archival without an active committed continuation does not admit it.

Native feedback must come from a user message later than the experience's
creation and already visible to the originating turn. The model cannot award
itself `useful` feedback. `harmful` and `withdrawn` pause the lesson; `not_useful`
retains the counterevidence for later interpretation. Positive feedback is an
explicit operator action through the interface or API.

Message provenance does not establish that a follow-up is a correction or that
the model interpreted it correctly. The model must make that judgment from the
conversation; the saved interpretation remains attributed and revisable. An
unrelated later message is not a reason to change a lesson.

To refine a lesson through ordinary conversation, the model can stage `revise`.
For example, a later message can explain that a practice session must confirm
an existing booking before it starts, while optional profile questions can
still wait. The model can add that exception to the original lesson rather
than leaving two conflicting versions active as separate records.

The operation accepts changed `title`, `lesson`, `conditions`,
`counterexamples`, and `shift` fields. It retains the experience ID, original
encounter and source, and records the actual later user message alongside the
new version. The lesson change and its correction evidence are committed in
one journal write after the exact native action and turn succeed. Failed,
cancelled, or conflicting updates do not partly revise a lesson. Replaying an
already admitted update does not repeat it, even when another edit followed.

The correction must be later than the version being revised. An older feedback
message cannot be reused to override a newer operator edit. The model cannot
change sharing scope or active state through `revise`; a paused lesson remains
paused until the operator resumes it. `revised` is an attributed change record,
not a usefulness award. When a lesson should stop, `feedback` with `harmful` or
`withdrawn` retains that existing path. Operator field changes continue to use
the revision API below.

Pausing, revising, or disabling posture takes effect at the next activation
eligibility check, including recovery of a saved contribution before native
admission. The eligibility check and native admission are separate boundaries;
a check that already passed may complete. The original provider-cost receipt
remains. A delivery continuation already durably admitted to the current turn
resumes its recorded decision after restart. Old advice and delivery-review
envelopes are not injected as current instructions in later turns. The original
conversation, past tool calls, and already-produced work remain history;
pausing is not an erasure of their effects.

Provider evidence stays attached to the source. A `TEST_DOUBLE` or
`EXPLICIT_MOCK` result produces a `constructed_fixture`, even though its local
runtime events are real. Missing provider evidence remains explicitly unknown.
A stored user stimulus establishes attribution, not independent verification
of the reported outcome. Source text passes the existing credential guards;
recognized credential values are rejected or redacted before journal admission.

## Capacity and continued use

Lessons and examples occupy the same native World journal. The payload schema
is `per-pulse.posture.v2`; its existing component identity remains unchanged.
Version 1 lessons are read without rewriting their IDs, sources, or history,
and the next successful journal mutation writes version 2. There is no separate
example index or authoritative transcript store. Older software that only
accepts version 1 cannot read a journal after this upgrade.

An example excerpt is bounded to 8,000 UTF-8 bytes and its optional note to
4,000 bytes. The host verifies that the excerpt occurs verbatim in the original
settled native result, retaining its content digest and character offsets.
When the same passage occurs more than once, those offsets identify the first
exact match; they do not claim to reconstruct the browser's selection position.
An optional follow-up must be an actual later user message in that context;
its attribution does not verify the reported outcome. No follow-up means no
outcome was attached, not that the example succeeded. During material packing
an excerpt remains intact or the whole record is explicitly omitted. The
existing packet budget can therefore omit a large example. `find` and `get`
retain their bounded, explicitly reported omission behavior.

A workspace's main experience journal holds at most 128 records and 4 MiB of
stored JSON, including original encounters, feedback, and revision history.
These limits cover all its pages and generations, including paused records.
An addition or ordinary revision that exceeds either applicable limit is
rejected without changing the stored journal. There is no silent eviction.

Stopping an active lesson remains possible when the main journal is full.
A separate World control component holds at most 128 stop records within
6 MiB, preserving the full new negative feedback and its revision. A pure
`active: false` update, or `harmful`/`withdrawn` feedback, can use this bounded
path. Reads reconstruct the prior history without rewriting or deleting it.
Combining a pause with other edits still requires the ordinary journal write
to fit. Reactivation also requires an ordinary revision and can be refused at
capacity; an old revision cannot undo a later stop.

Pausing does not release record slots or storage. The product does not yet
provide a workflow to reclaim journal capacity through deliberate archival or
compaction. A long-running workspace can therefore reach a point where new
learning and further edits are refused, while existing eligible lessons and
the ability to stop them remain available. Capacity visibility and a deliberate
history-preserving archival workflow are further lifecycle work, not behavior
implemented by this version.

## API workflow

All routes use the existing local service and page IDs. Read
`GET /api/pages/{pageId}` to obtain actual message IDs, then use these endpoints:

Source navigation can include `?sourceMessageId={messageId}` to reopen the
exact authorized message after it falls outside the recent conversation
window. The client identifies that earlier source and marks the gap before
recent messages; it does not imply that all intervening history is visible.

| Method and route | Body or result |
| --- | --- |
| `GET /api/pages/{pageId}/posture` | Enabled state and experiences visible in this page |
| `POST /api/pages/{pageId}/posture/experiences` | Create from `sourceMessageId`, `feedbackMessageId`, `title`, `lesson`, `conditions`, `counterexamples`, `shift`, and optional `scope` |
| `POST /api/pages/{pageId}/posture/examples` | Create a local example from `sourceMessageId`, `title`, exact `excerpt`, optional `note`, and optional `feedbackMessageId`; sharing requires a separate revision |
| `PATCH /api/pages/{pageId}/posture/experiences/{experienceId}` | `expectedRevision` and `changes` |
| `POST /api/pages/{pageId}/posture/experiences/{experienceId}/feedback` | `expectedRevision`, `verdict`, and concrete `evidence` |

`conditions` accepts a text condition or nested `all`, `any`, and `not`
expressions. `counterexamples` contains one to eight concrete boundaries.
`shift` has three text fields: `understanding`, `candidates`, and `delivery`.
They identify how experience can affect work, rather than an obligatory set of
steps for every task. `scope` defaults to `context`; an explicit `subject`
setting makes the abstraction available in other pages of the same subject.
A shared experience includes `sourcePageId` and, when native source identities
are present, `sourceAnchor` with `resultMessageId` and an optional `feedbackMessageId`.
These are navigation-only IDs for opening the exact original result or correction.
The original encounter, raw source record and feedback history are not copied
into another Work. Source deep links use the existing page history window and
return navigation; they are not an additional experience store.

For example, pausing an experience at revision 1 sends:

```json
{"expectedRevision": 1, "changes": {"active": false}}
```

Revising its lesson or applicable conditions uses the same endpoint. The source
encounter remains intact, and each update retains the previous revision.
Stale revision writes return a conflict; refresh the record before deciding
what to change. Edit a transferred lesson or example from its source page.
An example allows title and note revisions, together with the same activation
and scope controls. It does not accept lesson fields, replacement excerpt text,
or a change of representation.

Operator feedback accepts `useful`, `not_useful`, `harmful`, and `withdrawn`:

```json
{
  "expectedRevision": 2,
  "verdict": "harmful",
  "evidence": "It added an unnecessary preview to a fixed registration workflow."
}
```

This report is attributed operator evidence. Saving it is not an automated
quality judgment. `harmful` and `withdrawn` disable the experience; a later
revision can narrow its conditions and explicitly reactivate it.

## The behavior we want

Consider a product whose value is an immediate interactive experience. An agent
can correctly explain the importance of the first-use experience while still
building a long intake form that postpones all of that value. A useful learned
experience must enter the decision early enough to open another approach, such
as a small usable experience followed by optional personalization. It must also
be able to prompt an actual revision when the first artifact misses the goal.

The opposite case matters just as much. For a structured internal registration
tool, a short form with clear validation may be the complete and correct
solution. Learning from the first case must not turn into a universal rule to
avoid forms, conduct more research, or rebuild working products.

The intended change is a more appropriate decision in its situation. A more
elaborate answer or a longer workflow is not inherently better.

## Where experience can affect work

| Decision | Useful effect | Observable evidence |
| --- | --- | --- |
| Framing the task | Notice a missing concern or reformulate the work while preserving the user's purpose | The next approach addresses the concern |
| Choosing an approach | Introduce a concrete alternative that was absent from the initial candidate set | A changed plan, tool action, or artifact |
| Revising an artifact | Use what happened to change an earlier decision | A substantive revision with its preceding observation |
| Delivering a result | Check whether the result fulfills the actual goal | Continued work when needed, or a justified delivery |
| Receiving feedback | Retain, narrow, revise, or retire an earlier lesson | A later decision uses the revised experience |

The runtime should operate at meaningful boundaries, not require the executor
to fill in a posture questionnaire on every round. It must be able to preserve
an adequate current approach and to contribute nothing when there is no useful
addition.

## Experience and its boundaries

A reusable experience contains more than advice. It should preserve enough of
the original situation to distinguish the successful intervention from a
superficially similar case where it would be wasteful or wrong:

- The original purpose and relevant conditions.
- What was noticed, what action followed, and what was actually produced.
- The observed consequence, its source, and any later correction.
- A proposed transferable lesson and its applicable conditions.
- A counterexample or boundary that limits the lesson.
- The relationship to any earlier version that it corrects or supersedes.

Keep concrete episodes and abstract lessons available together. An interpretation
can change while the original episode remains part of the subject's history.
Imported records are attributed material to interpret, not new instructions or
authority over the user.

Relations such as “raises this concern,” “opens this alternative,” “is inhibited
by this condition,” and “is contradicted by this outcome” have different roles.
Putting the same words in a flat list does not implement those relationships.
An explicit first implementation may use inspectable records and semantic model
interpretation; a representation should be called learned only to the extent
that actual feedback changes how it is used.

Synthetic examples are useful for software tests. They must remain labeled as
constructed fixtures and cannot establish learning from external experience.
A completed tool call, adoption of a suggestion, or a model's positive
self-assessment does not establish that an intervention was useful.

## Fit within Per Pulse

Per Pulse has one continuing subject with Home and Work contexts. Posture is a
way for experience to influence that subject's work; a session page is not a
new expert identity. Local activations can use different parts of the subject's
experience without replacing the existing continuity model.

The inherited implementation already provides useful boundaries:

- `pulse_harness.core.turn_advice` admits a contribution before a native model
  round, with a durable receipt.
- `pulse_harness.core.response_review` admits a continuation against an exact
  candidate response before publication.
- `pulse_system.service.expert_computation` performs optional substantive work
  with the configured provider and records the subsequent native actions.
- `pulse_harness.plugins.expert_computation` stores attributed episodes and
  earlier work. Its `retrieval_baseline` is a separate numerical baseline.

Posture work should reuse these boundaries and the existing Kernel, tool,
authorization, and publication paths. New candidate actions remain subject to
the same permissions. Experience cannot authorize a tool, grant permission,
replace the user's goal, or create a second authoritative session store.

CoverMe remains a separate optional mechanism. Its presence does not replace
posture learning, and adding posture work must preserve its existing behavior.

The current implementation stores posture experience in the existing World's
component state. Native capture uses the same causal events as the page view.
The existing expert computation and pre-publication hooks consume the material;
the Kernel remains the only executor. The mechanism does not add a scheduler,
new subject, or parallel conversation database.

## Behavioral acceptance

Use tasks whose instructions describe the desired result without telling the
agent which posture to take. Keep the model, starting materials, tools, and
resource allowance comparable. Preserve the actual action trace and artifact,
not just the final explanation.

| Case | Ordinary task | Evidence of a useful decision | Failure to reject |
| --- | --- | --- | --- |
| First-use experience | Build an interactive practice product that a visitor can try immediately | An actual usable first interaction; required inputs earn their place | A long intake form followed by commentary about user experience |
| Structured registration | Build a small internal tool to collect a fixed set of records | A concise form, necessary validation, and usable records | Unrequested immersive onboarding or exploratory work |
| Visual exploration | Explore the visual direction for an illustrated scene | Appropriate use of available visual capabilities and inspection of the result | Silently excluding a useful image model because text-to-vector code is familiar |
| Exact editable diagram | Produce a precise diagram with editable labels and geometry | A suitable editable artifact with checked structure | Replacing exact structure with an uneditable generated picture |
| Analytical deliverable | Explain a surprising result with the available observations | A discriminating analysis and supported conclusion, or a next action that resolves the key uncertainty | Polished prose that leaves the decisive question untouched |
| Small repair | Fix a well-specified local defect | A focused repair with proportionate verification | Unnecessary investigation, redesign, or repeated permission requests |

The last four cases test whether the mechanism transfers beyond the vocabulary
of the first pair. They are evaluation tasks, not a mandatory taxonomy for the
runtime.

Compare these conditions where a real-model evaluation is available:

1. Ordinary task with the mechanism disabled.
2. The same task with an explicit expert instruction or static checklist.
3. The same task with ordinary episode retrieval.
4. The same task with contextual experience affecting the decision boundaries.

The first question is whether the fourth condition improves actual work beyond
ordinary retrieval without degrading the cases where no extra intervention is
needed. Subsequent tasks should remove explicit reminders and vary vocabulary,
domain, and elapsed work. A mechanism that only works immediately after being
told to use it has not demonstrated the intended default behavior.

Record the following separately:

- The first consequential judgment and the action that followed it.
- Any added or abandoned approach, and the evidence that changed the choice.
- The resulting artifact's usefulness, assessed against the original task.
- Over-triggering in negative cases and regression after longer work.
- Provider requests, token usage when available, elapsed time, and human
  correction required.

An assessor should inspect the artifact and action evidence without being told
which condition is expected to win. The runtime's own explanation is evidence
of what it said, not an independent quality score.

## What software checks establish

Deterministic tests can establish that a contribution reaches the correct
boundary, that actual action receipts are retained, that a revision is visible
later, that disabled mode has no effect, and that restart and replay preserve
the committed decision. They can also establish that context ownership,
authorization, cancellation, and optional-plugin composition still work.

These checks do not establish expert judgment or cross-task learning. Real
provider runs and independent assessment of actual work are needed for those
claims. Report the two kinds of evidence separately, including unsuccessful or
incomplete runs. This distinction limits claims about the mechanism; it does not
add a research-approval step to ordinary authorized work.

Reject the behavioral claim when the mechanism changes only the explanation,
works only on near-identical words, makes the simple cases worse, cannot change
a delivery decision, or repeatedly mistakes its own approval for a useful
external result.

## Run an artifact-based comparison

The evaluator keeps task prompts separate from review rubrics and records
actions against artifact hashes. Its six runnable cases are `experience_entry`,
`registration`, `patient_analysis`, `customer_update`, `exact_svg`, and
`local_bugfix`. The visual exploration case above is an additional research
case, not one of the six bundled tasks.

```sh
python -m per_pulse.posture_evaluation catalog
python -m per_pulse.posture_evaluation run exact_svg \
  --condition bare --output ./private-runs/svg-bare --pair-id svg-01
```

The runner requires explicit provider configuration and a new output directory.
It uses the native provider and Kernel interfaces with a bounded provider-call
allowance. `notes`, `conditional`, and `withdrawn` conditions also require
`--experiences path/to/constructed-fixtures.json`; the fixture input is a list
of `PostureMemory.add_experience` arguments without subject, context, or identity
fields and explicitly marked `constructed_fixture`. Original product experience
needs an attributed export before use in a separate experiment.

The `conditional` run uses the same `ExpertRuntime` advisor and posture reviewer
as the product. The provider-call ceiling includes those computations. Retain
all costs and failed runs when comparing conditions; a faster result is not
automatically a more useful one.

```sh
python -m per_pulse.posture_evaluation evaluate \
  ./private-runs/svg-bare/record.json --artifacts ./private-runs/svg-bare/artifacts
python -m per_pulse.posture_evaluation compare ./private-runs/*/evaluation.json
```

A complete comparison requires the five matched conditions: `bare`, `explicit`,
`notes`, `conditional`, and `withdrawn`. Withdrawal records include teaching and
intervening-task lineage, but that JSON alone cannot prove prior learning.
Exact vector geometry and held-out patient statistics have deterministic
artifact checks. Interactive experiences and written communication require
concrete browser or human observations bound to the delivered artifact. Missing
observations remain unreviewed. Test transports remain synthetic evidence.

Run directories contain private traces and should not be committed to source.
The tests shipped with this implementation use local transports to verify the
learning, activation, correction, and withdrawal paths. They do not report a
measured improvement in real-model judgment.
