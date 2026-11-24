# Command recovery after a host process dies

An approved container can outlive the Python process that started it. A World
lease and the process-local workspace write lane do not, by themselves, stop
that container. The native command adapter therefore separates creating a
container from starting it, and binds its name to the existing Kernel action
authority before approval. After creation and before starting, its immutable ID
is recorded as the existing action's Kernel operation ID. The World still owns
admission, execution and history.

The Docker CLI uses the local daemon, the selected immutable image, no network,
a read-only root filesystem and the same bounded writable workspace. No new
session store, container scheduler or authority database is introduced.

## Recovery behavior

After acquiring the World lease and the existing Kernel database lifetime lock,
the successor reconciles uncertain command actions before admitting workspace
writes. It checks the exact container identity, image, protocol labels and
workspace mount, follows the immutable operation identity even if the container
was renamed, removes that container by ID, and confirms absence
through a successful Docker query. This runs even when `P_PULSE_COMMAND_IMAGE`
is no longer set. A missing image is not needed to remove its old container.

Confirmed exit is recorded in the existing Kernel recovery record. The original
action remains `uncertain`; its previous effects are not declared successful,
reverted or repeated. This confirmation survives subsequent restarts without
requiring Docker to remain installed. The user can stop the recovered turn and
continue in the same Work with a new request and approval.

If Docker is unavailable, removal cannot be confirmed, or a container's identity
differs, participating workspace writes remain blocked. Reads and unrelated
workspaces remain available. Restore Docker access and restart the World to retry
the exact reconciliation; a repeated attempt does not rerun the old command.
Commands from the older combined `docker run` protocol lack a durable container
identity and remain blocked for operator investigation. They are not guessed
from a broad container listing, and no unrelated container is pruned.

## Why creation and starting are separate

Docker documents [`create`](https://docs.docker.com/reference/cli/docker/container/create/)
as creating a container without starting it. The adapter uses a separate
[`start --attach`](https://docs.docker.com/reference/cli/docker/container/start/)
request addressed by the returned immutable ID, after checking native ownership
again. The container's inspected exit state supplies the application exit code;
CLI connection loss cannot stand in for a completed command.

If the old Python owner dies while a create request is delayed, that request can
only leave an inert container. It cannot issue the subsequent guarded start.
If a start was already issued, its container ID already existed and is removed
by reconciliation. The lifetime lock prevents an older live Python owner from
issuing another start after a successor enters the same Kernel. A late inert
container can remain as a stopped Docker resource; the old action never grants
permission to start it again.

This is a native lifecycle boundary, not a filesystem lock against arbitrary
external host programs or a claim that every container is a virtual machine.
The ordinary workspace writer lane continues to coordinate participating writers
within the active process.

## Evidence and reproduction

The original real-Docker experiment killed the host after native approval,
waited for its normal 30-second lease to expire, and approved a successor file
write. The old container then overwrote that successful write. The corrected
experiment removed the old container before the successor write, observed past
the old command's planned write time, and retained the old action's uncertainty.

The focused regression uses real Docker, actual OS process death, native
approvals and SQLite, a configured five-second lease and real wall-clock expiry.
It continues in the original Work with commands disabled on the successor. A
second case delays the actual Docker create CLI until after the old Python
process dies and the successor writes; the delayed container remains `created`
and produces no file effect. Another case renames the running container before
reentry; recovery still finds and removes the recorded ID. The CLI delay is an explicit test barrier; the
Docker daemon and its effects are real. Model choices use a local TEST_DOUBLE.

```sh
docker pull python:3.12-slim
PER_PULSE_DOCKER_IMAGE=python:3.12-slim \
  python -m pytest tests/per_pulse/test_command_docker_sigkill.py -q
python -m pytest tests/per_pulse/test_command_recovery.py \
  tests/per_pulse/test_workspace_concurrency.py -q
```

The real-Docker tests skip when the image variable is absent; the separate CI
job supplies it explicitly. The ordinary tests cover unavailable control,
unconfirmed removal, mismatched image/mount/protocol, legacy identity, retry,
unrelated workspaces, retained uncertainty and confirmed exit after Docker is
removed. Passing these tests makes no claim about model quality or learning.
