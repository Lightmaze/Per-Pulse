# Observe a running workspace over time

To record an already-running local Per Pulse instance over one hour without
creating new tasks, model calls, or another scheduler, use a **separate terminal**:

```sh
python -m per_pulse.pulse_observer --url http://127.0.0.1:8000 \
  --duration-seconds 3600 --interval-seconds 10 \
  --output ./private-pulse-observation.jsonl
```

This read-only observer polls native `/api/pulse/observation` (one response
binds the persistent World, current service instance and Pulse status),
retains one World identity, records queue/budget snapshots and changed local
activation IDs with source links, and writes a private, create-only JSONL file
(mode 0600 on POSIX). Interrupted runs have no completed success footer unless
the observer catches the interruption; a caught interruption is marked
`interrupted`. Poll failures and a full twenty-event server window are
explicit evidence gaps, never silently counted as successful endurance.
The observed event count is a **lower bound**, not an exhaustive ledger read.
The summary separates settled background activations from **navigable Work results**:
only a settled local event with an exact receiving message verified by the
native page lineage counts as a navigable result, grouped by receiving Work.
Events without such a message are still recorded as activity, never promoted to
completed user-visible work. The report also distinguishes sampled running and
paused states; neither an accessible message nor a running process proves that
the result was useful, learned from, or delivered by a real model.
The native bootstrap exposes an ephemeral `serviceInstanceId`: when a running
service restarts under the same persistent World identity, the observer ends
with `service_restarted` instead of claiming uninterrupted process activity.
The Context Body and its history can remain persistent across that restart.

This command does not enable Pulse or a provider for you: start Per Pulse
normally, with `--pulse` and any explicitly authorized model, before observing.
A connected-provider status is not proof that a real provider ran, that
background activity improved a result, or that currency was capped. For a
long-horizon claim, inspect the actual file, elapsed wall time, native sources,
provider request/usage evidence, and whether relevant Works yielded useful
new outcomes; do not use a short synthetic CI run as that evidence.
