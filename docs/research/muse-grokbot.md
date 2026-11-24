# Muse and Grok Bot: harness evidence and Per Pulse contracts

Research date: 2026-10-23. Scope: public first-party documentation. Target: `Lightmaze/Per-Pulse`.

The most useful lesson is that a persistent agent product needs several independently governed lifecycles. A conversation can stay open while a run waits, a computer can be replaced while durable work survives, and an action can expire while its task remains useful. Per Pulse should make those distinctions visible through its existing Home/Work and Harness Kernel, rather than adding another runtime.

## 1. What the sources establish

**Evidence labels:** `disclosed implementation` means an official technical description names the component; `documented behavior` means the product documentation specifies behavior; `design inference` means our proposed mechanism. These labels describe the strength of the claim, not a comparative quality score.

| ID | Source, publication/update | Evidence and short finding |
| --- | --- | --- |
| M01 | [How We Designed Muse](https://introducing.muse.ai/), September 2026 | Documented behavior: main and side chats; concurrent requests and interruption; editable memory; scheduled/event work; selective updates; goals, activity, approvals and artifacts. |
| M02 | [How We Built Safety Into Muse](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse), 2026-09-08 | Disclosed implementation: Hatch daemon in a `systemd-nspawn` cell; host-side `Sentinel`, `privsep`, `authd`, and separate durable Postgres state. A browser broker mediates access. Human takeover pauses the agent. Approval grants carry scope. |
| G01 | [Grok Bot overview](https://docs.x.ai/grok-bot/overview), 2026-10-07 | Documented behavior: durable named Bots, cloud execution, shared computer resources, independent conversations and learned context, cross-Bot coordination. |
| G02 | [Create and manage Bots](https://docs.x.ai/grok-bot/bots), 2026-10-12 | Documented behavior: role descriptions carry lasting responsibility; duplication copies configuration but excludes conversation history and learned memory. Hiding is distinct from pausing. |
| G03 | [Message and collaborate](https://docs.x.ai/grok-bot/chat-and-collaboration), 2026-10-17 | Documented behavior: user messages can redirect ongoing work and outrank background work; asynchronous Bot messages wake their recipient. Groups preserve handoffs, while certain sensitive controls stay outside group conversations. |
| G04 | [Use the computer and apps](https://docs.x.ai/grok-bot/computer-and-apps), 2026-10-12 | Documented behavior: account-wide files, browser sessions and plugins; one screen per Bot, one computer-use task per screen. Screens are not isolation boundaries. Durable workspace and temporary software have different recovery guarantees. |
| G05 | [Skills and routines](https://docs.x.ai/grok-bot/skills-routines-and-automations), 2026-10-12 | Documented behavior: skills encode method; routines bind an owner and schedule/event. Demonstration produces a draft skill. Event integrations and ordinary plugins are separate. Runs have inspectable outcomes and controls. |
| G06 | [Approvals, security, and privacy](https://docs.x.ai/grok-bot/approvals-security-and-privacy), 2026-10-20 | Documented behavior: action-specific consent; unattended approval requests expire after about ten minutes without executing. A future allow rule does not execute an expired request. Conflicting rules favor asking. |
| G07 | [Files and results](https://docs.x.ai/grok-bot/files-and-results), 2026-10-03 | Documented behavior: inspectable artifact cards; revision of existing work; delegated Cloud Agent results return to the Bot computer and conversation. Shared intermediate files do not replace a final result link. |
| G08 | [Settings and notifications](https://docs.x.ai/grok-bot/settings-and-notifications), 2026-10-17 | Documented behavior: working, unread activity and needs-attention states differ; notification delivery depends on Bot preference and active device, with delayed escalation for input requests. |
| G09 | [Grok Bot security](https://docs.x.ai/grok-bot/security), 2026-10-12 | Disclosed implementation: separate action-review model; backend-held connector tokens; distinct administrative audit and action-recording pipelines. Connector restrictions and network restrictions govern different paths. |
| G10 | [Manage Grok Bot computers](https://docs.x.ai/grok-bot/computers), 2026-10-16 | Documented behavior: recreate pauses a running turn at a safe point before switching computers; terminate keeps durable disk but stops current work. Retried administrative starts avoid duplicate operations. |
| G11 | [Security FAQ](https://docs.x.ai/grok-bot/security-faq), 2026-10-18 | Disclosed implementation: dedicated Firecracker microVM per user. Documented behavior: authentication sessions can expire or be lost during recreation despite persistent data. |
| G12 | [Troubleshooting](https://docs.x.ai/grok-bot/troubleshooting), 2026-10-12 | Documented behavior: cloud work may continue while a client disconnects; blocked work can require input, credentials, quota or an available screen. Recovery, update and reset have different consequences. |
| G13 | [Team Bots](https://docs.x.ai/grok-bot/team-bots), 2026-10-16 | Documented behavior: team memory and private conversations coexist. Execution placement follows the conversation: a member's personal chat uses that member's computer; shared Slack contexts use a separate shared computer. |

Exact retrieval references and a short excerpt per source are in `evidence-muse-grokbot.json`. The documents were opened directly and official-domain search was used to cross-check their identity. No source code for either proprietary harness was recovered in this research.

## 2. Two useful architecture shapes

Muse gives the stronger published account of the **boundary between an agent and its operating environment**. Its disclosed design separates the process that proposes work from services that hold credentials and authorize external effects. For Per Pulse, the transferable decision is to keep an action's authority independent from the text that proposes it. Reproducing Meta's particular Linux stack is not a prerequisite for a useful local alpha.

Grok Bot gives the richer account of **several ongoing owners sharing operational resources**. Its documented roles, communications, screens and machine lifecycle reveal that logical ownership, context visibility, resource contention and credential access are different axes. Per Pulse can reuse these distinctions without turning each session page into a separate personality or agent.

These are architecture inferences. The exact model prompt, memory consolidation algorithm, queue implementation, scheduler, lock mechanism and database schema are not disclosed by the cited pages. Asynchronous messaging alone does not establish exactly-once delivery, nor does a persistent disk establish recovery of every in-flight effect.

## 3. Keep the existing Per Pulse object model

The repository's `AGENTS.md` requires one authoritative runtime and session store, reuse of Home/Work and Kernel contracts, and English product content. Its README says page IDs are Work IDs and message IDs are causal-event IDs. Those are the anchors below.

| Concern | Existing anchor to reuse | Additional contract to expose or strengthen |
| --- | --- | --- |
| Subject and focused context | `PulseWorld`, Engram lineage, Home/Work | A page selects a context of the continuing subject. A new page does not implicitly create an agent identity. |
| Input and causality | Native causal-event IDs | Each accepted input records its origin, target context, relation to ongoing work, and resulting admission or wait reason. |
| Session and turn | `HarnessScope`, `HarnessSession`, `HarnessTurn` | UI page lifetime, context lifetime and run lifetime stay distinguishable. |
| Tool effects | `ActionRecord`, `ToolDescriptor`, `ToolResult` | Correlate the exact proposed input, authority decision, dispatch, receipt and resulting artifact. |
| Permissions | `ApprovalRecord`, `CapabilitySet` | Bind a decision to its action/scope; make expiration, cancellation and supersession explicit. |
| Control | `ControlCommand`, `HarnessHandle.steer/abort` | Tell the user whether input is queued, delivered at a safe point, or has stopped future work. |
| Subtasks | `WorkerRequest`, `WorkerResult` | Carry ownership, parent scope, acceptance conditions and result references; child completion does not itself complete the parent goal. |
| Recovery | Kernel recovery/checkpoint records | Represent uncertain effects and distinguish them from work that is safe to retry. |
| Product projection | `PageService`, existing World component state | Project operational state from authoritative records instead of keeping a second task database in the web layer. |

Code entry points read for this mapping: `src/per_pulse/service.py`, `src/per_pulse/app.py`, `src/pulse_system/service/harness_factory.py`, `src/pulse_system/interaction/api/harness_kernel.py`, and the public contract declarations in `src/pulse_harness/core/`. This is an integration map, not a claim that every desired behavior is already reachable through the new product API.

## 4. Necessary modules and executable contracts

`P0` means necessary to make ordinary ongoing work usable. `P1` means important once the first end-to-end path works. These are Per Pulse design decisions informed by the evidence, not claims about proprietary implementation details.

| Priority | Module | Contract | Concrete acceptance case |
| --- | --- | --- | --- |
| P0 | Persistent input and work admission | Acknowledging a message means its causal input is durable. Routing identifies Home or a Work context and its current intent revision. | Send while the model is unavailable; restart with a provider; process once with the same original source event visible. |
| P0 | Run supervision and steering | A new user instruction can update an active work context. Record when it takes effect; preserve already completed effects. | During a slow tool call, send a correction. The next model/action boundary sees it, and the old request cannot silently become the final accepted outcome. |
| P0 | Operational attention | Separate queued, running, waiting-for-input, waiting-for-approval, unavailable and uncertain. Completion of a turn is not proof that a goal is complete. | An approval blocks the newest action while an older message remains queued; the page still exposes the blocking request clearly. |
| P0 | Action gateway and receipts | Dispatch a typed action under a scoped authority decision; retain outcome and provider correlation. | A successful external operation loses its acknowledgement; surface an uncertain result or reconcile by operation ID before retrying. |
| P0 | Durable artifacts and return path | An output has a stable reference and source context. Returning a selected result to Home does not discard its originating Work. | Revise a report in a focused page, return the selected version, then reopen its exact source message. |
| P0 | Bounded execution resources | A run declares scarce resources it needs. Conflicting actions serialize or fail visibly without blocking unrelated work. | Two contexts contend for one browser surface while a third reads a file; only the conflicting browser actions wait. |
| P0 | Resume and failure handling | Restart recovers authoritative state and makes in-flight uncertainty explicit. Retrying is a deliberate transition, not a new silent task. | Restart after tool dispatch but before receipt; do not label the operation successful merely because the turn has disappeared. |
| P1 | Context and memory revision | Context assembly records selected source versions and distinguishes observations, durable preferences and derived summaries. | Correct a durable assumption; a later local activation uses the revision while retaining the evidence that explains the change. |
| P1 | Worker coordination | A handoff names its receiver, owned deliverable, source references and reply destination. Retried delivery has a stable identity. | A worker returns twice after reconnection; the parent accepts one result and keeps the provenance of the other delivery attempt. |
| P1 | Trigger and routine adapters | Recurrence/event detection creates normal causal input, with owner, timezone, scope, budget and deduplication key. | The same webhook is delivered twice; only one work admission occurs, with both arrival receipts inspectable. |
| P1 | Skill lifecycle | Capture, review, version, test and activate methods independently from when they are scheduled. | A demonstrated method changes; its routine either uses a pinned version or visibly adopts the new one. |
| P1 | Attention and notification policy | Product notifications derive from meaningful result or input-needed events, with deduplication and acknowledgement. | Ten tool events and one user question produce an activity record plus one actionable notification, not eleven interruptions. |
| P1 | Environment lifecycle and auth handoff | Runtime resources have readiness, ownership, takeover and migration states. Persistent files do not imply valid credentials. | A browser login expires after machine recovery; pause only dependent actions and retain the work's remaining plan. |
| P1 | Usage accounting and trace | Account for model/tool cost against the same causal work, including retries and delegated work. | A budget stop leaves a restartable partial result and a specific reason; it does not fabricate completion. |

## 5. Design the difficult transitions first

### Steering a live action

A message such as “use the revised customer list” changes the work's intended input. It should not simply append text that the model may or may not see. Persist a new input revision, deliver it to the current run at its supported boundary, and mark any not-yet-executed proposal derived from obsolete inputs as needing reconsideration. An effect already committed remains in the receipt log. This uses existing control and action records; it does not require cancelling all background computation whenever a message arrives.

### Waiting without losing the whole context

Waiting is a typed reason, not a generic dead state. A Work can wait for a human answer while another context continues. A particular browser surface can be unavailable while connectors and file operations remain runnable. A model provider can be disconnected while user input continues to persist. The UI should offer the action that resolves the actual wait, while the scheduler keeps the rest of the context body available.

### Approval resolution without stale execution

Our proposed grant check is `scope + action identity + canonical input digest + authority revision + expiry`. The implementation may reuse current Kernel fields where possible. Updating an allow policy is distinct from resolving a pending concrete action. The exact expiry duration should follow Per Pulse's intended interaction model; Grok's ten-minute background window is evidence of the need for expiry semantics, not a number we must copy.

### Resource ownership without turning contexts into silos

A shared file or browser session can be useful to several contexts, but concurrent mutation needs an explicit rule. Choose an exclusive lease for an interactive surface and optimistic version checks for a document. Lease ownership is operational coordination, not a user isolation claim. A human takeover should invalidate the agent's control lease immediately; a new agent action requires a fresh lease after the handoff.

### Recovery without replaying effects blindly

Keep the states “accepted”, “dispatched”, “receipt observed”, and “outcome reconciled” distinct. A process restart can recover a queue, but cannot by itself prove whether a remote write happened. Prefer provider idempotency keys or a read-back reconciliation. If neither exists, retain uncertainty and ask for the narrow decision needed. This is an implementation decision for dependable work, not a proposed redefinition of Pulse.

## 6. Pulse-specific requirements beyond the reference products

The requirement for Per Pulse is a globally continuous context body with local activation. The reference products establish useful operational ingredients; they do not establish that this broader computation model is implemented in the same form.

A meaningful local activation should therefore retain links to the continuing subject, its focused Engram lineage, the original causal input, current goal/intent revision, active dependencies and the selected evidence. Its output changes that same world: it can add an observation, revise an assumption, complete a subtask, generate another activation, or alter future routing. Home is one foreground projection of this body, not the entire body.

Three acceptance scenarios test this distinction:

1. **Overlapping work:** two focused contexts produce evidence relevant to one ongoing goal; a later activation can use both by explicit references without copying whole conversations into every prompt.
2. **Different timescales:** a foreground interruption changes priority while a slower maintenance or review process retains its ownership and is resumed deliberately.
3. **Accumulated practice:** a corrected procedure and its feedback change subsequent execution, with the old method, new method and results recoverable. Merely retaining a transcript does not satisfy this case.

## 7. What to postpone

Voice, avatars, a public template marketplace, enterprise directory management and parity with every notification preference can wait. Full cloud VM orchestration is also separable from the first local product path. They should attach through established identities, permissions, event streams and environment interfaces later.

Do not postpone visible control of current work, an honest reason when it is waiting, a durable route back to the source, or a reviewable result. Those are the parts that turn the present connected-page interface into an operating harness the user can keep working with.

## 8. Suggested sequence for this sprint

1. Expose the existing run, control and approval contracts through the product service with one authoritative scope mapping.
2. Add a quiet per-page operational projection: what is running, what is waiting, and what input resolves it.
3. Verify interruption, scoped approval and restart behavior with deterministic provider/tool transports.
4. Return one real artifact through the existing source-linked result path.
5. Record remaining adapter gaps separately from components that already exist but are not connected to the product path.

The associated machine-readable file includes source records, claim labels and contract IDs so the engineering roadmap can link a module to both its rationale and its acceptance case.

## 9. Targeted code audit: highest-priority gaps after page controls

Audit base: commit `bde8aa888ed4415b33e3f1f9414cd8ac99e7440b`. Concurrent sprint changes in `src/per_pulse/` were visible during reading; the findings below focus on the inherited effect adapters and runtime. R1 describes the baseline defect; the authorized implementation in section 10 addresses its cooperating-writer case. R2–R5 remain recommendations.

### R1 — Shared resource exclusion is missing at the physical effect boundary

**Existing strengths:** `StructuredWorkspaceToolProvider` validates expected content, captures a checkpoint, uses handle-relative replacement, verifies the result, and retains uncertain outcomes. `HarnessScope` fences world, context, session, turn, owner and epoch. These are valuable controls already present.

**Gap:** `workspace.py` verifies the named target and then calls `os.replace` as separate operations. There is no shared path lock spanning them. Separate session runners can operate on the same physical workspace. The World publication guard is not a resource lock: `RuntimePublicationGate._publication_transaction_guard` releases its condition lock before yielding and tracks the number of admitted operations so revocation can drain them. This is correct for publication lifetime but does not serialize colliding effects. `run_command` intentionally releases that guard while its container runs and mounts the shared workspace read-write.

**Reproduced:** `workspace_race_probe.py` uses a temporary directory and two contexts with the same expected pre-image. It synchronizes both threads immediately after their final named-target checks, then lets A finish before B. Both physical replacements report success; the final contents are B's. This is a synthetic test of the physical adapter seam, not a full Kernel integration test. It demonstrates that repeated digest checks do not provide compare-and-swap semantics by themselves.

**Next implementation:** introduce a shared resource broker keyed by canonical workspace authority and path. Hold an exclusive file lease across final validation, replacement and verification. Arbitrary commands initially need a workspace-wide exclusive lease because their write set is unknown; narrow it only when an adapter declares enforceable resources. Leases must remain separate from the publication gate so cancellation and shutdown can still proceed. Cover restore and user upload paths according to their actual conflict semantics. A process-local lock can solve the present single-process alpha's internal races; it must be documented as coordination among participating writers, not protection against arbitrary external processes.

**Acceptance:** two concurrent mutation requests with the same expected digest produce one success and one conflict; nonoverlapping reads remain usable; cancellation while waiting does not consume an effect; a command cannot overlap a structured mutation in its declared write scope.

### R2 — Native file access is not yet a product artifact delivery contract

**Existing strengths:** `src/pulse_system/product/files.py` has bounded, symlink-resistant file reads and create-only uploads. `routes_product.py` wraps it in authenticated listing/content/download/upload routes. Per Pulse already retains source-message references when selected text returns to Home.

**Gap:** the new `src/per_pulse/app.py` serves its compiled frontend but does not mount the inherited product-file routes. `WorkspaceFiles` accepts UTF-8 text only, up to 1 MiB. Core `CheckpointRecord.artifact_path` describes recovery material, not a user deliverable; `ToolResult.output` is an untyped mapping. A document, PNG or PDF created by a future tool therefore lacks a first-class, source-linked route into the present product.

**Next implementation:** start with a small artifact manifest stored in existing World component state or a native migration: artifact ID, source causal event, action/operation ID when present, stable version/content digest, MIME type, byte count, relative storage handle, display name and lifecycle status. Expose only validated artifact handles through the same local API boundary. Use download disposition and isolated previews for active formats; never mount the entire workspace as static files. Reuse the native text reader for the first slice and add binary handling explicitly.

**Acceptance:** create a report, attach its exact version to a page, revise it, return the selected version to Home, and reopen the earlier version after restart. A content change cannot silently alter an existing immutable artifact reference.

### R3 — Durable usage evidence is not yet a durable admission budget

**Existing strengths:** model rounds persist provider response IDs, actual reported usage and unaccounted usage status in Kernel storage. `ProviderUsageReconciliationManager` can recover missing usage through capability-routed provider lookup. TurnRunner bounds model rounds and streamed output.

**Gap:** `RuntimeManager` creates a new `RuntimeStats` in memory. `RuntimeService` constructs it at startup without restoring a budget ledger. The hourly/daily counters therefore reset with the runtime. Its pulse admission estimate uses historical consumption, with no explicit reservation for concurrent in-flight maximum spend. Kernel accounting and the global admission window are different mechanisms and currently do not form a persisted spending contract.

**Next implementation:** persist budget windows and reservation IDs in the existing durable runtime state, using causal event/turn/round identities to make settlement idempotent. Reserve a bounded estimated maximum before model admission, settle reported usage, and keep an outstanding reserve for usage that remains uncertain. Expose whether a limit is a token quota, estimated billing equivalent, or actual currency amount; existing cache discounts are estimates, not prices.

**Acceptance:** exhaust a configured quota, restart, and observe that admission stays blocked for the same window; reconcile a response twice without double charging; concurrent admissions cannot each claim the same remaining capacity. Keep explicit missing-usage state rather than treating it as zero.

### R4 — A reusable environment/connector broker is still an adapter gap

**Existing strengths:** command execution uses a selected immutable Docker image, no network, no host credentials, bounded resources and explicit action authority. Kernel database and checkpoint state stay outside the writable workspace. This is a real isolation boundary for that adapter.

**Gap:** the Kernel product route does not expose a persistent browser or a connector broker. The repository includes older Pi/MCP/terminal facilities, but the Kernel's selected provider catalog is assembled in `_WorldWorkspace`; those older facilities are not automatically available there. `HookSet` explicitly accepts trusted in-process advisory code and its timeout only stops waiting. It cannot serve as the security boundary for untrusted extension code.

**Next implementation:** add one broker-backed tool provider through the existing ToolProvider/action-authority interface. Keep environment identity, broker generation, session readiness, credential reference and resource lease in the adapter's authority envelope. Start with one narrowly scoped connector or browser operation, then human takeover and expired-auth recovery. Keep credentials and plugin execution out of model-visible workspace state where the adapter supports that separation. Do not reproduce a full cloud control plane before validating one useful end-to-end workflow.

**Acceptance:** revoke the broker generation or take control of its surface while an action is pending; old calls cannot act. A missing login yields a resumable wait reason. A plugin failure does not erase the ongoing Work or grant a shell fallback additional access.

### R5 — Routine input needs a bounded adapter, not another scheduler

**Existing strengths:** the Pulse engine already has durable causal inputs, Center reservations, execution-context admission, local inhibition, and due living-concern reentry. Those are the existing scheduling foundation.

**Gap:** the source tree inspected has no product routine declaration with owner, timezone, recurrence/event matcher, enable state, deduplication identity, budget and last-run projection. A periodic UI feature should not dispatch a direct parallel model loop or bypass the causal ledger.

**Next implementation:** add a declarative trigger adapter that records each accepted firing as ordinary native input, with an idempotent `(trigger_id, scheduled_occurrence_or_source_event)` identity. Persist edits, pause and ownership in the same World. Tie firing admission to R3's budget and reuse current Work scope. Keep event connector authentication distinct from the tools a resulting task may use.

**Acceptance:** duplicate webhook delivery or timer replay across restart admits one input; timezone changes affect future occurrences explicitly; pausing cancels future firings while leaving already completed effects and existing Work intact.

### Recommended implementation order

After the current page-scoped execution/control work: **R1 resource coordination → R2 artifact delivery → R3 durable budgets → R4 one useful broker adapter → R5 routine inputs**. R1 is especially timely when enabling file mutations. R2 turns execution into reviewable user value. R3 should precede unattended recurring work. R4 and R5 then extend the same operating path instead of creating a second one.

## 10. Implemented resource-exclusion slice

The sprint adds a shared, process-local workspace writer lane in `src/pulse_harness/adapters/resources.py`. Providers resolve the same canonical root and filesystem identity to the same resource, even when their Kernel sessions and context IDs differ. The initial scope is the whole workspace because commands have an unknown write set. Ordinary file reads do not acquire the writer lane.

`StructuredWorkspaceToolProvider` holds the lane across mutation authority validation, pre-image checks, checkpoint creation, replacement, post-image checks, and its operation receipt. Restore uses the same lane. `CommandWorkspaceToolProvider` holds it across authority revalidation and the command executor's entire lifetime, including cleanup and its returned receipt. The executor's short publication guard remains limited to process start. The Kernel's final action settlement still occurs in the existing TurnRunner after provider return; no additional authoritative action store was introduced.

Acquisition checks cancellation at most every 50 milliseconds and has a 30-second deadline. A cancelled or timed-out waiter starts no effect. Exceptions release an acquired lane. Restore currently has no cancellation token in its public interface, so its wait is deadline-bounded rather than individually cancellable. Ordinary registry entries use weak references to avoid retaining every temporary workspace indefinitely.

If Docker cleanup cannot establish that its invocation stopped, the executor records that invocation as an explicit workspace quarantine. New participating writes fail with `workspace_resource_unavailable`; reads remain available. The provider's preflight reports `workspace_write_state` and the unresolved-command count. Quarantined resources are retained across provider garbage collection and replacement. The owning executor's successful cleanup removes only the corresponding invocation fence. This does not replay an action or decide its outcome: existing `EffectUnknown` handling leaves the original Kernel action running for RecoveryManager, unless a concurrent control already settled it as uncertain. This quarantine is process-local and is not a persisted cross-process container inventory.

The new `tests/per_pulse/test_workspace_concurrency.py` drives the real Kernel approval and continuation path through explicit local HTTP and command fixtures. Barrier-controlled cases cover stale competing writes, reading while a writer is held, cancelling a queued writer, restore versus mutation, command versus mutation, cancelling a queued command, and cancellation after a command's partial effect. Additional cases exercise unconfirmed cleanup, provider collection/replacement, unrelated workspace progress, and cleanup recovery. The real Docker executor's cleanup branch is exercised with synthetic process/control I/O. These are deterministic integration tests of admission, authority, persistence and physical file effects; they do not claim live model or Docker validation. A planned extended endurance run was not started after the user shortened this phase's deadline.

The guarantee is coordination among participating writers in one host process. It is not an OS filesystem lock, cross-process fencing, per-user sandbox, or a way to stop arbitrary external writers. Existing digest and identity checks still detect many external changes but do not provide atomic compare-and-swap against a hostile concurrent process. The existing runtime database fence remains the separate single-runtime authority boundary.
