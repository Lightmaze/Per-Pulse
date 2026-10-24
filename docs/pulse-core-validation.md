# Local activation validation

The 2026-10-23 checkpoint passed a 12-minute wall-clock run of the actual Runtime, World persistence, causal admission, Kernel attention tools, and streaming provider adapter. The provider was an explicit `httpx.MockTransport` fixture. No external model was contacted.

## Sustained runtime evidence

The test created three independent page histories. Each synthetic turn called `attention.inspect`, retained a thought through `attention.hold`, and then completed. After the three initial user turns settled, the test granted 30 background continuation admissions. The Runtime's own background tick loop drove every activation with the production field and refractory timings; the test did not advance a fake clock or manually call the engine.

| Measurement | Observed value |
| --- | ---: |
| Observation duration | 720.00 seconds |
| Pytest duration | 720.59 seconds |
| World ticks | 6,871 |
| Local contexts | 3 |
| Background activation admissions | 30 |
| Settled results, including initial user turns | 33 |
| HTTP fixture requests, including tool rounds | 99 |
| Quiet time after exhaustion and settlement | 499.84 seconds |

The run passed. Every sampled interval advanced the live tick count. Each provider request contained exactly one context's history marker. The admission count never exceeded 30. Once all 33 turns settled, the grant was disabled with zero remaining, and the request count remained at 99 for the rest of the observation. Sampled thread counts returned to eight after active tool turns and stayed there during the quiet interval.

The admission allowance counts turns, not provider requests: the fixture used three provider rounds per turn, yielding nine initial requests plus 90 background requests. Its repeated retained thought was deliberately scripted to exhaust the allowance; it does not demonstrate useful autonomous reasoning.

The [raw synthetic run record](validation/pulse-soak-2026-10-23.json) includes periodic counters. The test is opt-in so the ordinary suite does not wait 12 minutes. From the repository with its Python dependencies installed:

```bash
PER_PULSE_SOAK_SECONDS=720 \
PER_PULSE_SOAK_REPORT=/tmp/per-pulse-soak.json \
python -m pytest -s -q tests/per_pulse/test_core_acceptance_soak.py
```

## Fault and boundary acceptance

The two focused acceptance files passed 19 tests in 6.88 seconds:

```bash
python -m pytest -q \
  tests/per_pulse/test_core_acceptance.py \
  tests/per_pulse/test_core_acceptance_pulse.py
```

They exercise the native persistence and provider path across these boundaries:

- Offline inputs drain once after reopening; a blocked or failed provider turn leaves sibling contexts usable and preserves pending input in its own context.
- A settled source result excites one connected context and inhibits another without mixing their transcripts. Exhausted allowance prevents further provider calls even with retained drive.
- Pause cancels queued internal activation while preserving explicit user input. Restart retains notes and numeric fields, pauses the grant, and does not reapply settled feedback.
- Admission failure after reservation rolls back the allowance, sequence, pending reference, and causal event together. Feedback failure rolls back its cursor and propagated fields; retry applies the result once.
- Invalid budgets and boolean-as-number inputs leave state unchanged. Disconnected contexts can retain attention but cannot start a grant.
- A long clock gap contributes only the permitted live elapsed time. Failed autonomous turns clear pending state, damp drive, raise inhibition, and do not create successful feedback or immediately repeat.

Staged model action precedence and failure are additionally tested in `test_native_pulse_bridge.py`. Derived native Work provenance, shared allowance, and retry behavior are tested separately in `test_native_pulse_budget.py`; the sustained run above covers local attention tool loops, not derived Work chains. The [phase checkpoint](pulse-core.md) records the complete regression, browser, and build checks.

These checks establish bounded execution and persistence behavior under synthetic transport. Live-model usefulness, long-term stability beyond this observation, parameter tuning, and learned connection selection remain outside this evidence.
