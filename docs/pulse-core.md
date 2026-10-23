# Local pulses in one continuing context body

Per Pulse keeps Home and Work pages as local contexts of the same subject.
The pulse core lets a completed local computation contribute to later activity
without requiring another user message. It uses the existing World, causal
ledger, scheduler, Dendrite and Harness Kernel; pages do not become separate
agents, and the pulse core does not own a second conversation store.

Three events form the basic path:

1. A model turn settles in a local context. Its assistant result and causal
   source become durable evidence of that context's activity.
2. A later local opportunity can reopen that context's existing history. A
   directed connection can also carry the settled output to another context.
3. The receiving context computes with its own history and the selected
   incoming material. Its result remains part of the same context body.

The world tick is a coordination clock. It does not manufacture a new task at
every interval. Eligibility depends on retained local activity, the explicit
connection graph, inhibition, refractory time and the shared background quota.

## Separate histories, explicit influence

Each Work page retains its native TaskFront and ActivityCenter IDs. Kernel
sessions remain scoped by context, while Home and Work keep the same continuity
Engram. A pulse reentering a page uses that page's original model history. It
does not prepend a generic instruction telling the model to invent more work.

An excitatory edge delivers the source's settled result. It does not copy the
source page's whole transcript, tool authority or browser navigation state.
The delivered event points to its source result in the causal ledger. A
navigation reference and a computational connection are distinct: opening an
ordinary page link does not by itself enable autonomous diffusion.

When distinct local outputs reach the same target within its Dendrite window,
the native convergence mechanism can combine them into one receiving turn.
The original deliveries remain individually attributable to their source
results. The page shows those source-linked inputs and the receiving answer;
it does not show the internal aggregate again as a duplicate message.
The aggregate retains the member event IDs, source edge identities and frozen
window policy. Its depth follows the contributing paths, and it consumes no
extra background admission beyond the original deliveries. Removing a
contributing edge cancels an unstarted fused turn while preserving that
formation evidence.

An inhibitory edge changes a decaying numeric trace on its target context.
That trace influences later background eligibility and survives restart. A
trace of at least `1.0` defers background admission until it decays below that
level; lower traces reduce reentry probability. It does not write a semantic
instruction into the target transcript. Foreground
user input remains available when a background quota is exhausted.

## Configuration and API

Background computation is opt-in.

Start the ordinary local workspace with `per-pulse --pulse`. Set its background
admission ceiling with `--pulse-budget 12`; the default is twelve admissions in
a rolling hour. The normal command without `--pulse` keeps background
computation disabled.

Python callers can configure the local rhythm directly:

```python
from per_pulse.app import create_app
from per_pulse.service import PulseSettings

app = create_app(
    "./data/my-workspace",
    pulse=PulseSettings(
        enabled=True,
        check_interval_seconds=10,
        spontaneous_rate=0.1,
        refractory_seconds=60,
        max_background_pulses_per_hour=12,
        max_propagation_depth=2,
        propagation_threshold=0.3,
    ),
)
```

Use the ordinary provider configuration from the [README](../README.md).
Offline workspaces save input without calling a model. Example workspaces
remain authored examples and never consume model calls, even when a pulse
profile or provider credentials are supplied.

| Endpoint | Meaning |
| --- | --- |
| `GET /api/pulse` | Read running state, background quota, local contexts, connections and recent activation outcomes. |
| `POST /api/pulse/connections` | Connect two pages with an excitatory or inhibitory edge. |
| `DELETE /api/pulse/connections` | Remove the directed edge between two pages. |

Create a directed computational connection with the native page IDs returned
by `/api/bootstrap`:

```json
{
  "sourcePageId": "the-source-page-id",
  "targetPageId": "the-target-page-id",
  "weight": 0.6,
  "kind": "excitatory"
}
```

Use `"home"` for Home. To remove the edge, send its `sourcePageId` and
`targetPageId` to the DELETE endpoint as JSON. The connection API uses the same
loopback, same-origin and JSON request boundaries as the existing page API.

Repeating the same connection is idempotent. Changing its weight or kind
creates a new edge identity that applies to subsequently settled results.
Removing or changing an edge cancels its queued, unstarted deliveries; a model
turn already running may finish normally. Recreating an edge does not revive
cancelled deliveries or retroactively relay old results.

## Bounds and recovery

The background quota is charged when an autonomous input is admitted. Retrying
the coordinator or reopening the workspace cannot refill that quota or create
a second copy of an already admitted event. Propagation depth limits connected
cycles. One eligible context has at most one pending local opportunity, so a
slow provider cannot accumulate an unbounded timer-generated backlog.

Disabling the pulse profile pauses its queued work. Reenabling it resumes an
eligible admitted delivery under the same event identity. Refractory time
defers background turns in recently active contexts; it does not suppress user
input. A saturated background budget does not stop the observation of newly
settled foreground activity.

Successful results are the source of later influence. Failed or uncertain
model turns do not create an invented result or establish a new continuation
source. The existing Kernel retains model failures, cancellation and recovery
evidence. Normal user messages continue through the existing foreground path.
`recentActivations` in `/api/pulse` exposes queued, settled, failed, uncertain
and cancelled background events, including reentries that contain no new text.

Committed subject succession retains local context IDs, connection identities,
numeric inhibition and the rolling quota. Prior settled results remain
immutable evidence in the predecessor's identity; new eligible work belongs
to the active successor in the same committed lineage. Queued fused work keeps
its event identity and original formation record during handoff. Recovery can
resume a partially admitted fanout without replaying its already settled
predecessor deliveries. An uncommitted or unrelated identity cannot use that
lineage evidence as a continuation source.

The quota counts background pulse admissions. It is not a currency limit or a
hard token ceiling for a provider: a Kernel turn may contain more than one
model/tool round, and provider token usage is learned after the request. The
inherited Runtime token budget is an estimate, separate from the durable
background admission quota. Workspace mutation permissions remain unchanged.

## Verification

Run the native integration and dynamics checks with:

```sh
python -m pytest tests/per_pulse
```

The integration fixtures inject a local HTTP transport into the normal model
adapter. They exercise real World and Kernel persistence, distinct context
histories, a shared continuity subject, causal result propagation, bounded
reentry, multi-source convergence, inhibitory gating, pause/revocation, interrupted
checkpoints, restart and visible provider failure. They make no external provider
requests and incur no paid model validation. These checks establish runtime
behavior, including fusion and committed succession; the value of
model-generated background work still needs to be measured with the intended
provider and real ongoing projects.

### Local scaling probe

`tests/per_pulse/benchmark_pulse_core.py` creates a temporary native SQLite
body with 100 contexts, 200 directed edges and 10,000 historical causal events.
It uses no model or network calls and prints source hashes, query plans and
timings. Reproduce both profiles with:

```sh
PYTHONPATH=src python tests/per_pulse/benchmark_pulse_core.py --rate 0
PYTHONPATH=src python tests/per_pulse/benchmark_pulse_core.py --rate 0.1
```

On the development host with Python 3.12.14 and SQLite 3.53.1, the index and
graph snapshot changes produced these local measurements on 2026-10-23:

| Operation | Before | After |
| --- | ---: | ---: |
| Cold projection of 10,000 events, total | 16,279 ms | 635 ms |
| Status snapshot, median of 10 | 138.8 ms | 1.08 ms |
| Scheduled check with reentry rate 0, median of 10 | 689.4 ms | 2.06 ms |
| SQL statements per status snapshot | 204 | 4 |
| SQL statements per rate 0 scheduled check | 911 | 11 |

At rate `0.1`, the first eligible check started with an empty admission budget,
admitted twelve opportunities and took 30.2 ms. Later checks were quota-bound
and took a median 2.16 ms; that smaller number does not measure an unsaturated
candidate pass. Snapshot median was 1.01 ms. The quota and result-proof queries
used the new indexes. Schema version 8 adds indexes only; the upgrade smoke
test verifies retained causal rows, the version 7 backup and idempotent reopen.
These synthetic timings describe the coordinator on this host, not provider
latency, model quality or a service-level guarantee.
