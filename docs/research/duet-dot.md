# Duet and dot: harness contracts for Per-Pulse

Research date: 2026-10-23. Target: `Lightmaze/Per-Pulse`.

The useful finding is a set of operational contracts: an ongoing responsibility can outlive a conversation turn; a user can redirect work while execution continues; a waiting decision does not freeze unrelated work; and returned output, accepted work, and delivered artifacts are different facts. These contracts matter more to the core harness than copying a chat interface.

This report uses public first-party documentation. Product statements establish behavior. Engineering documents supply architectural references. Proposed Per-Pulse modules and schemas are inferences, not descriptions of closed-source implementations.

## Product identity and visible objects

Duet is **Anthropic Claude Duet**, announced on October 21, 2026. It is not Google's older similarly named product. dot is **OpenAI ChatGPT dots**.

| Public object | Claude Duet | ChatGPT dot | Per-Pulse contract inferred from the behavior |
|---|---|---|---|
| Ongoing interaction | Main thread, with optional topic channels | Same dot across supported messaging channels | Stable agent/responsibility identity; distinct channel history |
| Assigned work | Tasks view; Make holds editable outputs | Background agents and separate visible work threads | Work registry, parent-child links, output registry |
| Completion | Agent can suggest completion; user marks a task completed | Completed run does not establish achieved/delivered result | Separate execution, acceptance, and delivery |
| Intervention | Task decisions can wait while remaining work proceeds | New instructions can redirect ongoing work | Durable steering inbox and branch-local waiting |
| Environment | Own cloud computer/browser | Cloud computer plus explicitly connected local computer | Explicit environment binding and availability |
| Observation | Periodic connected-account checks | Restricted proactive research plus assigned recurring work | Separate observations from authorized actions |

Sources: [Duet announcement](https://claude.com/resources/articles/claude-duet), [Duet help](https://support.claude.com/en/articles/17631495-get-started-with-claude-duet), [dot overview](https://learn.chatgpt.com/docs/dots), [dot tasks](https://learn.chatgpt.com/docs/dots/tasks-and-memory). Duet's help page currently says users cannot inspect or take over its browser. dot documents a takeover/return flow. The products therefore do not expose identical environment controls.

Repository adaptation: the inspected Per-Pulse product layer maps a page to an existing native TaskFront under the continuity subject, and its execution adapter derives the original HarnessScope. This is the right reuse boundary. The modules below describe responsibilities that existing layers must satisfy; they do not call for new standalone services, a second session database, or a new agent identity for every page. Confirmed file results can likewise be a read-only projection of native action/change evidence.

## Lifecycle and intervention

dot distinguishes pausing its main task, stopping a delegated task, and cancelling a recurring schedule. These operations do not undo completed external effects. Its delegated tasks remain attached to their original computer or cloud environment; selecting a different default computer does not migrate existing work. [Controls](https://learn.chatgpt.com/docs/dots/controls), [tasks](https://learn.chatgpt.com/docs/dots/tasks-and-memory).

**Proposed Per-Pulse contract.** A user message should be committed before dispatch and given an idempotency key. A running worker should observe a new instruction revision at a documented safe point. Cancellation should identify its scope. A late worker result can be retained as evidence but must not overwrite a newer instruction revision or revive cancelled work. A branch waiting for input should retain a machine-readable wait reason while siblings remain eligible to run.

This does not require preempting arbitrary tools mid-syscall. A bounded, observable steering delay is a legitimate first implementation. A UI that says an edit was applied while a worker continues with stale input is not.

The [OpenAI Agents API session contract](https://developers.openai.com/api/docs/guides/agents-api/sessions) is a useful public analogue: input sent to an active session steers its turn; input to an idle session starts another turn; logical submissions retain idempotency keys. It is not evidence that dot uses precisely that API internally.

## Context selection and compaction

dot documents task-specific context selection rather than inheriting every conversation. It distinguishes conversation context, relevant ChatGPT memory, and its own saved notes. Voice interactions and background tasks may receive different selections. [Tasks and memory](https://learn.chatgpt.com/docs/dots/tasks-and-memory).

The strongest public infrastructure reference is Anthropic's [Managed Agents engineering article](https://www.anthropic.com/research/managed-agents): a durable event session, a model/tool harness, and execution sandboxes are separate components. Historical events remain queryable outside the current model context; the harness can transform a selected view. Duet explicitly cites this system's credential design, not its entire architecture.

**Proposed Per-Pulse contract.** Keep authoritative events and source artifacts separately from generated summaries. Each context pack should record selected source IDs, instruction revision, memory revisions, capability snapshot, and a budget estimate. Compaction creates a new derived view; it must not become deletion of the only source record. An explicit user deletion request is a different operation and must remain able to remove information from retrieval.

Do not introduce a heavyweight context compiler merely to match a competitor name. Start by making the existing context construction inspectable, bounded, and correct after steering. Neither product document establishes an exact token-packing policy, compaction threshold, embedding design, or memory database.

## Capability and permission boundaries

dot's proactive research tools are restricted to reading and private findings; they cannot directly send messages, modify connected apps, or control a computer. Normal assigned work has its own authorization. Duet documents action review and credential separation. [dot controls](https://learn.chatgpt.com/docs/dots/controls), [Duet announcement](https://claude.com/resources/articles/claude-duet).

**Proposed Per-Pulse contract.** Represent capability scope as data, checked at execution, rather than a prompt promise. Installed, connected, enabled, and authorized are separate states. A selected skill does not grant additional tool privileges. Background discovery can propose actions, but the task supervisor decides whether an action is already authorized or needs a concrete user decision.

An approval should bind the exact proposed operation, target, content revision, and expiry. If any changes, re-evaluate. Existing explicit authorization should be reusable within its scope: cloning Duet's visible approval UI must not cause Per-Pulse to ask repeatedly for reversible actions the user already authorized.

The [OpenAI Agents API architecture](https://developers.openai.com/api/docs/guides/agents-api/architecture) also separates harness, environment, and application server. An environment is optional; tool-only reasoning should not wait for a VM. The [multi-agent API](https://developers.openai.com/api/docs/guides/agents-api/multi-agent) documents shared filesystems: child-agent creation alone is not an isolation boundary. These are design references, not private dot implementation claims.

## Memory editing, deletion, and revocation

The current [Claude memory documentation](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context) uses editable individual topics. Changes apply to subsequent conversations. Deleting a source chat does not automatically delete derived memory entries; topic deletion is separate. The same page includes legacy-summary behavior, which must not be confused with the current topic model. Duet uses the account's search setting and loses deleted chats from search. [Duet help](https://support.claude.com/en/articles/17631495-get-started-with-claude-duet).

OpenAI's [dot administration guide](https://learn.chatgpt.com/docs/enterprise/dots-admin-guide) says disabling ChatGPT memory sharing or disconnecting a plugin does not erase information the dot has already retained. Deleting the dot removes its own context; separately stored files and Work/Codex conversations require their own deletion paths.

**Proposed Per-Pulse contract.** Expose distinct operations:

- Revoke future source access.
- Edit or forget a derived memory.
- Delete a source conversation/artifact.
- Delete the agent's own state.

Return a deletion receipt that names affected stores and remaining references. For a small initial system, one backing store and a clear receipt are preferable to claiming global erasure across unimplemented connectors. Tombstones should prevent a stale context pack or delayed worker from silently restoring forgotten memory. Do not promise immutable content logs and complete erasure simultaneously; retain non-content operational metadata where needed, and make the policy explicit.

## Skills and upgrade boundaries

Duet carries over account skills; this is capability reuse, not evidence of autonomous learning or self-modifying policy. The broader [Claude skills documentation](https://support.claude.com/en/articles/12512180-use-skills-in-claude) distinguishes next-use updates for directly shared skills from organization-published updates that retain an approved version pending review. This describes the surrounding Claude ecosystem; Duet-specific hot-reload timing is not documented.

The [OpenAI Agents API configuration contract](https://developers.openai.com/api/docs/guides/agents-api/configuration) separately illustrates configuration snapshots: saved-agent updates affect new sessions; active turns retain their settings. This is an architectural reference, not evidence about dot skill synchronization.

**Proposed Per-Pulse contract.** Record each skill's identity, content digest/version, source, and enabled state in the work's capability snapshot. Resolve upgrades at an explicit boundary, such as the next activation. Existing work can finish on a recorded version unless revoked. A security revocation should stop further use immediately; a routine improvement should not mutate a running tool's inputs. Start with versions and provenance; a marketplace, organization review workflow, and automatic skill optimization can follow actual demand.

## Module priority and a concrete acceptance scenario

| Priority | Module | Initial acceptance condition |
|---|---|---|
| Required | Responsibility/work registry | Unfinished work survives local activation boundaries without being replaced by a new chat |
| Required | Durable event/steering inbox | Retried input has one logical effect; in-flight guidance reaches the intended worker |
| Required | Supervisor and scoped lifecycle | One waiting/cancelled branch does not freeze or revive siblings |
| Required | Context assembly and source provenance | Revised instructions and memories appear in the next pack with traceable sources |
| Required | Tool/capability execution | Unsupported or unauthorized operations produce explicit outcomes |
| Required | Environment binding | Existing work never silently jumps to another environment |
| Required | Artifact/acceptance/delivery records | Tool return is not misrepresented as completed delivery |
| Required | Budgeted trigger handling | Duplicate or bursty triggers do not create unbounded work |
| Required | User-visible progress and notification outbox | Important decisions are visible; repeated observations do not flood the user |
| Important | Memory revision/deletion | A forgotten item is excluded from future retrieval and stale-worker writes |
| Important | Skill versioning | Every result identifies the skill version actually used |
| Important | Replay and resource accounting | A failed or stale result can be traced to its input, worker, environment, and cost |
| Later | Voice, avatars, rich remote desktop, marketplace scale | Added after the operational scenario works |

Acceptance scenario: create one responsibility with two work branches. Change its constraints while execution is active. Block one branch on a user decision and finish the other. Save an editable artifact with provenance. Restart the executor, then continue from the corrected context and existing pending decision. Deliver one consolidated, relevant update. Verify that repeated input, stale worker return, missing file, or notification failure cannot falsely close the responsibility.

The scenario tests continuous responsibility and evolving context. Restart recovery is one part of it, not the definition of Pulse continuity.

## Review limits

No closed-source code, private prompts, or live Duet/dot account workflows were inspected. Database choices, exact agents, compaction algorithms, and scheduler internals remain unknown. The report's engineering prescriptions should be checked against Per-Pulse's existing code before adding new abstractions. The companion evidence JSON separates official statements from proposed requirements.
