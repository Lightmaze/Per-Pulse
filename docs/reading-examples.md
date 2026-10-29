# Carry an observed reading passage into later work

An assistant can now keep a useful passage from native `reading` as a concrete
example. Previously, `posture.remember_example` accepted assistant messages;
reading material had to be repeated in a reply before it could become an example.
`posture.remember_reading` retains the observed text directly, with its source
version, while using the existing experience journal and sharing controls.

```json
{
  "op": "remember_reading",
  "receipt_id": "<receipt from an exposed reading passage>",
  "title": "A useful concrete example",
  "excerpt": "<an exact substring of that passage>",
  "note": "<optional interpretation to try in another situation>"
}
```

The receipt must belong to the current Work's committed native history. A read
earlier in the same turn is allowed, as is a read before a committed generation
change. Search matches, another Work's receipt, unseen text, and paraphrases are
not passage evidence. The selecting turn must finish successfully before the
example enters memory. Preparation alone does not admit it.

The saved example starts local. The existing experience editor can explicitly
make it available to other pages. A receiving page gets the excerpt, its note,
and the fact that it came from reading. It does not receive the surrounding
passage, file path, digest, raw receipt, or private encounter. Opening its source
uses the existing navigation back to the reading conversation, preserving drafts.
Existing action approval still applies when a later Work uses the example to
write a file.

## What the source means

The source identifies an observation, not verified authorship, current file
contents, or a successful outcome. The selected text remains available after
the original file changes or disappears. No reread happens during retention.
The source page shows that distinction, its recorded digest, and observation
time. The interface can change language without translating the excerpt.

The selected range is the first exact occurrence in the exposed receipt's
content. Start is inclusive and end exclusive, measured in Unicode code points.
The source file digest and reading position are retained separately. The
selection also records a SHA-256 of its exact UTF-8 bytes.

An example's note is a revisable interpretation. Material text does not acquire
instruction authority. Saving it adds no feedback or success rating. A selection
made by an explicit fixture provider is labeled `constructed_fixture`, even
though the reading action really executed.

## Expedition: a position needs its observed representation

The external learning source was the W3C
[Web Annotation Data Model](https://www.w3.org/TR/annotation-model/), especially
[text position selectors](https://www.w3.org/TR/annotation-model/#text-position-selector)
and [states](https://www.w3.org/TR/annotation-model/#states).
Its separation between a selected region and resource state prompted a concrete
implementation correction: adding a passage-relative offset to a file offset
would misidentify text when credential redaction changes the displayed length.
Per Pulse therefore binds selection coordinates to the exact exposed receipt,
and retains source version information separately. This is an engineering
application of the model, not an implementation of the W3C vocabulary.

The counterexample uses a synthetic credential before a selected phrase. The
test checks that the selected coordinates recover the phrase from the redacted
receipt, differ from the original file offset, and never retain the credential.
Other trials cover combining characters, emoji, exact whitespace, source
deletion, denied cross-Work access, failed selecting turns, generation changes,
and a cold image save/restore.

The browser trial carries a short attributed W3C passage through actual native
reading, retention, explicit sharing, denial before sharing, a later native file
action with approval, and restart. It checks desktop English and mobile Chinese
views. The model's choices are scripted test transport; the trial establishes
the executable product path, not real-provider judgment or improved learning
quality. A configured provider is still needed for that separate evaluation.

Two failures improved the trial: successful conversation settlement precedes
asynchronous experience admission, so the test must wait for the actual memory;
and the user command itself names the output file, so file-action completion
must wait for the assistant's settled result instead of a substring in any
message. Neither failure justified weakening product admission or approval.

## Run the focused checks

```sh
python -m pytest -q tests/per_pulse/test_reading_examples.py
npm --prefix web run build
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
  PER_PULSE_PYTHON=python \
  PER_PULSE_READING_SCREENSHOTS=/tmp/per-pulse-reading \
  node tests/browser/reading_examples_flow.mjs
```

The browser script uses temporary synthetic workspaces and the existing local
server. It never needs a paid provider or private user history. CI runs it after
the existing posture browser acceptance and retains its screenshots.
