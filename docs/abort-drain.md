# Retain stores until admitted cancellation callers leave

A native cancellation callback can still be constructing its return value after
the cancelled turn's model worker has detached. Closing the Kernel store when
only the turn worker has left can therefore make the callback's final read fail
with `HarnessStore is closed`. This was observed in a failed process-recovery
experiment and reproduced with the native World, Kernel and shutdown coordinator.

`KernelHarnessRuntime` now admits abort callers under its existing condition
lock. Shutdown seals new admission, signals active runners, waits for admitted
abort callers to return, and then closes subordinate resources and the Kernel.
The caller count is released in `finally`, including when cancellation raises.
A late engine abort after shutdown owns cancellation returns without entering a
closing journal. The Runtime coordinator still owns its existing deadline and
keeps the owner lease until physical drain; this change adds no store, runtime,
replay, forced lease release, or longer user-facing shutdown deadline.

The regression pauses the actual cancellation caller before its final store
read, releases the physical model call, and lets the shutdown deadline expire.
It verifies that the store remains readable, a second owner cannot acquire the
same World, the lease releases only after the callback returns, the original
deadline report stays unchanged, and reopening preserves the World without
repeating the request. The test fails on the prior implementation.

Validation on the candidate based on `63d6c6c7`:

- 40 related execution, concurrency, pre-create cancellation and shutdown checks
  passed; the complete Python suite passed 706 tests with eight Docker-only
  skips. The added immutable-report assertion was also checked afterward.
- A real Chromium journey approved and downloaded a file, retained a draft
  while a native provider call was held, and sent SIGTERM to the server. With
  the cancellation callback held, shutdown returned its original incomplete
  report and a premature successor was refused without changing the lease epoch.
  After release, the owner drained and the original report remained unchanged.
- A successor reopened the same Work with its uncertain input, no late answer
  or model replay, the exact previous artifact and the draft. A separately
  requested and approved file then completed and downloaded exactly. Desktop
  1280px and mobile 390px screenshots were inspected, with no page errors or
  horizontal overflow.

The private browser fixture retains its process after Uvicorn's graceful ASGI
shutdown so a late daemon callback can be observed. The first attempt used
Uvicorn's default signal re-raising and ended before that observation; its
failure was preserved. Only scheduling and model replies are synthetic. The
native timers, SQLite, lease acquisition, approvals, files and browser execute
for real. Private World history and screenshots are not committed. Optional
`stream_cognition` was not installed in this validation environment.

Primary documentation read during the investigation:
[Python 3.12 threading](https://docs.python.org/3.12/library/threading.html).
`join(timeout)` does not itself prove exit; condition waits release and reacquire
the lock, and a notified waiter must recheck its predicate. These facts helped
review the lifetime invariant after the native reproduction. They are not
evidence of independent model learning, and this bounded fix does not close all
shutdown or process-recovery cases tracked in issue #32.
