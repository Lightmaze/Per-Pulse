# Per Pulse harness audit — 2026-10-23

Audit target: `Lightmaze/Per-Pulse`, source commit `bde8aa888ed4415b33e3f1f9414cd8ac99e7440b` (`feat/english-session-pages`). The older `Lightmaze/P-Pulse` is source heritage, not this product's current entry point. Its imported snapshot is `20208de1fef6bd946cf1cad62e96782e6a9a0dc0`, as recorded in `docs/source-lineage.md`.

The product already has a substantial native execution engine. Its first English interface exposes only a small part of it. The immediate task is to connect the real execution and environment capabilities to the page experience while preserving the existing World, causal history and Kernel. Rebuilding another agent loop would discard the strongest part of the current code.

## The real path at the audited commit

| Stage | Existing implementation and contract | Product exposure at the audited commit |
| --- | --- | --- |
| User entry | `per_pulse/app.py:create_app`, `PageService.send` admit actual user input through `RuntimeService.inject` or `send_task_front_message`. | Home and Work messages, queued offline input, references, page creation. |
| Context and ownership | `PageService.create_page` uses one continuing subject; the page ID is a native TaskFront ID. `RuntimeService._native_kernel_context_for_center` resolves its `work:<center_id>` execution context. | Separate page histories, links, breadcrumbs, and selected user returns. |
| Background dispatch | `RuntimeService.start` runs the inherited tick loop. The native scheduler and per-context execution already permit concurrent Home/Work. `KernelHarnessRuntime.run` binds each admitted World turn to its exact Kernel scope. | Configured provider starts the loop. The UI polls only the currently visible queued/working page. |
| Model and tools | `TurnRunner.run`, `_drive_model`, `resume`, `_execute_action`; `ChatHTTPModelAdapter`; native `_WorldWorkspace.descriptors` and `execute_for_scope`. | Real HTTP provider transport. No synthetic success fallback. Tools are inherited, but workspace mutation is hard-disabled by this entry point. |
| Approval and cancellation | Durable action/approval identities in `HarnessStore`; `_WorldKernelService.resolve_approval` checks exact active World attachment; `TurnRunner.abort` fences late replies and uncertain effects. | No API or UI route in the initial English page layer. A long-running call has no page Stop control. |
| Artifact effects | `StructuredWorkspaceToolProvider` supports digest-guarded UTF-8 changes with checkpoints. Optional command adapter runs a bounded container. | No native materials/upload/download/approval console in the English interface. A text result is shown as conversation text. |
| Cross-context work | `native_work.py` has create/send/inspect/report with successful-source settlement and durable outbox dispatch. `native_team.py` reuses actual persistent collaborators and histories. | Source is present. Mutating operations are disallowed by the new read-only entry point; UI-created pages still work through explicit user APIs. |
| Feedback and memory | History read/search, selected Work returns, scoped collaborator feedback, native local histories, and optional cognition exist in inherited source. | Page links are navigation metadata. The selected Return Home action is explicitly user-supplied text with source references; it does not impersonate an automatic assistant report. |
| Restart and recovery | Existing World and Kernel stores, generation lineage, ownership leases, immutable action outcomes, uncertainty and cold Pulse Image machinery. | The new entry point reopens the same stores and resumes legitimate queued input when configured. It does not provide a recovery management interface. |

The inherited native catalog includes workspace reads, active reading, workspace index, history tools, local controls, team/work composition and optional Life/cognition tools. It does not by itself provide a native browser implementation or a public dynamic connector/skill manager. MCP and terminal-related source elsewhere in the tree are not evidence that the English product has those capabilities wired into its native model tool catalog.

## Findings that materially change the implementation plan

1. **Execution state must come from the Kernel, not inferred from message prose.** `PageService._status` originally considered only causal-message states. A World turn remains running while the Kernel waits for approval, so the page can show Working although the next action requires the user. Kernel action and turn rows are already authoritative and durable.
2. **A page ID is not a Kernel context ID.** The adapter must resolve the native center, then derive the stable scope. Accepting browser-supplied session/owner/context fields would create a second authority path. The new adapter accepts only page ID plus an exact existing turn/action ID.
3. **Stopping the whole subject would break the product's concurrency.** `KernelHarnessRuntime.abort(engram_id)` stops all that subject's active contexts. A page stop must call the native Kernel control for one exact turn. Home and neighboring Works must continue.
4. **An old approval is not permission for a replacement execution.** The native approval guard requires the original attached World turn. After a missing attachment or restart, the product may expose the old action and allow a safe stop; it must not approve and silently replay it under a new worker.
5. **Approval resolution can continue a model call.** Holding a global product lock while approving would prevent other page controls and even a stop request. Duplicate controls may share an identity-specific lock; unrelated controls and cancellation must remain independent.
6. **A completed reply is not a completed Work.** Existing native source carefully distinguishes response settlement, selected report delivery and overall work status. The English interface should retain that distinction when adding notifications or a work inbox.
7. **Read-only mode was a deliberate first interface boundary.** Workspace actions should be an explicit configuration choice with exact-action approvals. Enabling them changes native Work/team/local mutation availability too; a capability view should state the actual selected profile rather than calling the app generally connected.

## Implementation advanced in this sprint

`src/per_pulse/execution.py` adds `PageExecution(runtime)`, a projection/control adapter over the existing Kernel:

- `snapshot(page_id)` returns bounded original execution state, pending approvals and eligibility without creating/waking a session or making a model call.
- `cancel(page_id, turn_id)` stops only that exact turn. Retrying an already cancelled turn is observation; naming a previously completed turn never selects a newer one.
- `resolve_approval(page_id, action_id, decision)` checks original page ownership and delegates to native authority. A duplicate identical decision returns its original receipt without calling resume again. A changed decision conflicts.
- Approval details are bounded and redacted; incomplete details cannot be used to grant Allow once. Missing physical attachment and expired authority stay visible as unavailable, not as an actionable new grant.
- Current recovery eligibility is separate from retained historical uncertainty. An old cancelled/uncertain provider round cannot permanently label a new successful turn as needing Stop.
- Locks are scoped to a single approval/turn identity and retired after callers finish. An approval's model continuation cannot prevent a Stop operation or an unrelated page control.
- A committed approval now reports the durable action status separately from continuation failure. A successful file effect followed by a redaction failure returns `accepted: true`, `actionStatus: "succeeded"`, `actionCompleted: true`, and a safe `continuationError`; it never tells the user that the already-applied change was rejected. The redaction rule remains unchanged.
- Native `HarnessKernelService.control` can cancel a recovered exact turn without constructing any model, tool provider, reviewer or advisor. It uses the canonical abort state machine with a temporary control-only runner, a read-only model-round permit, no cached working runner, and cleanup in `finally`. Existing live runners remain the cancellation target when present.

The adapter does not define a new store, model loop, job scheduler, permission system or agent identity. It does not retract effects already performed. Cancelling an admitted turn does not erase later queued user inputs; the UI must say **Stop this turn** until queue cancellation is implemented separately.

## Prioritized remaining modules

| Priority | Module | What should be built on the existing path | Acceptance example |
| --- | --- | --- | --- |
| P0 | Execution visibility and controls | Integrate the new page adapter into API/UI; refresh busy neighboring pages and incoming Home reports. | Work waits for a real file approval, Home answers independently, the user stops or approves only the intended Work, reload retains the same authority. |
| P0 | Workspace material delivery | Reuse the existing workspace boundary for bounded uploads, inspection and downloads; bind a returned artifact to page, action and file version. | A user uploads a source, approves a computation/write, opens the actual output and returns its source-bearing result. |
| P0 | Input and work control | Distinguish next input, queued input cancellation, immediate stop and a revised instruction. Preserve actual user event identity. | An instruction sent mid-run has a visible pending state; stopping turn A cannot accidentally run or erase instruction B. |
| P0 | Real provider acceptance | Run the same path with an explicitly selected provider/budget. Exercise tool protocol, cancellation and normal multi-turn use. | One real task produces an inspectable artifact with usage records, ordinary restart and no fixture responses. |
| P1 | Persistent browser environment | Native browser adapter with per-world/profile identity, page observation, action receipts, account handoff and explicit network/effect authority. | A Work returns to a previously opened page/session while Home remains usable; tab IDs and action receipts cannot cross the wrong environment. |
| P1 | Budget and admission | Existing token evidence is durable; add explicit aggregate monetary/round/concurrency envelopes and visible exhaustion behavior. | Background Work cannot silently consume an unbounded allowance; exhaustion returns a usable resumable state. |
| P1 | Artifact catalog and versions | Extend actual workspace effects, not a second storage system. Retain source/action identity and distinguish drafts from delivered versions. | A user can tell which output belongs to which Work and which revision was reviewed. |
| P1 | Selective attention and durable inbox | Build on native Work reports, updates and exact turn states; separate input-needed from ordinary unread results. | A pending approval remains discoverable; a routine result can wait without repeatedly interrupting Home. |
| P1 | Tools, connectors and skills | Discover selected runtime capabilities, enable/install explicitly, expose tool failures, and preserve action authority through adapters. | A newly selected capability appears in the actual model catalog and runs with its documented environment and permissions. |
| P1 | User-visible memory correction | Surface retained source history, scoped corrections and withdrawal using inherited mechanisms. | Revising a remembered preference changes future scoped reads while preserving the old source and the correction event. |
| P1 | Schedules and external events | Route admitted schedules and connector events into the same World; preserve ownership, finite allowance, cancellation and attribution. | A condition triggers the correct existing Work and reports its outcome without forging a fresh human request. |
| P2 | Cloud hosting and accounts | Environment lifecycle, authentication, credentials, multi-device inbox and data isolation. | Reconnecting to the same workspace restores files, context and controllable work without sharing another user's authority. |

The P0/P1 order is a product judgment grounded in the code path. It does not imply that the referenced closed-source products use these precise internal classes or databases.

## Verification performed by this audit agent

Command (from this sprint's Per-Pulse checkout):

```sh
PYTHONPATH=src python -m pytest tests/per_pulse/test_execution.py -q
```

Result: **21 passed in 11.32s** after the final offline-control and truthful-receipt changes. These tests use the real World, SQLite, native Kernel, HTTP adapter and actual file effects, with explicitly scripted model replies. They cover observation without runner creation, exact approval and duplicate receipt, Home concurrency, cross-page rejection, denied/cancelled effects, in-flight cancellation and late replies, stale completed turns, restart readback, missing original attachment and cancellation while approval continuation is still waiting. Further cases cover normal restart while an approval is pending, a native turn deadline, preservation of a queued later instruction, later success despite retained historical uncertainty, explicit rejection of inherited internal mutation tools in the read-only profile, two offline crash-window controls followed by real reconnection, an applied effect whose continuation fails redaction, and artifact/Home observation while a writer waits for the resource lane.

The first run exposed a test synchronization race: the action becomes approval-pending just before the turn switches to waiting-approval. Tests now wait for the adapter's actual `canResolve` eligibility instead of treating action presence alone as permission. No production timeout or authority check was relaxed.

Parent integration remains responsible for the complete API/UI flow, browser checks, whole scoped regression, persistence and delivery receipt. No live-provider result is claimed here.

## Recovery and control boundary verified in this sprint

The earlier offline-control gap is fixed in the native Kernel, without direct ad hoc SQLite status edits. Tests persist unresolved turns through real Kernel transitions, reopen the real PageService with `model_factory=None`, and forbid all model/tool/reviewer/advisor factories. Stopping an old pending approval produces CANCELLED; stopping a recovered STARTING action retains UNCERTAIN. Both leave physical files untouched, make no model call, preserve logical session/scope, close the temporary runner, and leave no cached working runner. Reopening with a configured HTTP model fixture then processes the ordinary queued input in a new successful turn and delivers its World assistant response. The earlier uncertain action remains uncertain.

The resource-lane cross-check confirms that a waiting approved writer can be stopped while another caller holds the shared workspace lane. During that wait, an already-confirmed artifact can still be read over HTTP and Home can still be inspected. Artifact observation never obtains the write lane.

Native cancellation settles the turn before it finishes terminalizing all its action rows. A polling snapshot can briefly show a stopped turn with an unavailable pending action. No approval is eligible then; product copy must not recommend Stop when `canCancel` is false.

Read-only enforcement was exercised with attempted `work.create`, `team.establish` and `local.set_excitability` from valid native scopes. All failed before changing Works/participants/local tendency. The current `work` and `team` descriptions nevertheless advertise disabled mutations; filtering their read-only schemas/descriptions is a discoverability improvement, not a missing enforcement boundary.
