# Posture engineering: continuation plan

## Purpose

An ordinary correction should change an appropriate later decision: which
approach is considered, what is done, and whether the result is ready. The
working target remains a useful default under ordinary task instructions.
The system must also retain a simple adequate approach when the lesson's
exception applies.

This continuation starts from `feat/posture-engineering` at
`c57afeb167e40b327c8ccf5229f8c45557fd1899` and is scoped to posture learning.
The existing World, Home/Work contexts, Harness Kernel, page controls, and
provider interfaces remain the integration points. The product is still one
continuing subject with context-local histories.

## This iteration

### Refine an existing lesson in conversation

The existing native learning tool could remember a new lesson or stop an old
one. It could not change an old lesson's conditions in response to a later
ordinary correction. Creating a second lesson left the older generalization
active, while withdrawing the old lesson lost a still-useful distinction.

The new `revise` operation updates that same attributed lesson. It preserves
the original encounter, source, identity, sharing scope, and active state;
increments the version; and retains the actual later correction. The changed
lesson and correction evidence are one journal mutation. Admission waits for
the originating native action and turn to succeed. A stale version, cancelled
turn, or full journal leaves the old lesson intact. Crash replay recovers the
already committed revision without replacing a later operator edit.

The browser regression follows the full path: an initial practice-first
lesson, a later required-booking exception, inspection of the changed
condition and original source, service restart, and a shared projection that
includes the refined abstraction without the source conversation or raw
revision feedback.

### Separate experience from retained teaching history

The previous continuing-subject trial explicitly requested a lesson and kept
all teaching history in Home. It remains useful for lifecycle checks. The
ordinary-feedback cross-page protocol adds a different observation:

1. Give an ordinary writing task in a source Work page.
2. State a preference and its exception without naming the learning tool or
   claiming the first answer failed.
3. Observe whether a native lesson is actually admitted. If none is admitted,
   retain `capture_absent` and stop; do not manufacture the missing experience.
4. Run a later ordinary task and a plan-request negative control in fresh
   pages while the lesson remains local to its source.
5. Explicitly share the actual admitted abstraction through the operator
   revision path, then run the same two prompts in new empty pages.
6. Preserve the actual responses, native sources, request counts, and material
   exposures. Check that source conversation history did not enter the targets.

The order is deliberately recorded as baseline first. It is an initial paired
probe, with additional posture computations visible in the cost record. It
does not by itself isolate computation effects or establish general capability.
Response quality remains unreviewed until someone inspects the actual work.

The previous explicit-teaching correction now states a preference without
accusing a successful answer of being a writing plan. Request-audit comparison
also reconciles digest multiplicity: repeating one valid request entry cannot
stand in for a different completed request.

### Source-preserving operator refinement with optional evidence

The originating Work's Refine lesson editor can now attach a short, optional
operator-described outcome to the same atomic lesson revision. This reuses the
native journal's `revise_from_feedback` operation: original encounter and source
identities stay unchanged, the new revision and described evidence survive
restart together, and another Work receives only the revised abstraction.
The reported outcome is explicitly tagged `operator_described_refinement`;
it is **not** a newly observed native user event or evidence that a model
autonomously learned. The same source-Work editor now optionally exposes the
three existing action/understanding/delivery `shift` fields in a collapsed
section. An operator can revise the *proposed future action* as well as the
lesson text and exceptions in one atomic, version-checked native revision;
unchanged fields remain intact, and the receiving Work only gets the shared
abstraction. Blank or oversized shift fields are refused before save. Without a description, quick operator revisions retain
the previous behavior. Sharing/pausing changes cannot masquerade as attributed
learning; later genuine model-interpreted user corrections still follow the
existing source-message and turn-evidence path.

### Finding an older lesson without exposing the source episode

The source-Work Lessons dialog now offers a compact search of the bounded
journal's shareable abstraction: title, lesson, conditions, counterexamples
and proposed future choices. It deliberately does not index the original
encounter or private feedback. Filtering happens within the already authorized
page projection; it makes no provider request, changes no lesson permissions,
and does not disturb the original source/return journey. The English/简体中文
browser regression checks matching, empty results, and a private-encounter
phrase that must not match. This helps a person locate an old judgment before
refining or pausing it; it is not itself model learning or retrieval.

## Checkpoint verification

On 2026-10-25, the complete Python suite passed **160 tests** in 48.65 seconds.
The TypeScript/Vite build and Python wheel/sdist build passed. All **219 Python
source modules** matched the wheel byte for byte, and no private databases or
transcript files were included. Native revision received an independent code
review and targeted regression run.

The existing Chromium CI flow now also checks conversational refinement,
restart persistence, and shared projections of the revised lesson. Its
screenshots are retained as a seven-day workflow artifact. Browser access from
this development session could not reach the local service, so the local
checkpoint does not claim a manually observed browser pass; use the workflow
result for the rendered-flow result.

The runtime and experiment tests use explicitly synthetic provider transports.
No external model credentials were configured for this checkpoint. The new
ordinary-feedback runner is ready for the real-provider comparison described
below; this checkpoint delivers the learning mechanism and usable experiment,
with no measured capability gain asserted.

## Next decisions

| Priority | Concrete work | Result that changes the decision |
| --- | --- | --- |
| 1 | Run ordinary-feedback cross-page trials with a configured real provider, including unsuccessful capture | Actual later responses and costs show whether the captured distinction helps and whether the explicit plan request stays a plan |
| 2 | Carry the same protocol into the existing practice-product and registration artifact cases | A browser-tested first interaction improves while an appropriate short registration form remains adequate |
| 3 | Add access to older or omitted lessons when a correction needs them | A relevant old experience can be located and revised after newer lessons exceed the native list window |
| 4 | Repeat with task order counterbalanced and an appropriate computation control | An apparent improvement survives order changes and can be distinguished from extra inference alone |

Each next step should answer one of these concrete questions. More saved
lessons, longer reflections, or more checks are not success criteria on their
own. Failed capture, harmful transfer, and helpful non-intervention are useful
observations and remain part of the record.

## Integration handoff

The runtime change touches `pulse_system/service/native_posture.py` and
`pulse_system/service/posture.py`. It adds a native operation and an atomic
journal method; it adds no scheduler, action permission, runtime, or
authoritative store. Other routes can consume the revised lesson through the
existing posture material projection. The source encounter and cross-page
privacy behavior are unchanged.

See [runtime behavior](posture-engineering.md) and
[evaluation commands and limits](posture-evaluation.md). Source control keeps
this plan beside the implementation so another continuation can start with
the actual remaining question.
