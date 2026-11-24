# Unicode source search

`reading.find` can opt into full Unicode casefold matching with `casefold=true`.
This lets a Work locate `Straße` with `STRASSE`, then use the ordinary find
receipt and match index to read the original passage through `reading.follow`.
The existing default remains Python `re.IGNORECASE` on escaped literal text.
`case_sensitive=true` selects exact case. Combining it with `casefold=true`
is rejected, as are non-boolean mode values.

The receipt reports `case_mode`: `regex_ignorecase`, `case_sensitive`, or
`unicode_casefold`. Source identity, Work ownership, redaction, bounded excerpts,
result limits, cancellation, and follow/continue checks retain their existing
contracts. No new cursor database, file mutation or model provider is added.

## Match semantics and coordinates

A casefold match means that an entire original substring's `str.casefold()`
equals the query's casefold. Matches are greedy and non-overlapping in original
source order. Both folded endpoints must be original character boundaries:

| Source | Query | Original match `[start, end)` |
| --- | --- | --- |
| `ß Straße` | `STRASSE` | `[2, 8)` |
| `🌍 Straße` | `STRASSE` | `[2, 8)` |
| `sß` | `ss` | `[1, 2)` |
| `Aß` | `as` | none: the end would split `ß` |
| `ﬃ` | `FFI` | `[0, 1)` |
| `ﬃ` | `fi` | none: the start would split `ﬃ` |
| `İx` | `i` + U+0307 + `x` | `[0, 2)` |
| `ıx` | `IX` | none under default Unicode casefold |
| `cafe` + U+0301 | `café` | none: normalization is not performed |

Coordinates remain Python Unicode character indexes in the **redacted view**.
They are neither UTF-8 byte offsets nor JavaScript UTF-16 offsets. Full file
bytes and the redacted view are independently digested. Hidden bytes changing
still invalidate follow, even if the visible match stays identical. The query
must still contain 2–120 characters, and at most eight matches are exposed.

The implementation folds the already-redacted source and uses a compact
cumulative array of original character boundaries. ASCII sources need no map.
A miss returns before building one. Invalid partial-expansion hits advance one
folded position: advancing a full query length would miss `ß` in `sß`/`ss`.

## Observed development experiment

The change followed a source-reading experiment using Python's
[Unicode HOWTO](https://docs.python.org/3.12/howto/unicode.html),
[`str.casefold`](https://docs.python.org/3.12/library/stdtypes.html#str.casefold)
and [`re.IGNORECASE`](https://docs.python.org/3.12/library/re.html#re.IGNORECASE).
Predictions were recorded before running a prototype. Initial Python/Node
probes found different matching semantics and character units, **not an
existing original-string regex offset bug**.

In a private native Work, the prototype was created through an approved file
action, then run through approved Docker commands on 2026-10-29. It agreed with
an independent oracle enumerating original substrings on 42,120 exhaustive
short-string pairs, 5,000 seeded random pairs and 12 explicit examples under
Unicode 15.0.0. Deliberately using a folded offset or skipping invalid hits by
the query length failed on the expected counterexamples. A full codepoint scan
on that runtime observed casefold lengths from one to three characters.

A separate run used prebuilt inputs of 999,990–999,999 UTF-8 bytes. Medians of
three untraced prototype timings and separately measured incremental peaks:

| Case | Median | Incremental traced peak |
| --- | --- | --- |
| ASCII hits | 79.4 ms | 5.10 MB |
| Expansion hits | 70.5 ms | 9.80 MB |
| Repeated invalid partial-ligature hits | 381.7 ms | 5.00 MB |
| Supplementary-character miss | 64.0 ms | 10.67 MB |

These are one container's **prototype** measurements, not production latency
guarantees, total application memory, or a model benchmark. Production adds
cancellation checks and avoids the map for ASCII sources or absent folded
queries. The native model/expert choices were explicitly host-assistant-mediated
TEST_DOUBLEs; Docker execution, files, SQLite and native receipts were real.
No independent provider learning or quality improvement is established.

Automated regression uses a separate original-substring oracle plus native
find/follow/reopen cases, mode distinctions, redaction and result bounds.
