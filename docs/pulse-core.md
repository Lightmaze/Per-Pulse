# Local activation core

Per Pulse has one continuing subject with separate Home and Work contexts. This core adds persistent local attention to the existing runtime. It does not create a second model loop, session store, or agent identity.

## Execution path

The existing `RuntimeService` calls `ContextBody.advance()` at its live tick boundary. The body evolves numeric fields, reconciles settled results and attention actions, and offers at most one eligible local activation. The existing causal ledger, Pulse Engine, and Harness Kernel then admit and execute it. Generic random spontaneous activation remains disabled in the Per Pulse profile.

The component state is stored in the same World database under `per-pulse-context-body-v1:<world-id>`. Native causal event IDs identify activations and results; native Work IDs identify pages. Writes use the runtime ownership fence and the existing SQLite transaction mechanism.

| State | Meaning |
| --- | --- |
| Retained note and drive | A local unfinished thought and its persistence strength. The model can inspect, hold, or release its own context's thought. |
| Excitation and inhibition | Numeric local state; inhibition does not need a textual message. |
| Directed connections | A settled result can excite or inhibit another context of the same subject. |
| Evidence addresses | Up to eight positive source-result references per context; inspection exposes at most 2,000 characters from each settled source result. |
| Shared allowance | Explicit permission for a bounded number of background continuation admissions. |
| Revision and action receipts | Protection against stale model actions, failed turns, duplicate delivery, and results overwriting newer user edits. |

## Field dynamics

For elapsed live time `dt`, excitation approaches twice the retained drive:

```text
excitation = 2 * drive + (excitation - 2 * drive) * exp(-dt / 30)
inhibition = inhibition * exp(-dt / 90)
readiness = excitation - inhibition
```

Elapsed time is capped at five seconds per advance. Reopening resets the live clock and pauses the grant, so downtime is never simulated as thought. The default refractory interval is 20 seconds per page. A page is eligible only when it has an available native context, a nonempty retained note, positive drive, readiness at least one, and no queued or running input of its own. A successful admission resets excitation and debits one allowance unit in the event's transaction.

Ordinary user input adds a small local excitation. A settled result normally reduces its source's drive and excitation. A model that successfully retains a new thought in that turn preserves the new drive; a newer user edit also wins over the older result. Positive connections add excitation and an attributed result address; negative connections add inhibition without relaying text. Only settled, same-subject results participate, and selected replay does not propagate activity.

Failed or uncertain local turns clear their pending activation, reduce drive, and increase inhibition. They do not automatically replenish their allowance.

## Continuity and authority

An internal activation enters as a typed, empty-content causal event. The Kernel receives a host-authored notice identifying it as internal continuation, and the `attention` tool exposes the retained note and linked evidence as tool results. Generated notes are never interpolated into a system instruction or presented as a new human message.

Model `hold` and `release` actions are staged. They take effect only after both the World turn and exact Kernel action have succeeded. The final successful action for that turn and context wins. Failed or cancelled turns discard their staged changes. A later user revision takes precedence over an older model update.

The profile permits native internal Work operations while keeping filesystem mutations disabled. Team creation, team dispatch, and team history are unavailable. A Work result returns through a source-bound native report: a trusted notice carries identifiers, and `work.read_report` exposes the actual result as evidence. Selected user returns retain their separate user-authored provenance.

## Allowance, pause, and restart

The grant counts admitted continuation turns across all pages, including native Work starts and reports descended from a local activation. Native Work stores its verified attention root and performs the shared debit inside its own causal admission transaction. Delivery retries find the existing idempotent event before charging. Without allowance, a prepared request stays deferred rather than calling the model.

Starting a grant replaces the remaining grant. Pausing cancels queued local activations and admitted queued Work descendants. Already-running turns may finish; follow-up Work still needs allowance. Reopening preserves notes, numeric fields, connections, and prepared requests, but cancels queued background activations and starts with no grant. Human input is not cancelled by these controls.

A continuation may contain several provider requests and tool rounds. This admission allowance is not a provider-request, token, wall-clock, or money budget. Native Work may prepare an empty page before delivery is allowed; preparation itself performs no inference. Ordinary user-requested work continues through the ordinary runtime path.

## Phase checkpoint

The phase includes local field dynamics, the Kernel attention bridge, signed cross-context influence, source-bound native Work, bounded derived continuations, API controls, and the English **Keep thinking** interface. Three implementation/validation agents worked on the existing runtime contracts; the main integration retained one World and one Kernel path.

Checks completed for this checkpoint:

- Full Python regression suite: 75 passed, one opt-in sustained-run test skipped in the default run.
- Real World/Kernel tests exercise transactional admission rollback, duplicate delivery, scope ownership, staged action success/failure, source provenance, user revision precedence, cross-context propagation, replay suppression, and pause/restart behavior.
- A local HTTP provider fixture runs Home continuation, native Work creation, Work result return, and report reading against the actual streaming adapter. Three admitted continuation turns require six fixture provider rounds and share the same grant.
- Browser checks against the compiled client and actual local backend passed seven flows at 1440×1000 and 390×844, with no console or unhandled page errors. They cover retained thoughts, connections, reload persistence, release, and disconnected behavior. Both layouts were visually inspected.
- TypeScript/Vite production build and Python wheel/source-distribution build passed.

The separately run sustained-runtime results are recorded in [the validation note](pulse-core-validation.md). Tests use explicit synthetic transports; no external-model credentials were configured, and no live-model quality claim is made.

The next phase can assess useful continuation behavior with an explicitly configured real model, tune field parameters from observed work, and evaluate connection selection. The current connection graph is explicit and user-edited; this checkpoint does not implement learned connections or claim an emergent cognitive result.
